"""Row mapping approval changes which geometry requirements the next validation uses."""

from unittest.mock import Mock, patch

import pytest
from test_real_data import complete_validation

from parts_app.procedures import validate_row
from parts_app.web import create_app


@pytest.fixture
def mapping_app(tmp_path):
    app = create_app(tmp_path / "mapping.sqlite3")
    store = app.extensions["parts_store"]
    project = store.import_csv(b"Reference,Value,Footprint,Qty\nR1,10k,Custom:Chip,1\n")
    row = store.rows(project)[0]
    parameters = [
        {"name": name, "values": [{"value": value}]}
        for name, value in [("Case - inch", "0805"), ("Mounting", "SMD"), ("Resistance", "10kΩ")]
    ]
    row = store.edit(
        row["id"],
        row["revision"],
        {
            "selection": "TEST",
            "evidence": {
                "product": {"symbol": "TEST", "parameters": parameters},
                "proposed_mapping": {
                    "row": [["exact", "Case - inch", "0805"], ["exact", "Mounting", "SMD"]]
                },
            },
        },
    )
    yield app, store, row
    app.extensions["parts_worker"].pool.shutdown(wait=True)


def test_accept_mapping_is_row_local_and_requires_an_explicit_validation_run(mapping_app):
    app, store, row = mapping_app
    with store.connect() as db:
        db.execute("UPDATE rows SET review='approved', confidence=95 WHERE id=?", (row["id"],))
    row = store.row(row["id"])
    with patch("parts_app.procedures.ask", return_value=complete_validation("not_applicable")):
        _, _, before = validate_row(row, Mock())
    assert before == "needs_review"
    with patch("parts_app.procedures.ask") as ask:
        response = app.test_client().post(
            f"/api/rows/{row['id']}/mapping", json={"revision": row["revision"]}
        )
    assert response.status_code == 200
    ask.assert_not_called()
    accepted = store.row(row["id"])
    assert accepted["evidence"]["mapping_approved"] is True
    assert accepted["selection"] == row["selection"]
    assert accepted["review"] == ""
    assert accepted["confidence"] is None
    assert "validation" not in accepted["evidence"]
    with patch("parts_app.procedures.ask", return_value=complete_validation("not_applicable")):
        _, _, after = validate_row(accepted, Mock())
    assert after == "validated_match"
    edited = store.edit(accepted["id"], accepted["revision"], {"fields": {"Footprint": "Other"}})
    assert not edited["evidence"].get("mapping_approved")


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("Case - inch", "1206", "do not match"),
        ("Height", "1mm", "No acceptable"),
    ],
)
def test_approval_rejects_nonmatching_or_disallowed_constraints(mapping_app, name, value, error):
    app, store, row = mapping_app
    evidence = row["evidence"] | {"proposed_mapping": {"row": [["exact", name, value]]}}
    row = store.edit(row["id"], row["revision"], {"evidence": evidence})
    response = app.test_client().post(
        f"/api/rows/{row['id']}/mapping", json={"revision": row["revision"]}
    )
    assert response.status_code == 422
    assert error in response.get_json()["error"]
    assert not store.row(row["id"])["evidence"].get("mapping_approved")
