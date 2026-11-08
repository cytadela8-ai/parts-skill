"""Every selected row has an explicit action, and combined runs preserve manual parts."""

from unittest.mock import patch

import pytest

from parts_app.web import create_app


@pytest.fixture
def mixed_app(tmp_path):
    app = create_app(tmp_path / "selected.sqlite3")
    store = app.extensions["parts_store"]
    bom = "Reference,Value,Footprint,Qty\n" + "".join(f"R{i},10k,0805,1\n" for i in range(1, 6))
    project = store.import_csv(bom.encode())
    rows = store.rows(project)
    for index, review in ((1, "manually_substituted"), (2, "approved"), (4, "")):
        store.edit(rows[index]["id"], 0, {"selection": f"MANUAL-{index}", "review": review})
    store.edit(rows[3]["id"], 0, {"review": "not needed"})
    with store.connect() as db:
        db.execute(
            "UPDATE rows SET evidence='{" + '"manual_origin":true' + "}' WHERE id=?",
            (rows[4]["id"],),
        )
    yield app
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def validation(row, _charge):
    return {"confidence": 80, "comment": "Checked", "checks": []}, None, "needs_review"


def test_combined_preview_identifies_every_selected_action(mixed_app):
    rows = mixed_app.extensions["parts_store"].rows()
    response = mixed_app.test_client().post(
        "/api/runs/preview", json={"rows": [row["id"] for row in rows], "procedure": "both"}
    )
    assert response.status_code == 200
    plan = response.json["rows"]
    assert [row["procedure"] for row in plan] == [
        "both",
        "validate",
        "validate",
        "skip",
        "validate",
    ]
    assert "not needed" in plan[3]["reason"]
    assert "Keep protected selection" in plan[1]["reason"]


def test_search_keeps_all_five_rows_in_run_and_explains_four_skips(mixed_app):
    store = mixed_app.extensions["parts_store"]
    worker = mixed_app.extensions["parts_worker"]
    original = store.rows()
    with patch.object(worker.pool, "submit"):
        run_id = worker.start([row["id"] for row in original], "search", 0.04)
    with patch(
        "parts_app.worker.search_row", return_value=("FOUND", {}, "candidate_unreviewed")
    ) as search:
        worker.dispatch(run_id)
    state = mixed_app.test_client().get("/api/state").json
    jobs = [job for job in state["jobs"] if job["run"] == run_id]
    assert len(jobs) == 5
    assert sum(job["state"] == "completed" for job in jobs) == 1
    assert sum(job["state"] == "skipped" for job in jobs) == 4
    assert search.call_count == 1
    for expected in original[1:]:
        assert store.row(expected["id"]) == expected


def test_combined_run_searches_one_validates_four_and_preserves_manual_parts(mixed_app):
    store = mixed_app.extensions["parts_store"]
    worker = mixed_app.extensions["parts_worker"]
    rows = store.rows()
    with patch.object(worker.pool, "submit"):
        run_id = worker.start([row["id"] for row in rows], "both", 0.04)
    with (
        patch(
            "parts_app.worker.search_row", return_value=("FOUND", {}, "candidate_unreviewed")
        ) as search,
        patch("parts_app.worker.validate_row", side_effect=validation) as validate,
    ):
        worker.dispatch(run_id)
    assert search.call_count == 1
    assert validate.call_count == 4
    for index in (1, 2, 4):
        updated = store.row(rows[index]["id"])
        assert updated["selection"] == rows[index]["selection"]
        assert updated["review"] == rows[index]["review"]
    assert store.row(rows[3]["id"]) == rows[3]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'").fetchone()[0] == 4
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE state='skipped'").fetchone()[0] == 1


def test_five_eligible_rows_are_all_processed(mixed_app):
    store = mixed_app.extensions["parts_store"]
    worker = mixed_app.extensions["parts_worker"]
    with store.connect() as db:
        db.execute("UPDATE rows SET review='',verdict='',evidence='{}'")
    with patch.object(worker.pool, "submit"):
        run_id = worker.start([row["id"] for row in store.rows()], "search", 0.04)
    with patch(
        "parts_app.worker.search_row", return_value=("FOUND", {}, "candidate_unreviewed")
    ) as search:
        worker.dispatch(run_id)
    assert search.call_count == 5
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'").fetchone()[0] == 5
        assert (
            db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()[0] == "completed"
        )
