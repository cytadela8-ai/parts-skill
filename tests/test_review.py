"""Review semantics, supplier reads and human replacement behavior."""

from unittest.mock import Mock, patch

import pytest
from test_app import BOM, setup_run

from parts_app.review import ProductCache, compatibility
from parts_app.web import create_app


@pytest.mark.parametrize(
    ("outcomes", "verdict", "expected"),
    [
        (["pass", "not_applicable"], "validated_match", "pass"),
        (["not_applicable"], "validated_match", "attention"),
        (["pass", "unknown", "not_applicable"], "needs_review", "attention"),
        (["pass", "fail", "not_applicable"], "no_safe_match", "fail"),
        (["pass"], "needs_review", "pass"),
    ],
)
def test_compatibility_is_based_on_checks(outcomes, verdict, expected):
    validation = {"checks": [{"outcome": item} for item in outcomes]}
    assert compatibility(validation, verdict) == expected
    assert compatibility(None, verdict) == "not_checked"


def test_agent_uncertainty_needs_attention_but_citation_audit_is_separate():
    validation = {"checks": [{"outcome": "pass"}], "uncertainties": ["Pinout is uncertain."]}
    assert compatibility(validation, "needs_review") == "attention"
    validation["uncertainties"] = ["Application check: evidence reference incomplete."]
    assert compatibility(validation, "needs_review") == "pass"


def test_product_reads_are_cached_and_do_not_edit_human_review(tmp_path):
    app = create_app(tmp_path / "review.sqlite3")
    store = app.extensions["parts_store"]
    project = store.import_csv(BOM)
    row = store.rows(project)[0]
    row = store.edit(row["id"], 0, {"selection": "TEST", "review": "approved"})
    with patch("parts_app.web.fetch", return_value={"symbol": "TEST"}) as fetch:
        client = app.test_client()
        for _ in range(2):
            assert client.get(f"/api/rows/{row['id']}/product").json["product"]["symbol"] == "TEST"
        fetch.assert_called_once_with("TEST", 12, allow_unpriced=True)
    assert store.row(row["id"]) == row
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def test_supplier_error_can_be_retried_without_caching_failure():
    loader = Mock(side_effect=[ValueError("Temporary supplier failure"), {"symbol": "TEST"}])
    cache = ProductCache(loader)
    row = {"selection": "TEST", "fields": {"Final Item Count": "0"}}
    with pytest.raises(ValueError, match="supplier"):
        cache.get(row)
    assert cache.get(row)["symbol"] == "TEST"
    assert loader.call_count == 2
    with pytest.raises(ValueError, match="Choose"):
        cache.get(row | {"selection": ""})


def test_supplier_cache_expires_and_quantities_have_separate_prices():
    loader = Mock(return_value={"symbol": "TEST"})
    cache = ProductCache(loader)
    row = {"selection": "TEST", "fields": {"Final Item Count": "1"}}
    with patch("parts_app.review.time.monotonic", return_value=0):
        cache.get(row)
    with patch("parts_app.review.time.monotonic", return_value=901):
        cache.get(row)
        cache.get(row | {"fields": {"Final Item Count": "2"}})
    assert loader.call_count == 3


def test_saved_checklist_persists_and_input_edit_invalidates_it(tmp_path):
    app = create_app(tmp_path / "checks.sqlite3")
    store = app.extensions["parts_store"]
    worker, row, job, run = setup_run(store)
    validation = {"confidence": 100, "comment": "Compatible", "checks": [{"outcome": "pass"}]}
    worker.publish(
        job,
        run,
        {
            "selection": "TEST",
            "evidence": {},
            "validation": validation,
            "verdict": "validated_match",
        },
    )
    state = app.test_client().get("/api/state").json
    saved = state["rows"][0]
    assert saved["validation"] == validation
    assert saved["compatibility"] == "pass"
    assert "TME Symbol" not in saved["bom"]
    with store.connect() as db:
        db.execute("UPDATE rows SET evidence=json_remove(evidence, '$.validation')")
        for _ in range(205):
            store.event(db, "", "", "extra", {})
    recovered = app.test_client().get("/api/state").json["rows"][0]
    assert recovered["validation"] == validation
    assert recovered["compatibility"] == "pass"
    store.edit(row["id"], saved["revision"], {"fields": {"Value": "20k"}})
    changed = app.test_client().get("/api/state").json["rows"][0]
    assert changed["validation"] is None
    assert changed["compatibility"] == "not_checked"
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def test_bom_spend_includes_old_events_and_excludes_other_boms(tmp_path):
    app = create_app(tmp_path / "cost.sqlite3")
    store = app.extensions["parts_store"]
    first = store.import_csv(BOM)
    second = store.import_csv(BOM)
    with store.connect() as db:
        for project, amount in [(first, 0.002), (second, 0.005)]:
            row = store.rows(project)[0]
            store.event(db, "", row["id"], "model_cost", {"usd": amount})
        for _ in range(205):
            store.event(db, "", "", "extra", {})
    assert app.test_client().get("/api/state").json["spend"] == {first: 0.002, second: 0.005}
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def test_completed_run_controls_cannot_restart_paid_work(tmp_path):
    from parts_app.store import Store

    store = Store(tmp_path / "control.sqlite3")
    worker, _, _, run = setup_run(store)
    with store.connect() as db:
        db.execute("UPDATE runs SET state='completed'")
    for action in ("pause", "resume", "cancel"):
        with pytest.raises(ValueError, match="unavailable"):
            worker.control(run["id"], action)
    worker.pool.shutdown(wait=True)


def test_save_replacement_fetches_and_invalidates_prior_validation(tmp_path):
    app = create_app(tmp_path / "replace.sqlite3")
    store = app.extensions["parts_store"]
    project = store.import_csv(BOM)
    row = store.rows(project)[0]
    row = store.edit(row["id"], 0, {"selection": "OLD"})
    product = {
        "symbol": "NEW",
        "product_url": "https://www.tme.eu/new",
        "unit_price_pln": 1,
        "line_total_pln": 12,
        "order_quantity": 12,
        "stock_quantity": 100,
        "excess_quantity": 0,
    }
    with patch("parts_app.web.fetch", return_value=product):
        response = app.test_client().post(
            f"/api/rows/{row['id']}/substitute",
            json={"symbol": " NEW ", "revision": row["revision"]},
        )
    assert response.status_code == 200
    saved = response.json["row"]
    assert saved["selection"] == "NEW"
    assert saved["review"] == "manually_substituted"
    assert saved["evidence"]["manual_origin"] is True
    assert saved["confidence"] is None
    assert app.test_client().get("/api/state").json["rows"][0]["validation"] is None
    unlocked = store.edit(saved["id"], saved["revision"], {"review": "", "unlock": True})
    assert unlocked["selection"] == "NEW"
    assert unlocked["evidence"]["manual_origin"] is False
    app.extensions["parts_worker"].pool.shutdown(wait=True)
