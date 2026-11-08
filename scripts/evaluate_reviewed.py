"""Bounded live evaluation against human-reviewed BOMs; never modifies reference CSVs.

Run with uv run python scripts/evaluate_reviewed.py --phase validate or --phase search.
Results/checkpoints are local JSONL; answer columns never enter agent BOM requirements.
A run-wide $0.25 conservative ceiling covers both phases and uncertain charges.
"""

import argparse
import csv
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from parts_app.procedures import bom_fields, search_row, validate_row

DIRECTORY = Path(__file__).resolve().parents[1]
RESULTS = DIRECTORY / "parts-data/evaluation.jsonl"
LOCK = threading.Lock()
CEILING = 0.25


def records():
    if not RESULTS.exists():
        return []
    return [json.loads(line) for line in RESULTS.read_text().splitlines()]


class Charge:
    def __init__(self):
        self.spent = 0.0
        self.reserved = 0.0

    def reserve(self, amount):
        with LOCK:
            if self.spent + self.reserved + amount > 0.008:
                raise ValueError("Evaluation row budget exhausted")
            files = set(RESULTS.parent.glob("evaluation*.jsonl"))
            if RESULTS.exists():
                files.add(RESULTS)
            total = sum(
                json.loads(line)["spent"] + json.loads(line)["reserved"]
                for file in files
                for line in file.read_text().splitlines()
            )
            if total + sum(c.spent + c.reserved for c in CHARGES) + amount > CEILING:
                raise ValueError("Evaluation total budget exhausted")
            self.reserved += amount
        return amount

    def reconcile(self, reserved, actual):
        with LOCK:
            self.reserved -= reserved
            self.spent += actual


CHARGES = []


def run_one(task, phase):
    charge = Charge()
    with LOCK:
        CHARGES.append(charge)
    row = {"fields": bom_fields(task["original"]), "selection": "", "evidence": {}}
    cached = [
        r
        for r in (
            json.loads(line)
            for line in (DIRECTORY / "parts-data/evaluation.jsonl").read_text().splitlines()
        )
        if r["dataset"] == task["dataset"]
        and r["reference"] == task["reference"]
        and r["phase"] == "validate"
        and r.get("product")
    ]
    if phase == "validate" and cached:
        row["evidence"] = {"product": cached[-1]["product"]}
    outcome = task | {"phase": phase}
    try:
        if phase == "validate":
            row["selection"] = task["gold"]
            validation, product, verdict = validate_row(row, charge)
            outcome.update(
                validation=validation, product=product, verdict=verdict, selected=row["selection"]
            )
        else:
            selected, evidence, verdict = search_row(row, charge)
            if phase == "both" and selected:
                row.update(selection=selected, evidence=evidence)
                validation, product, verdict = validate_row(row, charge)
                outcome.update(validation=validation, product=product)
            outcome.update(
                selected=selected,
                evidence=evidence,
                verdict=verdict,
                exact_agreement=selected == task["gold"],
            )
    except Exception as error:  # noqa: BLE001 - Record failed paid calls instead of losing the result.
        outcome["error"] = str(error)
    with LOCK:
        outcome.update(spent=charge.spent, reserved=charge.reserved)
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        with RESULTS.open("a") as stream:
            stream.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        CHARGES.remove(charge)
        print(
            json.dumps(
                {
                    k: outcome.get(k)
                    for k in (
                        "dataset",
                        "reference",
                        "phase",
                        "selected",
                        "verdict",
                        "error",
                        "spent",
                        "reserved",
                    )
                }
            ),
            flush=True,
        )
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["validate", "search", "both"], required=True)
    parser.add_argument("--dataset", default="all")
    parser.add_argument("--reference", default="")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--output", type=Path, default=RESULTS)
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    args = parser.parse_args()
    globals()["RESULTS"] = args.output
    load_dotenv(args.env_file)
    previous = records()
    tasks = []
    for filename in ["VFD-disp-tme-results-final.csv", "tme-results-final.csv"]:
        if args.dataset != "all" and args.dataset not in filename:
            continue
        with (DIRECTORY / "tests/fixtures" / filename).open() as stream:
            for row in csv.DictReader(stream):
                if row["TME Match Status"] == "not needed" or not row["TME Symbol"]:
                    continue
                if args.reference and row["Reference"] != args.reference:
                    continue
                done = [
                    r
                    for r in previous
                    if r["dataset"] == filename
                    and r["reference"] == row["Reference"]
                    and r["phase"] == args.phase
                ]
                if done and not (args.retry_errors and done[-1].get("error")):
                    continue
                tasks.append(
                    {
                        "dataset": filename,
                        "reference": row["Reference"],
                        "gold": row["TME Symbol"],
                        "original": row,
                    }
                )
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in as_completed([pool.submit(run_one, task, args.phase) for task in tasks]):
            result.result()


if __name__ == "__main__":
    main()
