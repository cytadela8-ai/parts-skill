"""Bounded per-row search/validation, with API evidence independent of agent conclusions."""

import json
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import create_model

from parts_app import progress, tme
from parts_app.agents import Choice, SearchPlan, Validation, ask
from parts_app.requirements import compare_value, footprint_conflicts, shortlist_candidates

TME_LOCK = threading.Lock()


def access():
    """Refresh a bearer token for each short API phase without exposing credentials."""
    return tme.bearer(*tme.credentials(Path(".env")))


def fetch(symbol: str, quantity: int, *, allow_unpriced: bool = False) -> dict:
    with TME_LOCK:
        return tme.load_selected_candidate(
            symbol, quantity, access(), None, allow_unpriced=allow_unpriced
        )


def search_row(row: dict, charge) -> tuple[str, dict, str]:
    progress.step("Planning TME search")
    plan = ask(
        SearchPlan,
        "Plan 1–3 TME phrases, each 2–40 characters. The second/third "
        "are corrected recovery searches. State requirements and unknowns.",
        {"bom": bom_fields(row["fields"])},
        charge=charge,
    )
    candidates = {}
    history = []
    mappings = tme.load_footprint_options(
        Path(__file__).with_name("footprint-translations.csv"), row["fields"]["Footprint"]
    )
    quantity = int(row["fields"]["Final Item Count"])
    progress.step("Connecting to TME")
    progress.activity("Waiting for supplier request slot")
    with TME_LOCK:
        token = access()
        for index, phrase in enumerate(plan.phrases, 1):
            progress.step(f"Searching TME {index}/{len(plan.phrases)}: {phrase}")
            found = tme.search(
                phrase,
                row["fields"]["Footprint"],
                quantity,
                token,
                limit=100,
                options=mappings or None,
            )
            history.append(
                {"query": phrase, "candidates": len(found), "coverage": "up to three pages"}
            )
            for candidate in found:
                if not footprint_conflicts(row["fields"], candidate):
                    candidates[candidate["symbol"]] = candidate
    progress.step(f"Comparing {len(candidates)} eligible TME candidates")
    shortlist = []
    for candidate in shortlist_candidates(row["fields"], list(candidates.values())):
        if len(json.dumps(shortlist + [candidate]).encode()) > 16000:
            break
        shortlist.append(candidate)
        if len(shortlist) == 10:
            break
    if not candidates:
        return "", {"queries": history, "requirements": plan.model_dump()}, "needs_review"
    if not shortlist:
        raise ValueError("TME candidate exceeds context limit; narrow the search.")
    allowed = tuple([str(index) for index in range(len(shortlist))] + [None])
    choice_type = create_model(
        "CandidateChoice",
        __base__=Choice,
        candidate_id=(Literal[allowed], ...),  # ty: ignore[invalid-type-form]  # Dynamic enum.
    )
    progress.step(f"Agent selecting from {len(shortlist)} shortlisted candidates")
    choice = ask(
        choice_type,
        "Choose one candidate_id from the supplied numbered candidates, or null. "
        "Do not transcribe or invent a part number. You may choose a tentative component "
        "even with uncertain compatibility: explain all doubts for separate validation. "
        "Do not hide likely issues or invent matches. When the footprint is "
        "unmapped, propose footprint_constraints from actual returned parameter "
        "labels and values only. Verify case, mounting and geometry against the "
        "KiCad footprint; never use height for capacitors. Explain footprint "
        "uncertainty in footprint_comment. Reject known incompatible footprints;"
        "if footprint evidence is missing you may propose but must state it is uncertain.",
        {
            "bom": bom_fields(row["fields"]),
            "requirements": plan.model_dump(),
            "candidates": [
                agent_product(item) | {"candidate_id": str(index)}
                for index, item in enumerate(shortlist)
            ],
        },
        charge=charge,
    )
    if choice.candidate_id is not None:
        choice.symbol = shortlist[int(choice.candidate_id)]["symbol"]
    if choice.symbol and choice.symbol not in candidates:
        raise ValueError(f"Search returned noncandidate symbol {choice.symbol!r}.")
    evidence = {
        "queries": history,
        "requirements": plan.model_dump(),
        "choice": choice.model_dump(),
        "candidates": shortlist,
        "product": candidates.get(choice.symbol),
        "mapping": mappings,
        "proposed_mapping": {
            "row": [
                (item.match_type, item.parameter, item.value)
                for item in choice.footprint_constraints
            ]
        },
        "coverage": "Bounded search: absence/global cheapest not established.",
    }
    return (
        choice.symbol or "",
        evidence,
        "candidate_unreviewed" if choice.symbol else "no_safe_match",
    )


def validate_row(row: dict, charge) -> tuple[dict, dict, str]:
    if not row["selection"]:
        raise ValueError("Choose a TME symbol or run search before validation.")
    progress.step("Loading selected component for validation")
    product = row["evidence"].get("product")
    if not product or product["symbol"] != row["selection"]:
        product = fetch(
            row["selection"], int(row["fields"]["Final Item Count"]), allow_unpriced=True
        )
    mappings = tme.load_footprint_options(
        Path(__file__).with_name("footprint-translations.csv"), row["fields"]["Footprint"]
    )
    if row["evidence"].get("mapping_approved"):
        mappings = row["evidence"].get("proposed_mapping") or mappings
    footprint = bool(mappings) and tme.has_footprint_options(product["parameters"], mappings)
    evidence = {
        "bom": bom_fields(row["fields"]),
        "product": agent_product(product) | {"footprint_match": footprint},
        "mapping": mappings,
        "deterministic_footprint_match": footprint,
        "deterministic_value_check": compare_value(row["fields"], product),
    }
    progress.step("Agent checking component compatibility")
    result = ask(
        Validation,
        "Independently validate the selected component. Confidence 0–100 "
        "estimates likelihood of compatibility with the BOM. Include checks for type, "
        "value, all ratings, MPN/family, package, polarity/pinout and commercial risk. "
        "Cite literal parameter names/values or BOM fields. Each check must include "
        "category "
        "type/value/footprint/polarity_pinout/ratings/mpn/technology/commercial. "
        "Include all eight categories, using not_applicable where justified. For "
        "pass checks, sources must list actual dot-separated evidence paths, e.g. "
        "bom.Value or product.parameters.Capacitance. If evidence is "
        "unavailable use unknown. Say precisely where unsure. "
        "Confidence measures compatibility: a known technical mismatch means 0-20; "
        "missing critical evidence means 30-60. 90+ requires supported technical checks. "
        "Stock/price problems belong only to commercial, not likely_bad. Do not fail a "
        "technical check solely because no global footprint mapping exists. Unspecified "
        "ratings are not_applicable, never invented requirements. 3.5V is not equal to 3.6V. "
        "Do not compare the reference designator with the MPN. Include mapping in sources "
        "when citing it. "
        "Mark likely_bad for likely incompatibility. Missing pinout or geometry cannot "
        "be inferred from package naming. Search-agent commentary is not evidence.",
        evidence,
        charge=charge,
    )
    progress.step("Checking agent evidence and saving validation")
    verdict = "validated_match"
    outcomes = [check.outcome for check in result.checks]
    numeric = compare_value(row["fields"], product)
    if numeric["outcome"] == "fail" or any(
        check.outcome == "fail" and check.category != "commercial" for check in result.checks
    ):
        verdict = "no_safe_match"
    elif (
        not footprint
        or (
            result.likely_bad
            and not any(
                check.category == "commercial" and check.outcome == "fail"
                for check in result.checks
            )
        )
        or "unknown" in outcomes
        or result.uncertainties
        or not checklist_complete(result, evidence)
    ):
        verdict = "needs_review"
    elif product.get("needs_attention") or any(
        check.outcome == "fail" and check.category == "commercial" for check in result.checks
    ):
        verdict = "needs_attention"
    if numeric["outcome"] == "fail":
        result.uncertainties.append(
            f"Application check: {numeric['requirement']} mismatch; "
            f"required {numeric['expected']}, API {numeric['actual']}."
        )
    if not footprint:
        result.uncertainties.append("Application check: exact footprint mapping is unconfirmed.")
    if not checklist_complete(result, evidence):
        result.uncertainties.append(
            "Application check: checklist categories or evidence references "
            "are incomplete; the confidence estimate does not resolve this."
        )
    limit = 20 if verdict == "no_safe_match" else 60 if verdict == "needs_review" else 100
    if result.confidence > limit:
        result.uncertainties.append(
            f"Application confidence cap: {result.confidence} to {limit}; "
            "deterministic checks or evidence coverage remain unresolved."
        )
        result.confidence = limit
    return result.model_dump(), product, verdict


class Charge:
    """Reserve against job and run allowance transactionally before model requests."""

    def __init__(self, store, run_id: str, job_id: int, active):
        self.store, self.run_id, self.job_id, self.active = store, run_id, job_id, active

    def progress(self, message: str):
        """Persist the latest phase and a short activity trail alongside existing job costs."""
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = db.execute("SELECT result FROM jobs WHERE id=?", (self.job_id,)).fetchone()
            metadata = json.loads(record["result"])
            update = {"message": message, "at": now}
            history = metadata.get("activity", [])
            if not history or history[-1]["message"] != message:
                history = (history + [update])[-8:]
            metadata.update(progress=update, activity=history)
            metadata.setdefault("started_at", now)
            db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(metadata), self.job_id))

    def reserve(self, amount: float) -> float:
        self.active()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            spent = db.execute("SELECT result FROM jobs WHERE id=?", (self.job_id,)).fetchone()
            metadata = json.loads(spent["result"])
            cost = metadata.get("reserved_cost", 0)
            if cost + amount > 0.008:
                raise ValueError(
                    "Row inference allowance exhausted; inspect results before rerunning."
                )
            changed = db.execute(
                """UPDATE runs SET reserved=reserved+? WHERE id=?
                AND spent+reserved+?<=allowance AND state='running' """,
                (amount, self.run_id, amount),
            )
            if changed.rowcount != 1:
                raise ValueError("Run paused, canceled, or its spend allowance is exhausted.")
            db.execute(
                "UPDATE jobs SET result=? WHERE id=?",
                (json.dumps(metadata | {"reserved_cost": cost + amount}), self.job_id),
            )
        return amount

    def reconcile(self, reserved: float, actual: float):
        with self.store.connect() as db:
            db.execute(
                "UPDATE runs SET reserved=MAX(0,reserved-?),spent=spent+? WHERE id=?",
                (reserved, actual, self.run_id),
            )
            record = db.execute(
                "SELECT row_id,result FROM jobs WHERE id=?", (self.job_id,)
            ).fetchone()
            metadata = json.loads(record["result"])
            metadata["reserved_cost"] = max(0, metadata.get("reserved_cost", 0) - reserved + actual)
            db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(metadata), self.job_id))
            row_id = record["row_id"]
            self.store.event(db, self.run_id, row_id, "model_cost", {"usd": actual})


def checklist_complete(result: Validation, evidence: dict) -> bool:
    """Fail closed on omitted categories or references to nonexistent evidence."""
    required = {
        "type",
        "value",
        "footprint",
        "polarity_pinout",
        "ratings",
        "mpn",
        "technology",
        "commercial",
    }
    if not required.issubset({check.category for check in result.checks}):
        return False
    for check in result.checks:
        if check.outcome == "pass" and not check.sources:
            return False
        for path in check.sources:
            value = evidence
            try:
                for key in path.split("."):
                    value = value[int(key)] if isinstance(value, list) else value[key]
            except (KeyError, IndexError, ValueError, TypeError):
                return False
    return True


RESULT_COLUMNS = {
    "Selection Origin",
    "TME_SYMBOL",
    "TME Symbol",
    "Manufacturer Part Number",
    "TME URL",
    "Unit Price PLN",
    "Line Total PLN",
    "Order Qty",
    "Excess Qty",
    "Stock Qty",
    "TME Match Status",
    "TME Match Notes",
    "Validation Confidence",
    "Validation Uncertainty",
}


def bom_fields(fields: dict) -> dict:
    """Keep prior selections, verdicts and review comments out of agent requirements."""
    return {key: value for key, value in fields.items() if key not in RESULT_COLUMNS}


def agent_product(product: dict) -> dict:
    """Compact API parameters while preserving their order and literal source values."""
    result = {
        key: value
        for key, value in product.items()
        if key not in {"parameters", "parameter_summary", "photo_url"}
    }
    result["parameters"] = {}
    for index, item in enumerate(product.get("parameters", [])):
        name = item.get("name", "").replace(".", "_")
        if name in result["parameters"]:
            name += f" [{index}]"
        result["parameters"][name] = ", ".join(
            str(value.get("value", "")) for value in item.get("values", [])
        )
    return result
