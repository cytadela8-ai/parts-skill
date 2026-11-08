"""Browser review summaries and bounded, read-only caching of current supplier data."""

import json
import threading
import time
from collections import OrderedDict

from parts_app.procedures import bom_fields


class ProductCache:
    """Serialize supplier reads and reuse up to 128 snapshots for fifteen minutes."""

    def __init__(self, loader):
        self.loader = loader
        self.lock = threading.Lock()
        self.items = OrderedDict()

    def get(self, row: dict) -> dict:
        """Read a product without modifying row revisions, approvals or validation."""
        symbol = row["selection"]
        if not symbol:
            raise ValueError("Choose a TME component before loading supplier data.")
        quantity = max(1, int(row["fields"]["Final Item Count"]))
        key = (symbol, quantity)
        with self.lock:
            cached = self.items.get(key)
            if cached and time.monotonic() - cached[0] < 900:
                self.items.move_to_end(key)
                return cached[1]
            product = self.loader(symbol, quantity, allow_unpriced=True)
            self.items[key] = (time.monotonic(), product)
            self.items.move_to_end(key)
            while len(self.items) > 128:
                self.items.popitem(last=False)
            return product


def row_review(row: dict, db) -> dict:
    """Expose current checklist evidence, recovering older published results from history."""
    validation = row["evidence"].get("validation")
    if not validation and row["confidence"] is not None:
        records = db.execute(
            "SELECT payload FROM events WHERE row_id=? AND kind='procedure_result' "
            "ORDER BY id DESC",
            (row["id"],),
        )
        for record in records:
            result = json.loads(record["payload"])
            if result.get("selection") == row["selection"] and result.get("validation"):
                validation = result["validation"]
                break
    return row | {
        "bom": bom_fields(row["fields"]),
        "validation": validation,
        "compatibility": compatibility(validation, row["verdict"]),
    }


def compatibility(validation: dict | None, verdict: str) -> str:
    """Summarize agent checks; evidence audits and human approval remain separate."""
    if not validation:
        return "not_checked"
    outcomes = [
        check["outcome"]
        for check in validation.get("checks", [])
        if check["outcome"] != "not_applicable"
    ]
    if "fail" in outcomes or verdict == "no_safe_match":
        return "fail"
    uncertainties = [
        note
        for note in validation.get("uncertainties", [])
        if not note.startswith(("Application check:", "Application confidence cap:"))
    ]
    if not outcomes or "unknown" in outcomes or validation.get("likely_bad") or uncertainties:
        return "attention"
    return "pass"
