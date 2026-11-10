"""Category → actual facets → filters → sorted candidates, with bounded evidence-led recovery.

Python controls API calls and rejects known conflicts. Models cannot invent category/filter IDs.
Each attempt records its choices and rejected requirements; recovery never drops hard constraints.
"""

import json

from parts_app import progress, tme
from parts_app.agents import CategoryChoice, FilterPlan, SearchPlan, ask
from parts_app.requirements import (
    derived_footprint_options,
    explicit_property_checks,
    requested_value,
    requirement_checks,
    scalar,
)


def category_options(discovery: dict, catalogue: list[dict]) -> list[dict]:
    """Prefer leaf categories while retaining actual names from the full supplier catalogue."""
    counts = {
        int(c["id"]): c["products_count"]
        for c in tme.nested(discovery, "counters", "categories", "elements")
    }
    parents = {c.get("parent_id") for c in catalogue if c["id"] in counts}
    return [
        {"id": c["id"], "name": c["name"], "count": counts[c["id"]]}
        for c in catalogue
        if c["id"] in counts and c["id"] not in parents
    ]


def facet_evidence(facets: list[dict], fields: dict) -> list[dict]:
    """Bound huge numeric facets, prioritizing literal and SI-equivalent BOM values."""
    literals = {tme.norm(str(v)) for v in fields.values() if str(v).strip()}
    numbers = {scalar(str(v)) for v in fields.values()} - {None}
    result = []
    used = 0
    for facet in facets:
        values = [str(v["value"]) for v in facet.get("values", []) if v.get("is_active", True)]
        values.sort(key=lambda v: (tme.norm(v) not in literals and scalar(v) not in numbers, v))
        item = {
            "parameter": facet["name"],
            "values": values[:40],
            "total_values": len(values),
            "truncated": len(values) > 40,
        }
        size = len(json.dumps(item).encode())
        if used + size <= 13000:
            result.append(item)
            used += size
    return result


def resolve_filters(facets: list[dict], selected: FilterPlan, fields: dict, mappings: dict):
    """Resolve actual labels and force known package filters even if the model omits them."""
    required = tme.common_exact_constraints(derived_footprint_options(fields, mappings))
    groups = {item.parameter: item.values for item in selected.filters}
    # Missing facets are checked again against downloaded product parameters.
    available = {tme.norm(p["name"]) for p in facets}
    for name, value in required:
        if tme.norm(name) in available:
            groups[name] = [value]
    numeric = requested_value(fields)
    for facet in facets:
        name = facet["name"]
        values = [str(v["value"]) for v in facet.get("values", [])]
        if numeric and name.lower() == numeric[0]:
            allowed = [v for v in values if scalar(v) == numeric[1]]
            if not allowed:
                raise ValueError(f"Required {numeric[0]} has no exact value in this category.")
            groups[name] = allowed
        checks = explicit_property_checks(fields, {"parameters": [facet]})
        relevant = [c for c in checks if c["outcome"] != "unknown"]
        if relevant:
            allowed = []
            for value in values:
                single = facet | {"values": [{"value": value}]}
                checks = explicit_property_checks(fields, {"parameters": [single]})
                if all(
                    c["outcome"] == "pass"
                    for c in checks
                    if c["requirement"] in {r["requirement"] for r in relevant}
                ):
                    allowed.append(value)
            if not allowed:
                raise ValueError(f"Required BOM properties have no compatible {name} filter.")
            groups[name] = allowed
    filters = []
    applied = []
    for index, (name, values) in enumerate(groups.items()):
        matching = [p for p in facets if tme.norm(p["name"]) == tme.norm(name)]
        if len(matching) != 1:
            raise ValueError(f"TME filter parameter is missing or ambiguous: {name!r}.")
        facet = matching[0]
        ids = []
        for value in values:
            matches = [
                v for v in facet.get("values", []) if tme.norm(str(v["value"])) == tme.norm(value)
            ]
            if not matches:
                raise ValueError(f"TME filter value is not available: {name}={value!r}.")
            ids.extend(str(v["id"]) for v in matches)
        filters.append((f"parameters[{index}][id]", str(facet["id"])))
        filters.extend((f"parameters[{index}][values][]", value) for value in ids)
        applied.append({"parameter": name, "values": values})
    return filters, applied


def find_candidates(fields: dict, plan: SearchPlan, mappings: dict, charge) -> tuple[dict, list]:
    """Try at most three adaptive category/filter searches under the existing spend guard."""
    from parts_app.procedures import TME_LOCK, access

    history = []
    phrases = list(plan.phrases)
    with TME_LOCK:
        token = access()
        catalogue = tme.nested(
            tme.get("/products/categories/list", token, [("country", "PL")]), "data", "elements"
        )
    for attempt in range(3):
        phrase = phrases[min(attempt, len(phrases) - 1)]
        progress.step(f"Finding TME category ({attempt + 1}/3): {phrase}")
        with TME_LOCK:
            token = access()
            discovery = tme.search_catalogue(phrase, token)
        categories = category_options(discovery, catalogue)
        record = {"query": phrase, "candidates": 0, "coverage": "up to three sorted pages"}
        history.append(record)
        if not categories:
            record["reason"] = "No matching categories; retrying corrected phrase."
            continue
        category = ask(
            CategoryChoice,
            "Select the most specific actual category for this BOM, using only a "
            "supplied category_id. Consider mounting and function. If previous "
            "attempts failed choose a corrected category or retry_phrase. "
            "Never relax BOM requirements during recovery.",
            {
                "bom": fields,
                "categories": [[c["id"], c["name"]] for c in categories],
                "previous_attempts": history[:-1],
            },
            charge=charge,
        )
        record["category_choice"] = category.model_dump()
        if category.category_id is None:
            if category.retry_phrase:
                phrases.insert(attempt + 1, category.retry_phrase)
            continue
        if category.category_id not in {c["id"] for c in categories}:
            raise ValueError("Agent selected a category not returned by TME.")
        with TME_LOCK:
            scoped = tme.search_catalogue(phrase, token, category_id=category.category_id)
        facets = tme.nested(scoped, "parameters", "elements")
        progress.step("Choosing filters from TME category properties")
        selected = ask(
            FilterPlan,
            "Select filters from actual parameter names and values for every "
            "specified BOM property supported here. Use Case - inch for chip size "
            "(LEDs included), mounting, value, technology, type, polarity, pitch, "
            "pin count and specified ratings. Do not invent unstated requirements. "
            "Multiple values within one filter are OR; different filters are AND. "
            "Use numeric unit equivalence, never approximate nominal values. "
            "Only explicit minimums allow higher ratings. If category/phrase is "
            "wrong return retry_phrase and explain. Missing facets stay unknown. "
            "Use prior failures to correct filters without relaxing requirements.",
            {
                "bom": fields,
                "footprint_options": derived_footprint_options(fields, mappings),
                "category": category.model_dump(),
                "facets": facet_evidence(facets, fields),
                "previous_attempts": history[:-1],
            },
            charge=charge,
        )
        record["filter_plan"] = selected.model_dump()
        if selected.retry_phrase:
            phrases.insert(attempt + 1, selected.retry_phrase)
            continue
        try:
            filters, applied = resolve_filters(facets, selected, fields, mappings)
        except ValueError as error:
            record["reason"] = str(error)
            continue
        record["filters"] = applied
        progress.step("Loading sorted, filtered TME results")
        with TME_LOCK:
            found = tme.search(
                phrase,
                fields["Footprint"],
                int(fields["Final Item Count"]),
                token,
                options=derived_footprint_options(fields, mappings),
                category_id=category.category_id,
                filters=filters,
            )
        eligible, rejected = eligible_candidates(fields, found, mappings)
        record.update(candidates=len(found), eligible=len(eligible), rejected=rejected[:5])
        if eligible:
            return eligible, history
        record["reason"] = "No stocked/priced compatible results; correct category or filters."
    return {}, history


def eligible_candidates(fields: dict, found: list[dict], mappings: dict) -> tuple[dict, list]:
    """Retain uncertainty but reject every explicit conflict before commercial ranking."""
    eligible = {}
    rejected = []
    for candidate in found:
        failures = [
            c for c in requirement_checks(fields, candidate, mappings) if c["outcome"] == "fail"
        ]
        if failures:
            rejected.append({"symbol": candidate["symbol"], "conflicts": failures})
        else:
            eligible[candidate["symbol"]] = candidate
    return eligible, rejected
