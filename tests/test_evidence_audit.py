"""Citation errors must not hide independently verified BOM matches."""

from unittest.mock import Mock, patch

import pytest
from test_real_data import complete_validation

from parts_app.procedures import validate_row
from parts_app.validation import audit_checks


def test_invalid_additional_reference_does_not_reject_supported_check():
    result = complete_validation("not_applicable")
    check = result.checks[2]
    check.sources = ["bom.Footprint", "product.parameters.Case - inch", "product.mapping.default"]
    evidence = {
        "bom": {"Footprint": "Inductor_SMD:L_0402_1005Metric"},
        "product": {"parameters": {"Case - inch": "0402"}},
    }
    notes = audit_checks(result, evidence)
    assert check.outcome == "pass"
    assert check.sources == ["bom.Footprint", "product.parameters.Case - inch"]
    assert any("product.mapping.default" in note for note in notes)


@pytest.mark.parametrize("value", [0, 0.0, False])
def test_zero_and_false_are_valid_commercial_evidence(value):
    result = complete_validation()
    result.checks[-1].sources = ["product.excess_quantity"]
    audit_checks(result, {"bom": {}, "product": {"excess_quantity": value}})
    assert result.checks[-1].outcome == "pass"


@pytest.mark.parametrize("value", [None, "", " ", [], {}])
def test_actually_empty_supplier_evidence_stays_unknown(value):
    result = complete_validation()
    result.checks[-1].sources = ["product.stock_quantity"]
    audit_checks(result, {"bom": {}, "product": {"stock_quantity": value}})
    assert result.checks[-1].outcome == "unknown"
    assert "supplier" in result.checks[-1].evidence.lower()
    assert "BOM requirement" not in result.checks[-1].evidence


def test_missing_supplier_reference_is_not_silently_approved():
    result = complete_validation("not_applicable")
    check = result.checks[2]
    check.sources = ["bom.Footprint", "product.missing"]
    audit_checks(result, {"bom": {"Footprint": "0402"}, "product": {}})
    assert check.outcome == "unknown"
    assert "supplier" in check.evidence.lower()
    assert "BOM requirement" not in check.evidence


def test_exact_inductor_checks_recover_incomplete_agent_citations():
    row = {
        "selection": "TEST",
        "fields": {
            "Reference": "L4",
            "Value": "3.9nH",
            "Footprint": "Inductor_SMD:L_0402_1005Metric",
            "Final Item Count": "2",
        },
        "evidence": {
            "product": {
                "symbol": "TEST",
                "parameters": [
                    {"name": name, "values": [{"value": value}]}
                    for name, value in [
                        ("Case - inch", "0402"),
                        ("Case - mm", "1005"),
                        ("Mounting", "SMD"),
                        ("Inductance", "3.9nH"),
                    ]
                ],
            }
        },
    }
    response = complete_validation("not_applicable")
    response.checks[1].outcome = "pass"
    response.checks[1].sources = ["bom.Value"]
    response.checks[2].sources = ["bom.Footprint", "product.mapping.default"]
    with patch("parts_app.procedures.ask", return_value=response):
        validation, _, verdict = validate_row(row, Mock())
    assert verdict == "validated_match"
    assert all(c["outcome"] != "unknown" for c in validation["checks"])
    assert validation["citation_notes"]
    assert validation["model_evaluation"]["checks"][2]["sources"][-1] == "product.mapping.default"


def test_missing_bom_citation_identifies_only_the_missing_side():
    result = complete_validation("not_applicable")
    check = result.checks[0]
    check.outcome = "pass"
    check.sources = ["product.parameters.Type of inductor"]
    audit_checks(
        result,
        {"bom": {"Value": "3.9nH"}, "product": {"parameters": {"Type of inductor": "air coil"}}},
    )
    assert check.outcome == "unknown"
    assert "BOM requirement" in check.evidence
    assert "supplier evidence" not in check.evidence


def test_application_match_does_not_erase_a_deliberate_agent_unknown():
    result = complete_validation("not_applicable")
    check = result.checks[2]
    check.outcome = "unknown"
    check.evidence = "Additional required geometry needs review."
    audit_checks(
        result,
        {
            "bom": {"Footprint": "0402"},
            "product": {},
            "application_checks": [
                {
                    "category": "footprint",
                    "outcome": "pass",
                    "sources": ["bom.Footprint"],
                    "evidence": "Known case matches",
                }
            ],
        },
    )
    assert check.outcome == "unknown"
    assert "geometry" in check.evidence
