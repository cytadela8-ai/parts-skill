"""Bounded per-row search/validation, with API evidence independent of agent conclusions."""

import json
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import create_model

from parts_app import progress, tme
from parts_app.agents import (
    CHOICE_PROMPT,
    VALIDATION_PROMPT,
    Check,
    Choice,
    SearchPlan,
    Validation,
    ask,
)
from parts_app.requirements import compare_value, requirement_checks, shortlist_candidates
from parts_app.validation import audit_checks, pinout_scope

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
    from parts_app.sourcing import find_candidates

    mappings = tme.load_footprint_options(
        Path(__file__).with_name("footprint-translations.csv"), row["fields"]["Footprint"]
    )
    candidates, history = find_candidates(bom_fields(row["fields"]), plan, mappings, charge)
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
        CHOICE_PROMPT,
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
    application_checks = requirement_checks(bom_fields(row["fields"]), product, mappings)
    footprint = application_checks[0]["outcome"] != "fail" and (
        application_checks[0]["outcome"] == "pass"
        or (bool(mappings) and tme.has_footprint_options(product["parameters"], mappings))
    )
    if footprint and application_checks[0]["outcome"] != "fail":
        application_checks[0]["outcome"] = "pass"
    evidence = {
        "bom": bom_fields(row["fields"]),
        "product": agent_product(product) | {"footprint_match": footprint},
        "mapping": mappings,
        "deterministic_footprint_match": footprint,
        "deterministic_value_check": compare_value(row["fields"], product),
        "application_checks": application_checks,
        "pinout_scope": pinout_scope(
            bom_fields(row["fields"]), agent_product(product)["parameters"]
        ),
    }
    progress.step("Agent checking component compatibility")
    result = ask(
        Validation,
        VALIDATION_PROMPT,
        evidence,
        charge=charge,
    )
    return finalize_validation(result, evidence, product, application_checks)


def finalize_validation(result: Validation, evidence: dict, product: dict, checks: list):
    """Apply authoritative checks and retain the raw model assessment for audit."""
    footprint = evidence["deterministic_footprint_match"]
    progress.step("Checking agent evidence and saving validation")
    model_evaluation = result.model_dump()
    result.confidence = (
        result.model_confidence if result.model_confidence is not None else result.confidence
    )
    result.uncertainties = [
        note for note in result.uncertainties if not note.startswith("Application confidence cap:")
    ]
    audit_checks(result, evidence)
    for check in checks:
        if check["outcome"] in {"fail", "unknown"}:
            for existing in result.checks:
                if existing.category == check["category"] and existing.outcome == "pass":
                    existing.outcome = check["outcome"]
                    existing.evidence = "Application override: " + check["evidence"]
        result.checks.append(Check(**check))
    failures = [
        check
        for check in result.checks
        if check.category != "commercial" and check.outcome == "fail"
    ]
    if failures:
        result.likely_bad = True
    if any(check["outcome"] == "fail" for check in checks):
        result.comment = "Known incompatibility: " + "; ".join(
            check["evidence"] for check in checks if check["outcome"] == "fail"
        )
    verdict = "validated_match"
    outcomes = [check.outcome for check in result.checks]
    numeric = evidence["deterministic_value_check"]
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
        result.checks.append(
            Check(
                category="other",
                requirement="Evidence coverage",
                outcome="unknown",
                sources=[],
                evidence="Checklist categories or evidence references are incomplete.",
            )
        )
        result.uncertainties.append(
            "Application check: checklist categories or evidence references "
            "are incomplete; the confidence estimate does not resolve this."
        )
    if verdict == "needs_review":
        unresolved = list(
            dict.fromkeys(
                c.category.replace("_", "/") for c in result.checks if c.outcome == "unknown"
            )
        )
        if unresolved:
            result.comment = "Attention: evidence is missing or insufficient for " + "; ".join(
                unresolved
            )
    limit = 20 if verdict == "no_safe_match" else 60 if verdict == "needs_review" else 100
    if result.confidence > limit:
        result.uncertainties.append(
            f"Application confidence cap: {result.confidence} to {limit}; "
            "deterministic checks or evidence coverage remain unresolved."
        )
        result.confidence = limit
    return result.model_dump() | {"model_evaluation": model_evaluation}, product, verdict


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
        if check.outcome == "not_applicable":
            continue
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
