"""Export evaluated human decisions and blind searches, with current deterministic policy replay.

Run after evaluate_reviewed.py. Reports preserve raw model findings and separately report the
application verdict. Policy replay uses stored API/model evidence and makes no external requests.
"""

import csv
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from parts_app.agents import Validation
from parts_app.procedures import validate_row

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "parts-data"
OUTPUT = ROOT / "docs/evaluation"


def read(name):
    path = DATA / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def latest(rows):
    return {(row["dataset"], row["reference"]): row for row in rows}


def write(name, rows):
    with (OUTPUT / name).open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def replay(row):
    """Reapply current publication checks to previously paid model output without rebilling."""
    if row.get("error") or not row.get("validation"):
        return row
    response = dict(row["validation"])
    response["confidence"] = response.get("model_confidence") or response["confidence"]
    response["uncertainties"] = [
        note for note in response["uncertainties"] if not note.startswith("Application ")
    ]
    snapshot = {
        "fields": row["original"],
        "selection": row["selected"],
        "evidence": {"product": row["product"]},
    }
    with patch("parts_app.procedures.ask", return_value=Validation.model_validate(response)):
        validation, _, verdict = validate_row(snapshot, None)
    return row | {"validation": validation, "verdict": verdict}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    gold = [replay(row) for row in latest(read("evaluation-gold-checked.jsonl")).values()]
    blind = latest([row for row in read("evaluation-final.jsonl") if row["phase"] == "both"])
    blind.update(latest(read("evaluation-regression.jsonl")))
    blind = [replay(row) for row in blind.values()]
    for filename, rows in [("validation.csv", gold), ("blind-search.csv", blind)]:
        write(
            filename,
            [
                {
                    "Dataset": row["dataset"],
                    "Reference": row["reference"],
                    "BOM Value": row["original"]["Value"],
                    "Human selection": row["gold"],
                    "Agent selection": row.get("selected", ""),
                    "Application verdict": row.get("verdict", "error"),
                    "Raw model confidence": row.get("validation", {}).get("model_confidence", ""),
                    "Checked confidence": row.get("validation", {}).get("confidence", ""),
                    "Comment": row.get("validation", {}).get("comment", ""),
                    "Uncertainties": " | ".join(row.get("validation", {}).get("uncertainties", [])),
                    "Error": row.get("error", ""),
                    "Inference USD": row["spent"],
                    "Uncertain reservation USD": row["reserved"],
                }
                for row in rows
            ],
        )
    summary = {}
    for phase, rows in [("validation", gold), ("blind_search", blind)]:
        summary[phase] = {}
        for dataset in sorted({row["dataset"] for row in rows}):
            subset = [row for row in rows if row["dataset"] == dataset]
            summary[phase][dataset] = {
                "rows": len(subset),
                "errors": sum(bool(row.get("error")) for row in subset),
                "selected": sum(bool(row.get("selected")) for row in subset),
                "exact_human_selection": sum(row.get("selected") == row["gold"] for row in subset),
                "verdicts": dict(Counter(row.get("verdict", "error") for row in subset)),
                "inference_usd": sum(row["spent"] for row in subset),
                "max_inference_usd": max(row["spent"] for row in subset),
            }
    controls = read("evaluation-negative.jsonl")
    summary["negative_controls"] = {
        "rows": len(controls),
        "flagged": sum(row.get("verdict") == "no_safe_match" for row in controls),
        "errors": sum(bool(row.get("error")) for row in controls),
    }
    all_attempts = [row for path in DATA.glob("evaluation*.jsonl") for row in read(path.name)]
    summary["all_experiments"] = {
        "attempts": len(all_attempts),
        "known_inference_usd": sum(row["spent"] for row in all_attempts),
        "uncertain_reservations_usd": sum(row["reserved"] for row in all_attempts),
    }
    (OUTPUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
