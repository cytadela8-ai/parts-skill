"""Persistent row jobs with bounded threads, explicit dispatch, and revision-safe publication."""

import json
import math
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from parts_app import progress
from parts_app.agents import MODEL
from parts_app.procedures import Charge, search_row, validate_row


def row_task(row: dict, procedure: str) -> dict:
    """Plan a row explicitly, preserving manual choices while allowing their validation."""
    protected = (
        row["review"]
        or row["verdict"] == "manually_provided"
        or row["evidence"].get("manual_origin")
    )
    mode, reason = procedure, ""
    if row["review"] == "not needed":
        mode, reason = "skip", "Marked not needed."
    elif protected and procedure == "search":
        mode, reason = "skip", "Selection protected; use Allow automated replacement to search."
    elif procedure == "validate" and not row["selection"]:
        mode, reason = "skip", "No component selected; run search first."
    elif protected and procedure == "both":
        mode, reason = "validate", "Keep protected selection; validate it without searching."
    return {
        "id": row["id"],
        "reference": row["fields"]["Reference"],
        "procedure": mode,
        "reason": reason,
    }


class Worker:
    def __init__(self, store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.dispatch_lock = threading.Lock()

    def preview(self, row_ids: list[str], procedure: str) -> list[dict]:
        """Describe the actual action or skip reason for every selected row."""
        if procedure not in ("search", "validate", "both"):
            raise ValueError("Choose search, validate, or both.")
        if not row_ids:
            raise ValueError("Select at least one row.")
        return [row_task(self.store.row(row_id), procedure) for row_id in dict.fromkeys(row_ids)]

    def start(self, row_ids: list[str], procedure: str, allowance: float) -> str:
        """Freeze selected revisions and dispatch only unprotected, nonbusy rows."""
        if procedure not in ("search", "validate", "both"):
            raise ValueError("Choose search, validate, or both.")
        if (
            not row_ids
            or not math.isfinite(allowance)
            or allowance <= 0
            or allowance > len(set(row_ids)) * 0.008
        ):
            raise ValueError("Choose rows and a positive allowance no larger than $0.008 per row.")
        snapshots = [self.store.row(row_id) for row_id in dict.fromkeys(row_ids)]
        run_id = uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO runs(id,procedure,state,model,allowance) VALUES (?,?,?,?,?)",
                (run_id, procedure, "running", MODEL, allowance),
            )
            added = 0
            for row in snapshots:
                task = row_task(row, procedure)
                metadata = {
                    "queued_at": time.time(),
                    "procedure": task["procedure"],
                    "reason": task["reason"],
                }
                if task["procedure"] == "skip":
                    metadata["finished_at"] = time.time()
                    db.execute(
                        "INSERT INTO jobs(run,row_id,revision,state,result) VALUES (?,?,?,?,?)",
                        (run_id, row["id"], row["revision"], "skipped", json.dumps(metadata)),
                    )
                    continue
                busy = db.execute(
                    "SELECT id FROM jobs WHERE row_id=? AND state IN ('queued',"
                    "'running','interrupted')",
                    (row["id"],),
                ).fetchone()
                if busy:
                    raise ValueError("One of the rows already has unfinished work.")
                db.execute(
                    "INSERT INTO jobs(run,row_id,revision,state,result) VALUES (?,?,?,?,?)",
                    (
                        run_id,
                        row["id"],
                        row["revision"],
                        "queued",
                        json.dumps(metadata),
                    ),
                )
                added += 1
            if not added:
                raise ValueError("No eligible rows. Check the preview for each row’s skip reason.")
        self.pool.submit(self.dispatch, run_id)
        return run_id

    def dispatch(self, run_id: str):
        with self.dispatch_lock:
            with self.store.connect() as db:
                run = dict(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
                if run["state"] != "running":
                    return
                jobs = list(
                    db.execute("SELECT * FROM jobs WHERE run=? AND state='queued'", (run_id,))
                )

            # Dispatch is serialized; queued runs wait until the current run releases the lock.
            for job in jobs:
                with self.store.connect() as db:
                    state = db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()[
                        "state"
                    ]
                    if state != "running":
                        break
                    db.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
                self.execute(dict(job), run)
            with self.store.connect() as db:
                db.execute(
                    "UPDATE runs SET state='completed' WHERE id=? AND state='running'", (run_id,)
                )

    def active(self, run_id: str):
        with self.store.connect() as db:
            state = db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()["state"]
        if state != "running":
            raise ValueError(f"Run {state}; no further requests will be made.")

    def execute(self, job: dict, run: dict):
        """Run one row with scoped activity reporting and durable terminal timestamps."""
        charge = Charge(self.store, run["id"], job["id"], lambda: self.active(run["id"]))
        try:
            with progress.track(charge.progress):
                progress.step("Preparing component")
                self.run_job(job, run, charge)
        finally:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT state,result FROM jobs WHERE id=?", (job["id"],)
                ).fetchone()
                if current["state"] in ("completed", "failed", "stale", "canceled"):
                    metadata = json.loads(current["result"]) | {"finished_at": time.time()}
                    db.execute(
                        "UPDATE jobs SET result=? WHERE id=?",
                        (json.dumps(metadata), job["id"]),
                    )

    def run_job(self, job: dict, run: dict, charge):
        try:
            self.active(run["id"])
            row = self.store.row(job["row_id"])
            if row["revision"] != job["revision"]:
                raise ValueError("Row edited since run started; rerun with the current revision.")
            result = {"selection": row["selection"], "evidence": row["evidence"]}
            metadata = json.loads(job.get("result", "{}"))
            procedure = metadata.get("procedure", run["procedure"])
            searched = procedure in ("search", "both") and metadata.get("phase") != "validate"
            if searched:
                symbol, evidence, verdict = search_row(row, charge)
                result.update(selection=symbol, evidence=evidence, verdict=verdict)
                row = row | result
            if procedure in ("validate", "both") and row["selection"]:
                # Persist a successful tentative search even if later validation fails.
                if procedure == "both" and searched:
                    if not self.publish(job, run, result, complete=False):
                        return
                    row = self.store.row(job["row_id"])
                    job = job | {"revision": row["revision"]}
                    with self.store.connect() as db:
                        record = db.execute(
                            "SELECT result FROM jobs WHERE id=?", (job["id"],)
                        ).fetchone()
                        metadata = json.loads(record[0]) | {"phase": "validate"}
                        db.execute(
                            "UPDATE jobs SET state='running',revision=?,result=? WHERE id=?",
                            (row["revision"], json.dumps(metadata), job["id"]),
                        )
                validation, product, verdict = validate_row(row, charge)
                result.update(validation=validation, verdict=verdict)
                result["evidence"] = result["evidence"] | {"product": product}
            progress.step("Saving component result")
            self.publish(job, run, result)
        except Exception as exc:  # noqa: BLE001 - Worker boundary persists failures for UI inspection.
            with self.store.connect() as db:
                state = db.execute("SELECT state FROM runs WHERE id=?", (run["id"],)).fetchone()[
                    "state"
                ]
                status = "queued" if state == "paused" else "failed"
                if state == "canceled":
                    status = "canceled"
                db.execute(
                    "UPDATE jobs SET state=?,error=? WHERE id=?", (status, str(exc), job["id"])
                )

    def publish(self, job: dict, run: dict, result: dict, *, complete: bool = True):
        validation = result.get("validation", result["evidence"].get("choice", {}))
        if "validation" in result:
            result["evidence"] = result["evidence"] | {"validation": result["validation"]}
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT state FROM runs WHERE id=?", (run["id"],)).fetchone()[
                "state"
            ]
            current = self.store.row(job["row_id"])
            self.store.event(db, run["id"], job["row_id"], "procedure_result", result)
            if state == "canceled" or current["revision"] != job["revision"]:
                db.execute(
                    "UPDATE jobs SET state='stale',error=? WHERE id=?",
                    ("Result retained in history; current row changed or run canceled.", job["id"]),
                )
                return False
            fields = current["fields"]
            product = result["evidence"].get("product")
            if product:
                fields = fields | product_fields(product)
            db.execute(
                """UPDATE rows SET revision=revision+1,selection=?,evidence=?,verdict=?,
                confidence=?,uncertainty=?,fields=? WHERE id=?""",
                (
                    result["selection"],
                    json.dumps(result["evidence"]),
                    result.get("verdict", "needs_review"),
                    validation.get("confidence"),
                    validation.get("comment", "")
                    + "\n"
                    + "\n".join(validation.get("uncertainties", [])),
                    json.dumps(fields),
                    job["row_id"],
                ),
            )
            if complete:
                db.execute("UPDATE jobs SET state='completed' WHERE id=?", (job["id"],))
            return True

    def control(self, run_id: str, action: str):
        states = {"pause": "paused", "cancel": "canceled", "resume": "running"}
        if action not in states:
            raise ValueError("Unknown run control.")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
            allowed = {
                "pause": ("running",),
                "resume": ("paused", "interrupted"),
                "cancel": ("running", "paused", "interrupted"),
            }
            if run is None or run["state"] not in allowed[action]:
                raise ValueError("This control is unavailable for the current run state.")
            db.execute("UPDATE runs SET state=? WHERE id=?", (states[action], run_id))
            if action == "cancel":
                db.execute(
                    "UPDATE jobs SET state='canceled',result=json_set(result, '$.finished_at', ?) "
                    "WHERE run=? AND state='queued'",
                    (time.time(), run_id),
                )
            if action == "resume":
                db.execute(
                    "UPDATE jobs SET state='queued' WHERE run=? AND state='interrupted'", (run_id,)
                )
        if action == "resume":
            self.pool.submit(self.dispatch, run_id)


def product_fields(product: dict) -> dict:
    return {
        "TME URL": product["product_url"],
        "Manufacturer Part Number": ", ".join(product.get("manufacturer_part_numbers", [])),
        "Unit Price PLN": str(product["unit_price_pln"])
        if product["unit_price_pln"] is not None
        else "",
        "Line Total PLN": str(product["line_total_pln"])
        if product["line_total_pln"] is not None
        else "",
        "Order Qty": str(product["order_quantity"]),
        "Stock Qty": str(product["stock_quantity"]),
        "Excess Qty": str(product["excess_quantity"]),
    }
