"""Behavior checks for imports, row control, agent findings, and spending boundaries."""

import io
from unittest.mock import Mock, patch

import pytest

from parts_app.agents import Choice, SearchPlan, Validation
from parts_app.procedures import Charge, search_row, validate_row
from parts_app.store import Store
from parts_app.web import create_app
from parts_app.worker import Worker

BOM = b"Reference,Value,Footprint,Qty\nR1,10k,Resistor_SMD:R_0805_2012Metric,10\n"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "parts.sqlite3")


def test_import_preserves_provenance_and_roundtrip(store):
    project = store.import_csv(BOM)
    row = store.rows(project)[0]
    assert row["fields"]["Final Item Count"] == "12"
    store.edit(row["id"], 0, {"selection": "TEST"})
    imported = store.import_csv(store.export_csv(project).encode())
    assert store.rows(imported)[0]["verdict"] == "candidate_unreviewed"
    assert store.rows(imported)[0]["selection"] == "TEST"


@pytest.mark.parametrize(
    "content",
    [
        b"Reference,Value,Footprint,Qty\nR1,10k,0805,0\n",
        b"Reference,Value,Footprint,Qty\nR1,10k,0805,1,extra\n",
        b"Reference,Value,Footprint,Qty\n",
        b"Reference,Value,Qty\nR1,10k,1\n",
        b"Reference,Value,Footprint,Qty,Qty\nR1,10k,0805,1,1\n",
    ],
)
def test_malformed_import_is_atomic(store, content):
    with pytest.raises(ValueError):
        store.import_csv(content)
    assert store.rows() == []


def test_semicolon_grouped_and_manual_import(store):
    project = store.import_csv(
        (
            "\ufeffReference;Value;Footprint;Qty;TME_SYMBOL;Opis\n"
            '"R1,R2";10k;0805;2;MANUAL;"resistor; note"\n'
        ).encode()
    )
    row = store.rows(project)[0]
    assert row["verdict"] == "manually_provided"
    assert row["fields"]["Opis"] == "resistor; note"


def test_human_revision_and_basket_protection(store):
    project = store.import_csv(BOM)
    row = store.rows(project)[0]
    with pytest.raises(ValueError, match="Approve"):
        store.export_csv(project, True)
    store.edit(row["id"], 0, {"selection": "TEST"})
    with pytest.raises(ValueError, match="changed"):
        store.edit(row["id"], 0, {"review": "approved"})
    store.edit(row["id"], 1, {"review": "approved"})
    assert "TEST,12" in store.export_csv(project, True)
    changed = store.edit(row["id"], 2, {"fields": {"Value": "20k"}})
    assert changed["review"] == ""
    assert changed["verdict"] == "candidate_unreviewed"


def test_not_needed_and_missing_symbol_approval(store):
    project = store.import_csv(BOM)
    row = store.rows(project)[0]
    with pytest.raises(ValueError, match="Choose"):
        store.edit(row["id"], 0, {"review": "approved"})
    updated = store.edit(row["id"], 0, {"review": "not needed"})
    assert updated["fields"]["Final Item Count"] == "0"
    assert "R1" not in store.export_csv(project, True)


def setup_run(store):
    project = store.import_csv(BOM)
    row = store.rows(project)[0]
    worker = Worker(store)
    with patch.object(worker.pool, "submit"):
        worker.start([row["id"]], "both", 0.008)
    with store.connect() as db:
        job = dict(db.execute("SELECT * FROM jobs").fetchone())
        run = dict(db.execute("SELECT * FROM runs").fetchone())
    return worker, row, job, run


def test_budget_is_atomic_and_unknown_charge_stays_reserved(store):
    worker, _, job, run = setup_run(store)
    charge = Charge(store, run["id"], job["id"], lambda: worker.active(run["id"]))
    charge.reserve(0.006)
    with pytest.raises(ValueError, match="allowance"):
        charge.reserve(0.003)
    with store.connect() as db:
        state = dict(db.execute("SELECT * FROM runs").fetchone())
    assert state["reserved"] == 0.006
    charge.reconcile(0.006, 0.001)
    with store.connect() as db:
        assert db.execute("SELECT spent FROM runs").fetchone()[0] == 0.001


def test_publish_never_overwrites_manual_changes(store):
    worker, row, job, run = setup_run(store)
    store.edit(row["id"], 0, {"selection": "MANUAL"})
    worker.publish(job, run, {"selection": "AGENT", "evidence": {}})
    assert store.row(row["id"])["selection"] == "MANUAL"
    with store.connect() as db:
        assert db.execute("SELECT state FROM jobs").fetchone()[0] == "stale"
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_validation_reports_low_confidence_without_replacing_part(store):
    project = store.import_csv(BOM)
    row = store.rows(project)[0] | {
        "selection": "TEST",
        "evidence": {
            "product": {
                "symbol": "TEST",
                "parameters": [],
                "needs_attention": False,
            }
        },
    }
    verdict = Validation(
        confidence=12,
        comment="Likely wrong value.",
        likely_bad=True,
        uncertainties=["Missing pinout"],
        checks=[
            {
                "requirement": "value",
                "outcome": "fail",
                "evidence": "Value differs",
            }
        ],
    )
    with patch("parts_app.procedures.ask", return_value=verdict):
        result, _, status = validate_row(row, Mock())
    assert status == "no_safe_match"
    assert result["confidence"] == 12
    assert row["selection"] == "TEST"


def test_search_preserves_tentative_candidate(store):
    project = store.import_csv(BOM)
    row = store.rows(project)[0]
    plan = SearchPlan(
        phrases=["resistor", "10k resistor"],
        requirements=["10k"],
        unknowns=["Power"],
        interpretation="Generic",
    )
    choice = Choice(symbol="TEST", comment="Tentative", uncertainties=["Package unconfirmed"])
    with (
        patch("parts_app.procedures.ask", side_effect=[plan, choice]),
        patch("parts_app.procedures.access", return_value="token"),
        patch("parts_app.tme.search", return_value=[{"symbol": "TEST"}]),
    ):
        symbol, evidence, status = search_row(row, Mock())
    assert symbol == "TEST"
    assert status == "candidate_unreviewed"
    assert evidence["choice"]["uncertainties"]
    assert len(evidence["queries"]) == 2


def test_web_import_export_and_origin_protection(tmp_path):
    app = create_app(tmp_path / "app.sqlite3")
    client = app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    response = client.post("/api/import", data={"file": (io.BytesIO(BOM), "bom.csv")})
    assert response.status_code == 200
    project = response.json["project"]
    assert client.get(f"/api/export/{project}.csv").status_code == 200
    assert client.get("/api/backup").data.startswith(b"SQLite format 3")
    assert (
        client.post("/api/runs", json={}, headers={"Origin": "https://evil.test"}).status_code
        == 403
    )
    assert client.get("/", headers={"Host": "evil.test"}).status_code == 403
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def test_restart_does_not_auto_resume_paid_work(store):
    _, _, _, _ = setup_run(store)
    Store(store.path)
    with store.connect() as db:
        assert db.execute("SELECT state FROM runs").fetchone()[0] == "interrupted"


def test_canceled_result_retained_but_not_published(store):
    worker, row, job, run = setup_run(store)
    worker.control(run["id"], "cancel")
    worker.publish(job, run, {"selection": "LATE", "evidence": {}})
    assert store.row(row["id"])["selection"] == ""


def test_confident_incomplete_checklist_stays_needs_review(store):
    project = store.import_csv(BOM)
    row = store.rows(project)[0] | {
        "selection": "TEST",
        "evidence": {
            "product": {
                "symbol": "TEST",
                "parameters": [],
                "needs_attention": False,
            }
        },
    }
    response = Validation(
        confidence=100,
        comment="Looks good",
        likely_bad=False,
        uncertainties=[],
        checks=[
            {"requirement": "value", "outcome": "pass", "evidence": "10k", "sources": ["bom.Value"]}
        ],
    )
    with (
        patch("parts_app.procedures.ask", return_value=response),
        patch("parts_app.tme.has_footprint_options", return_value=True),
    ):
        _, _, verdict = validate_row(row, Mock())
    assert verdict == "needs_review"


def test_combined_run_keeps_search_when_validation_fails(store):
    worker, row, job, run = setup_run(store)
    with (
        patch(
            "parts_app.worker.search_row", return_value=("TENTATIVE", {}, "candidate_unreviewed")
        ),
        patch("parts_app.worker.validate_row", side_effect=ValueError("Model unavailable")),
    ):
        worker.execute(job, run)
    saved = store.row(row["id"])
    assert saved["selection"] == "TENTATIVE"
    assert saved["verdict"] == "candidate_unreviewed"
    with store.connect() as db:
        assert db.execute("SELECT state FROM jobs").fetchone()[0] == "failed"


def test_import_preview_does_not_create_project(tmp_path):
    app = create_app(tmp_path / "preview.sqlite3")
    client = app.test_client()
    response = client.post("/api/import?preview=1", data={"file": (io.BytesIO(BOM), "bom.csv")})
    assert response.status_code == 200
    assert len(response.json["rows"]) == 1
    assert client.get("/api/state").json["rows"] == []
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def test_tme_search_examines_bounded_pages():
    from parts_app import tme

    pages = [
        {"data": {"products": {"elements": [{"symbol": str(i)} for i in range(100)]}}},
        {"data": {"products": {"elements": [{"symbol": str(i)} for i in range(100, 200)]}}},
        {"data": {"products": {"elements": [{"symbol": "last"}]}}},
    ]
    with patch("parts_app.tme.get", side_effect=pages) as get:
        result = tme.products("resistor", "", "token", 100)
    assert len(result) == 201
    assert get.call_count == 3
    assert ("page", "3") in get.call_args.args[2]


def test_commercial_ranking_preserves_twenty_five_percent_policy():
    from parts_app import tme

    candidates = [
        {"symbol": "CHEAP", "order_quantity": 10, "unit_price_pln": 1},
        {"symbol": "SURPLUS", "order_quantity": 25, "unit_price_pln": 0.5},
        {"symbol": "EXPENSIVE", "order_quantity": 100, "unit_price_pln": 0.126},
    ]
    assert tme.select_preferred_candidate(candidates)["symbol"] == "SURPLUS"


def test_count_change_refetches_adjusted_order_quantity(tmp_path):
    app = create_app(tmp_path / "count.sqlite3")
    store = app.extensions["parts_store"]
    project = store.import_csv(BOM)
    row = store.rows(project)[0]
    store.edit(row["id"], 0, {"selection": "TEST"})
    product = {
        "symbol": "TEST",
        "product_url": "https://www.tme.eu/test",
        "unit_price_pln": 0.1,
        "line_total_pln": 2.5,
        "order_quantity": 25,
        "stock_quantity": 100,
        "excess_quantity": 12,
    }
    with patch("parts_app.web.fetch", return_value=product) as fetch:
        response = app.test_client().patch(
            "/api/rows/" + row["id"],
            json={
                "revision": 1,
                "fields": {"Final Item Count": "13"},
            },
        )
    assert response.status_code == 200
    assert response.json["row"]["fields"]["Order Qty"] == "25"
    fetch.assert_called_once_with("TEST", 13)
    app.extensions["parts_worker"].pool.shutdown(wait=True)


@pytest.mark.parametrize("filename", ["VFD-disp-tme-results-final.csv", "tme-results-final.csv"])
def test_real_reviewed_csv_roundtrip(store, filename):
    import csv
    from pathlib import Path

    content = (Path(__file__).parent / "fixtures" / filename).read_bytes()
    original = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    project = store.import_csv(content)
    imported = store.rows(project)
    assert len(imported) == len(original)
    for expected, actual in zip(original, imported, strict=True):
        assert actual["selection"] == expected["TME Symbol"]
        assert actual["fields"]["Final Item Count"] == expected["Final Item Count"]
        assert actual["fields"]["Reference"] == expected["Reference"]
    exported = list(csv.DictReader(io.StringIO(store.export_csv(project))))
    for expected, actual in zip(original, exported, strict=True):
        assert expected.items() <= actual.items()


def test_remote_host_binding_preserves_origin_protection(tmp_path):
    app = create_app(tmp_path / "remote.sqlite3", allow_remote=True)
    client = app.test_client()
    assert client.get("/", headers={"Host": "192.168.1.50:5050"}).status_code == 200
    response = client.post(
        "/api/import",
        headers={
            "Host": "192.168.1.50:5050",
            "Origin": "https://other.test",
        },
    )
    assert response.status_code == 403
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def test_agents_never_receive_previous_review_answers():
    from parts_app.procedures import bom_fields

    result = bom_fields(
        {
            "Value": "10k",
            "Opis": "Resistor",
            "TME Symbol": "GOLD",
            "TME Match Notes": "Human approved",
            "Validation Confidence": 100,
        }
    )
    assert result == {"Value": "10k", "Opis": "Resistor"}
