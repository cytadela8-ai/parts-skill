"""Regressions discovered by exercising both human-reviewed production BOMs."""

from unittest.mock import Mock, patch

import pytest

from parts_app import tme
from parts_app.agents import Validation
from parts_app.procedures import validate_row
from parts_app.requirements import compare_value, magnitude, shortlist_candidates


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("40m", ".04"),
        ("40mΩ", ".04"),
        ("4,7k", "4700"),
        ("10K", "10000"),
        ("1M", "1000000"),
        ("150µF", ".000150"),
        ("3.9nH", ".0000000039"),
        ("0R", "0"),
    ],
)
def test_real_bom_value_notation(text, expected):
    from decimal import Decimal

    assert magnitude(text) == Decimal(expected)


def test_shortlist_never_prefers_cheap_wrong_zener_voltage():
    fields = {"Reference": "D2", "Value": "Zener 3.5V"}

    def product(symbol, voltage, price):
        return {
            "symbol": symbol,
            "line_total_pln": price,
            "parameters": [
                {"name": "Zener voltage", "values": [{"value": voltage}]},
            ],
        }

    candidates = [product("WRONG", "100V", 0.1), product("CLOSE", "3.6V", 1)]
    assert shortlist_candidates(fields, candidates)[0]["symbol"] == "CLOSE"
    assert compare_value(fields, candidates[1])["outcome"] == "fail"


def complete_validation(commercial="pass"):
    checks = [
        {
            "category": category,
            "requirement": "specified requirement",
            "outcome": "not_applicable",
            "sources": [],
            "evidence": "Not specified",
        }
        for category in [
            "type",
            "value",
            "footprint",
            "polarity_pinout",
            "ratings",
            "mpn",
            "technology",
            "commercial",
        ]
    ]
    checks[2].update(outcome="pass", sources=["product.footprint_match", "mapping.default"])
    checks[-1].update(outcome=commercial)
    return Validation(
        confidence=95, comment="Compatible", uncertainties=[], checks=checks, likely_bad=False
    )


def test_stock_shortage_is_commercial_not_incompatible():
    product = {
        "symbol": "TEST",
        "parameters": [],
        "needs_attention": True,
        "footprint_match": False,
    }
    row = {
        "fields": {
            "Reference": "R1",
            "Value": "10k",
            "Footprint": "0805",
            "Final Item Count": "10",
        },
        "selection": "TEST",
        "evidence": {"product": product},
    }

    def respond(output, prompt, payload, **kwargs):
        assert payload["product"]["footprint_match"] is True
        return complete_validation("fail")

    with (
        patch("parts_app.procedures.ask", side_effect=respond),
        patch("parts_app.tme.load_footprint_options", return_value={"default": []}),
        patch("parts_app.tme.has_footprint_options", return_value=True),
    ):
        _, _, verdict = validate_row(row, Mock())
    assert verdict == "needs_attention"


def test_unpriced_product_still_supports_technical_validation():
    records = {
        "product": {"symbol": "TEST"},
        "parameters": [],
        "data": {"stock_quantity": 100, "prices": {"elements": []}},
    }
    with patch("parts_app.tme.product_records", return_value=records):
        product = tme.load_selected_candidate("TEST", 2, "token", None, allow_unpriced=True)
        assert product["unit_price_pln"] is None
        assert product["line_total_pln"] is None
        assert product["needs_attention"] is True
        with pytest.raises(tme.TmeError, match="no PLN price"):
            tme.load_selected_candidate("TEST", 2, "token", None)


def test_numeric_mismatch_overrides_an_overconfident_agent():
    product = {
        "symbol": "TEST",
        "parameters": [
            {
                "name": "Capacitance",
                "values": [{"value": "47µF"}],
            }
        ],
        "needs_attention": False,
    }
    row = {
        "fields": {
            "Reference": "C28,C34",
            "Value": "40uF",
            "Footprint": "7343",
            "Final Item Count": "2",
        },
        "selection": "TEST",
        "evidence": {"product": product},
    }
    with (
        patch("parts_app.procedures.ask", return_value=complete_validation()),
        patch("parts_app.tme.load_footprint_options", return_value={"default": []}),
        patch("parts_app.tme.has_footprint_options", return_value=True),
    ):
        result, _, verdict = validate_row(row, Mock())
    assert verdict == "no_safe_match"
    assert result["confidence"] == 20
    assert result["model_confidence"] == 95
    assert any("capacitance mismatch" in note for note in result["uncertainties"])


def test_smd_candidate_cannot_be_replaced_with_through_hole():
    from parts_app.requirements import footprint_conflicts

    fields = {"Footprint": "Capacitor_SMD:CP_Elec_5x4.5"}
    product = {"parameters": [{"name": "Mounting", "values": [{"value": "THT"}]}]}
    assert footprint_conflicts(fields, product)
    assert footprint_conflicts(fields, {"parameters": []}) == []


def test_stale_combined_search_stops_before_validation(tmp_path):
    from parts_app.store import Store
    from parts_app.worker import Worker

    store = Store(tmp_path / "stale.sqlite3")
    project = store.import_csv(b"Reference,Value,Footprint,Qty\nR1,10k,0805,1\n")
    row = store.rows(project)[0]
    worker = Worker(store)
    with patch.object(worker.pool, "submit"):
        run_id = worker.start([row["id"]], "both", 0.008)
    with store.connect() as db:
        job = dict(db.execute("SELECT * FROM jobs").fetchone())
        run = dict(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def finish_search(*args):
        store.edit(row["id"], 0, {"selection": "HUMAN"})
        return "OLD_AGENT", {}, "candidate_unreviewed"

    with (
        patch("parts_app.worker.search_row", side_effect=finish_search),
        patch("parts_app.worker.validate_row") as validate,
    ):
        worker.execute(job, run)
    validate.assert_not_called()
    assert store.row(row["id"])["selection"] == "HUMAN"
    worker.pool.shutdown(wait=True)


def test_original_manual_choice_stays_protected_after_validation(tmp_path):
    from parts_app.store import Store
    from parts_app.worker import Worker

    store = Store(tmp_path / "manual.sqlite3")
    project = store.import_csv(b"Reference,Value,Footprint,Qty,TME Symbol\nR1,10k,0805,1,MANUAL\n")
    row = store.rows(project)[0]
    with store.connect() as db:
        db.execute("UPDATE rows SET verdict='needs_review' WHERE id=?", (row["id"],))
    restored = store.import_csv(store.export_csv(project).encode())
    assert store.rows(restored)[0]["evidence"]["manual_origin"] is True
    worker = Worker(store)
    with patch.object(worker.pool, "submit"), pytest.raises(ValueError, match="No eligible"):
        worker.start([row["id"]], "search", 0.008)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    worker.pool.shutdown(wait=True)


@pytest.mark.parametrize("value", ["None", "null", "NULL", "", None])
def test_model_abstention_is_not_an_invented_part_number(value):
    from typing import Literal

    from pydantic import create_model

    from parts_app.agents import Choice

    result_type = create_model(
        "TestChoice", __base__=Choice, candidate_id=(Literal["0", "1"] | None, ...)
    )
    result = result_type(
        candidate_id=value, symbol=value, comment="No suitable candidate", uncertainties=[]
    )
    assert result.candidate_id is None
    assert result.symbol is None
