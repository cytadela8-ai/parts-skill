"""Transactional project state; revisions prevent workers overwriting human changes."""

import csv
import io
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    """Persist rows, run events, evidence, and cost reservations in one portable file."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rows (
                    id TEXT PRIMARY KEY, project TEXT, position INTEGER, revision INTEGER,
                    fields TEXT, selection TEXT, review TEXT, verdict TEXT, evidence TEXT,
                    confidence REAL, uncertainty TEXT
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, procedure TEXT, state TEXT, model TEXT,
                    allowance REAL, spent REAL DEFAULT 0, reserved REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, run TEXT, row_id TEXT, revision INTEGER,
                    state TEXT, result TEXT DEFAULT '{}', error TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run TEXT, row_id TEXT, kind TEXT, payload TEXT,
                    created TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            db.execute("UPDATE jobs SET state='interrupted' WHERE state='running'")
            db.execute("UPDATE runs SET state='interrupted' WHERE state='running'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_csv(self, content: bytes) -> str:
        """Import original or enriched CSV without losing its selection provenance."""
        text = content.decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise ValueError("Duplicate CSV headers; rename them before import.")
        missing = {"Reference", "Value", "Footprint", "Qty"} - set(headers)
        if missing:
            raise ValueError("Missing CSV columns: " + ", ".join(sorted(missing)))
        rows = list(reader)
        if not rows:
            raise ValueError("CSV contains no component rows.")
        project = uuid.uuid4().hex
        with self.connect() as db:
            for position, fields in enumerate(rows):
                if None in fields or any(value is None for value in fields.values()):
                    raise ValueError(f"CSV row {position + 2} does not match the header.")
                try:
                    qty = int(fields["Qty"])
                    if qty < 1:
                        raise ValueError()
                except ValueError as exc:
                    raise ValueError(
                        f"CSV row {position + 2}: Qty must be a positive integer."
                    ) from exc
                fields.setdefault("Final Item Count", str(max(qty + 1, math.ceil(qty * 1.15))))
                status = fields.get("TME Match Status", "")
                symbol = (
                    fields.get("TME_SYMBOL", "").strip() or fields.get("TME Symbol", "").strip()
                )
                if status and status != "manually_provided":
                    symbol = fields.get("TME Symbol", "").strip() or symbol
                review = (
                    status if status in ("approved", "not needed", "manually_substituted") else ""
                )
                if status == "not needed":
                    fields["Final Item Count"] = "0"
                if not status and symbol:
                    status = "manually_provided"
                db.execute(
                    "INSERT INTO rows VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        project,
                        position,
                        0,
                        json.dumps(fields),
                        symbol,
                        review,
                        status,
                        json.dumps(
                            {
                                "manual_origin": status == "manually_provided"
                                or fields.get("Selection Origin") == "manual"
                            }
                        ),
                        None,
                        "Imported CSV has no stored validation evidence.",
                    ),
                )
        return project

    def rows(self, project: str | None = None) -> list[dict]:
        with self.connect() as db:
            records = (
                db.execute("SELECT * FROM rows WHERE project=? ORDER BY position", (project,))
                if project
                else db.execute("SELECT * FROM rows ORDER BY project, position")
            )
            return [self.decode(row) for row in records]

    @staticmethod
    def decode(record) -> dict:
        row = dict(record)
        for key in ("fields", "evidence"):
            row[key] = json.loads(row[key])
        return row

    def row(self, row_id: str) -> dict:
        with self.connect() as db:
            record = db.execute("SELECT * FROM rows WHERE id=?", (row_id,)).fetchone()
        if record is None:
            raise ValueError("Component row no longer exists.")
        return self.decode(record)

    def edit(self, row_id: str, revision: int, changes: dict) -> dict:
        """Apply an optimistic edit and invalidate old validation when inputs change."""
        row = self.row(row_id)
        fields = row["fields"] | changes.get("fields", {})
        selection = changes.get("selection", row["selection"])
        changed = (
            fields != row["fields"]
            or selection != row["selection"]
            or changes.get("evidence", row["evidence"]) != row["evidence"]
        )
        review = changes.get("review", "" if changed else row["review"])
        if review not in ("", "approved", "not needed", "manually_substituted"):
            raise ValueError("Unknown human review state.")
        count = int(fields["Final Item Count"])
        if count < 0:
            raise ValueError("Final Item Count cannot be negative.")
        if review == "not needed":
            fields["Final Item Count"] = "0"
        if review == "approved" and not selection:
            raise ValueError("Choose a TME component before approving.")
        evidence = changes.get("evidence", {} if changed else row["evidence"])
        if changed:
            evidence.pop("validation", None)
        evidence["manual_origin"] = bool(row["evidence"].get("manual_origin"))
        if review == "manually_substituted":
            evidence["manual_origin"] = True
        if changes.get("unlock"):
            evidence["manual_origin"] = False
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE rows SET revision=revision+1,fields=?,selection=?,
                review=?,verdict=?,confidence=?,uncertainty=?,evidence=?
                WHERE id=? AND revision=?""",
                (
                    json.dumps(fields),
                    selection,
                    review,
                    "candidate_unreviewed" if changed or changes.get("unlock") else row["verdict"],
                    None if changed else row["confidence"],
                    "Inputs changed; validate again." if changed else row["uncertainty"],
                    json.dumps(evidence),
                    row_id,
                    revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Row changed during this operation. Refresh and try again.")
            self.event(db, "", row_id, "human_edit", changes)
        return self.row(row_id)

    @staticmethod
    def event(db, run: str, row_id: str, kind: str, payload: dict):
        db.execute(
            "INSERT INTO events(run,row_id,kind,payload) VALUES (?,?,?,?)",
            (run, row_id, kind, json.dumps(payload)),
        )

    def export_csv(self, project: str, basket: bool = False) -> str:
        """Export preserved BOM fields with current review results or ordering quantities."""
        rows = self.rows(project)
        exported = []
        for row in rows:
            fields = row["fields"] | {
                "TME Symbol": row["selection"],
                "Selection Origin": "manual" if row["evidence"].get("manual_origin") else "agent",
                "TME Match Status": row["review"] or row["verdict"],
                "Validation Confidence": "" if row["confidence"] is None else row["confidence"],
                "Validation Uncertainty": row["uncertainty"],
            }
            if basket:
                if row["review"] == "not needed":
                    continue
                if row["review"] != "approved" or not row["selection"]:
                    raise ValueError("Approve all needed rows before exporting the basket.")
                fields = {
                    "TME Symbol": row["selection"],
                    "Count": fields.get("Order Qty", fields["Final Item Count"]),
                }
            exported.append(fields)
        headers = list(dict.fromkeys(key for row in exported for key in row))
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows(exported)
        return output.getvalue()
