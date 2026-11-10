"""Shared BOM constraints for selection and validation; missing evidence stays unknown.

Documented footprint options are OR alternatives containing AND constraints. Derived chip codes
and mounting supplement each option. Scalar comparisons never treat ranges as nominal values.
"""

import re
from decimal import Decimal, InvalidOperation

PREFIXES = {
    "p": "1e-12",
    "n": "1e-9",
    "u": "1e-6",
    "µ": "1e-6",
    "m": "1e-3",
    "k": "1e3",
    "K": "1e3",
    "M": "1e6",
    "": "1",
    "R": "1",
}


def magnitude(text: str) -> Decimal | None:
    """Parse a leading decimal with SI prefix, preserving milli/mega distinction."""
    text = text.strip().replace(",", ".").replace("μ", "µ").replace("Ω", "ohm")
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([pnuµmkKMR]?)", text)
    if not match:
        return None
    number, prefix = match.groups()
    try:
        return Decimal(number) * Decimal(PREFIXES[prefix])
    except InvalidOperation:
        return None


def requested_value(fields: dict) -> tuple[str, Decimal] | None:
    """Find only explicitly numeric R/C/L or Zener-voltage requirements."""
    value = fields.get("Value", "").strip()
    if "zener" in value.lower():
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*V", value)
        return ("zener voltage", Decimal(match[1].replace(",", "."))) if match else None
    if re.search(r"\d\s*(?:-|\.\.|÷|to)\s*\d", value):
        return None
    reference = fields.get("Reference", "").split(",")[0].strip()
    category = next(
        (
            name
            for prefix, name in [("R", "resistance"), ("C", "capacitance"), ("L", "inductance")]
            if re.fullmatch(prefix + r"\d+", reference)
        ),
        None,
    )
    if category is None:
        return None
    number = magnitude(value)
    return (category, number) if number is not None else None


def compare_value(fields: dict, product: dict) -> dict:
    """Return a measured value comparison without promoting absent data to a match."""
    request = requested_value(fields)
    if request is None:
        return {"outcome": "unknown", "reason": "No supported explicit numeric requirement."}
    category, expected = request
    for parameter in product.get("parameters", []):
        name = parameter.get("name", "").lower()
        if name == category or (
            category == "zener voltage" and "zener" in name and "voltage" in name
        ):
            values = parameter.get("values", [])
            actual = scalar(str(values[0].get("value", ""))) if values else None
            if actual is not None:
                delta = abs(actual - expected) / max(abs(expected), Decimal("1e-18"))
                return {
                    "outcome": "pass" if actual == expected else "fail",
                    "requirement": category,
                    "expected": str(expected),
                    "actual": str(actual),
                    "relative_difference": float(delta),
                }
    return {"outcome": "unknown", "reason": f"TME does not expose {category}."}


def shortlist_candidates(fields: dict, candidates: list[dict]) -> list[dict]:
    """Prioritize exact values over cheap unrelated results before the model sees a shortlist."""
    annotated = []
    for product in candidates:
        check = compare_value(fields, product)
        annotated.append(product | {"numeric_requirement": check})
    return sorted(
        annotated,
        key=lambda product: (
            {"pass": 0, "unknown": 2, "fail": 3}[product["numeric_requirement"]["outcome"]],
            product["numeric_requirement"].get("relative_difference", 0),
            not product.get("footprint_match", False),
            product.get("line_total_pln", 0),
            product["symbol"],
        ),
    )


def footprint_conflicts(fields: dict, product: dict) -> list[str]:
    """Reject explicit mounting/chip-case conflicts, retaining candidates with missing data."""
    check = footprint_check(fields, product, {})
    return [check["evidence"]] if check["outcome"] == "fail" else []


def derived_footprint_options(fields: dict, options: dict) -> dict:
    """Add unambiguous KiCad chip codes and mounting to every documented alternative."""
    footprint = fields.get("Footprint", "")
    constraints = []
    chip = re.search(r"_(\d{4})_(\d{4})Metric(?:_|$)", footprint)
    if chip:
        constraints.extend(
            [
                ("exact", "Case - inch", chip[1]),
                ("exact", "Case - mm", chip[2]),
            ]
        )
    if "_SMD:" in footprint or "_SMD_" in footprint:
        constraints.append(("exact", "Mounting", "SMD"))
    elif "_THT:" in footprint or "_THT_" in footprint:
        constraints.append(("exact", "Mounting", "THT"))
    if not options and not constraints:
        return {}
    return {
        name: list(dict.fromkeys([*option, *constraints]))
        for name, option in (options or {"derived": []}).items()
    }


def constraint_result(parameters: list[dict], constraint: tuple) -> tuple[str, str]:
    """Compare one geometry constraint using only populated API parameters."""
    from parts_app import tme

    kind, name, expected = constraint
    values = []
    for parameter in parameters:
        if tme.norm(parameter.get("name", "")) == tme.norm(name):
            values.extend(parameter.get("values", []))
    if name == "Mounting":
        values = [
            v | {"value": "SMD" if v.get("value") == "SMT" else v.get("value")} for v in values
        ]
        expected = "SMD" if expected == "SMT" else expected
    if not values:
        outcome = "unknown"
    elif tme.constraint_matches({"values": values}, kind, expected):
        outcome = "pass"
    else:
        outcome = "fail"
    actual = [v.get("value") for v in values]
    return outcome, f"{name}: required {expected}; API {actual or 'missing'} ({outcome})"


def footprint_option_result(parameters: list[dict], option: list) -> tuple[str, list]:
    """Check an AND option; one supported chip code can cover an absent equivalent code."""
    results = [(constraint[1], constraint_result(parameters, constraint)) for constraint in option]
    outcomes = []
    for name, (outcome, _) in results:
        equivalent = {"Case - inch": "Case - mm", "Case - mm": "Case - inch"}.get(name)
        if outcome == "unknown" and any(
            key == equivalent and result[0] == "pass" for key, result in results
        ):
            outcome = "pass"
        outcomes.append(outcome)
    if "fail" in outcomes:
        outcome = "fail"
    elif not outcomes or "unknown" in outcomes:
        outcome = "unknown"
    else:
        outcome = "pass"
    return outcome, [detail for _, (_, detail) in results]


def footprint_check(fields: dict, product: dict, options: dict) -> dict:
    """Distinguish contradicted constraints from absent evidence across OR alternatives."""
    options = derived_footprint_options(fields, options)
    alternatives = []
    details = []
    for option in options.values():
        outcome, evidence = footprint_option_result(product.get("parameters", []), option)
        alternatives.append(outcome)
        details.extend(evidence)
    if "pass" in alternatives:
        outcome = "pass"
    elif alternatives and all(state == "fail" for state in alternatives):
        outcome = "fail"
    else:
        outcome = "unknown"
    return {
        "category": "footprint",
        "outcome": outcome,
        "requirement": fields.get("Footprint", "Footprint compatibility"),
        "evidence": "; ".join(dict.fromkeys(details)) or "No verified geometry mapping.",
        "sources": ["bom.Footprint", "product.parameters"],
    }


# Only explicit BOM fields become requirements. Numeric fields keep their stated comparison.
PROPERTY_NAMES = {
    "voltage": ("ratings", ["Voltage", "Rated voltage", "Operating voltage"]),
    "rated voltage": ("ratings", ["Rated voltage", "Operating voltage", "Voltage"]),
    "power": ("ratings", ["Power", "Rated power"]),
    "current": ("ratings", ["Current", "Operating current", "LED current"]),
    "tolerance": ("ratings", ["Tolerance"]),
    "led colour": ("technology", ["LED colour"]),
    "color": ("technology", ["LED colour", "Colour"]),
    "colour": ("technology", ["LED colour", "Colour"]),
    "technology": ("technology", ["Technology"]),
    "dielectric": ("technology", ["Dielectric"]),
    "pinout": ("polarity_pinout", ["Pinout", "Pin configuration"]),
    "number of contacts": ("polarity_pinout", ["Number of contacts"]),
    "type of diode": ("type", ["Type of diode"]),
    "type of capacitor": ("type", ["Type of capacitor"]),
    "type of resistor": ("type", ["Type of resistor"]),
    "type of transistor": ("type", ["Type of transistor"]),
    "case": ("footprint", ["Case"]),
    "mounting": ("footprint", ["Mounting"]),
    "case - inch": ("footprint", ["Case - inch"]),
    "case - mm": ("footprint", ["Case - mm"]),
    "terminal pitch": ("footprint", ["Terminal pitch"]),
    "number of pins": ("polarity_pinout", ["Number of pins", "Number of terminals"]),
    "resistance": ("value", ["Resistance"]),
    "capacitance": ("value", ["Capacitance"]),
    "inductance": ("value", ["Inductance"]),
}


def scalar(text: str) -> Decimal | None:
    """Parse a scalar only; ranges, conditions and ambiguous suffixes remain unknown."""
    text = text.replace(" ", "")
    if not re.fullmatch(
        r"\s*\d+(?:[.,]\d+)?\s*[pnuµμmkKMR]?(?:V(?:DC|AC)?|A|W|F|H|Ω|ohm|mm|%)?\s*", text
    ):
        return None
    # mm is a unit, not the milli prefix of an unnamed unit.
    return magnitude(text.removesuffix("mm").strip())


def explicit_property_checks(fields: dict, product: dict) -> list[dict]:
    """Compare supported explicit fields, never infer requirements from supplier data."""
    from parts_app import tme

    checks = []
    for key, expected in fields.items():
        label = key.strip().lower()
        minimum = label.startswith("minimum ")
        label = label.removeprefix("minimum ")
        if label not in PROPERTY_NAMES or not str(expected).strip():
            continue
        category, names = PROPERTY_NAMES[label]
        parameters = [p for p in product.get("parameters", []) if p.get("name") in names]
        values = [str(v.get("value", "")) for p in parameters for v in p.get("values", [])]
        expected_number = scalar(str(expected))
        states = []
        for value in values:
            number = scalar(value)
            if (
                expected_number is not None
                and number is not None
                and label not in {"case - inch", "case - mm"}
            ):
                states.append(number >= expected_number if minimum else number == expected_number)
            elif expected_number is None or label in {"case - inch", "case - mm"}:
                actual_text, expected_text = tme.norm(value), tme.norm(str(expected))
                if label == "mounting":
                    actual_text = "smd" if actual_text == "smt" else actual_text
                    expected_text = "smd" if expected_text == "smt" else expected_text
                states.append(actual_text == expected_text)
        outcome = "unknown" if not states else "pass" if any(states) else "fail"
        checks.append(
            {
                "category": category,
                "requirement": f"{key}: {expected}",
                "outcome": outcome,
                "evidence": f"BOM {expected}; API {values or 'missing'}",
                "sources": ["bom", "product.parameters"],
            }
        )
    return checks


def requirement_checks(fields: dict, product: dict, options: dict) -> list[dict]:
    """Return shared application checks used by selection and independent validation."""
    checks = [footprint_check(fields, product, options), *explicit_property_checks(fields, product)]
    numeric = compare_value(fields, product)
    if requested_value(fields) is not None:
        checks.append(
            {
                "category": "value",
                "requirement": "Exact BOM numeric value",
                "outcome": numeric["outcome"],
                "evidence": str(numeric),
                "sources": ["bom.Value", "product.parameters"],
            }
        )
    elif re.search(r"\d\s*(?:-|\.\.|÷|to)\s*\d", fields.get("Value", "")):
        checks.append(
            {
                "category": "value",
                "requirement": "BOM value range",
                "outcome": "unknown",
                "evidence": "Range endpoints and any required adjustability need verification.",
                "sources": ["bom.Value"],
            }
        )
    elif fields.get("Value", "").strip().lower() in {
        "r",
        "c",
        "l",
        "resistor",
        "capacitor",
        "inductor",
    } and not any(c["category"] == "value" for c in checks):
        checks.append(
            {
                "category": "value",
                "requirement": "Required nominal value",
                "outcome": "unknown",
                "evidence": "BOM nominal value is unspecified.",
                "sources": ["bom.Value"],
            }
        )
    return checks
