"""Progress stays observable during blocked requests and never changes row evidence."""

import json
import threading
from unittest.mock import Mock, patch

import pytest
from test_app import setup_run

from parts_app import progress, tme
from parts_app.procedures import Charge
from parts_app.web import create_app


def test_running_job_exposes_live_progress_before_result(tmp_path):
    app = create_app(tmp_path / "progress.sqlite3")
    store = app.extensions["parts_store"]
    worker, row, job, run = setup_run(store)
    entered = threading.Event()
    release = threading.Event()

    def searching(_row, charge):
        progress.step("Searching TME 1/2: 10k resistor")
        charge.reserve(0.001)
        progress.activity("Downloading parameters for 50 parts")
        entered.set()
        assert release.wait(5), "Test did not release simulated supplier request"
        charge.reconcile(0.001, 0.0002)
        return "", {}, "needs_review"

    with store.connect() as db:
        db.execute("UPDATE jobs SET state='running'")
    with patch("parts_app.worker.search_row", side_effect=searching):
        thread = threading.Thread(target=worker.execute, args=(job, run))
        thread.start()
        try:
            assert entered.wait(5)
            state = app.test_client().get("/api/state").json
            running = state["jobs"][0]
            metadata = json.loads(running["result"])
            assert running["state"] == "running"
            assert "Downloading parameters" in metadata["progress"]["message"]
            assert metadata["queued_at"] <= metadata["started_at"] <= metadata["progress"]["at"]
            assert metadata["reserved_cost"] == 0.001
            assert store.row(row["id"])["revision"] == 0
        finally:
            release.set()
            thread.join(5)
    assert not thread.is_alive()
    finished = app.test_client().get("/api/state").json["jobs"][0]
    metadata = json.loads(finished["result"])
    assert finished["state"] == "completed"
    assert metadata["finished_at"] >= metadata["started_at"]
    assert metadata["reserved_cost"] == pytest.approx(0.0002)
    worker.pool.shutdown(wait=True)
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def test_supplier_progress_is_scoped_and_never_includes_token():
    messages = []
    with progress.track(messages.append), patch("parts_app.tme.request", return_value={}):
        progress.step("Searching TME 2/3: capacitor")
        tme.get("/products/parameters", "SECRET-TOKEN", [("symbols[]", "TEST")])
    assert messages[-1] == (
        "Searching TME 2/3: capacitor — Downloading component parameters (1 parts)"
    )
    assert "SECRET-TOKEN" not in str(messages)
    progress.activity("Background browser fetch")
    assert len(messages) == 2


def test_model_rate_limit_reports_wait_and_retry():
    from pydantic_ai.exceptions import ModelRateLimitError

    from parts_app.agents import request_with_backoff

    agent = Mock()
    agent.run_sync.side_effect = [ModelRateLimitError("test-model", "busy"), "success"]
    charge = Mock()
    messages = []
    with progress.track(messages.append), patch("parts_app.agents.time.sleep") as sleep:
        progress.step("Planning TME search")
        assert request_with_backoff(agent, "prompt", 0.001, charge) == "success"
    assert any("rate limit; retrying in 5 seconds" in message for message in messages)
    assert "attempt 2/5" in messages[-1]
    sleep.assert_called_once_with(5)
    charge.reconcile.assert_called_once_with(0.001, 0.0)


def test_activity_trail_is_bounded_without_overwriting_billing(tmp_path):
    app = create_app(tmp_path / "trail.sqlite3")
    store = app.extensions["parts_store"]
    worker, _, job, run = setup_run(store)
    charge = Charge(store, run["id"], job["id"], lambda: None)
    charge.reserve(0.001)
    for index in range(20):
        charge.progress(f"Step {index}")
    with store.connect() as db:
        metadata = json.loads(db.execute("SELECT result FROM jobs").fetchone()[0])
    assert len(metadata["activity"]) == 8
    assert metadata["progress"]["message"] == "Step 19"
    assert metadata["reserved_cost"] == 0.001
    worker.pool.shutdown(wait=True)
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def test_model_schema_repair_reports_what_is_happening():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from parts_app.agents import request_with_backoff

    agent = Mock()
    agent.run_sync.side_effect = [UnexpectedModelBehavior("Missing JSON field"), "fixed"]
    messages = []
    with progress.track(messages.append):
        progress.step("Agent selecting candidate")
        assert request_with_backoff(agent, "prompt", 0.001, Mock()) == "fixed"
    assert any("Repairing model output" in message for message in messages)
    assert "attempt 2/5" in messages[-1]


def test_failed_job_records_end_time_and_last_attempted_step(tmp_path):
    app = create_app(tmp_path / "failed.sqlite3")
    store = app.extensions["parts_store"]
    worker, _, job, run = setup_run(store)

    def failing_search(_row, _charge):
        progress.step("Downloading supplier parameters")
        raise tme.TmeError("Supplier connection timed out")

    with patch("parts_app.worker.search_row", side_effect=failing_search):
        worker.execute(job, run)
    failed = app.test_client().get("/api/state").json["jobs"][0]
    metadata = json.loads(failed["result"])
    assert failed["state"] == "failed"
    assert "timed out" in failed["error"]
    assert metadata["progress"]["message"] == "Downloading supplier parameters"
    assert metadata["finished_at"] >= metadata["started_at"]
    worker.pool.shutdown(wait=True)
    app.extensions["parts_worker"].pool.shutdown(wait=True)
