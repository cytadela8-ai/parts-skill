"""Known BOM conflicts must survive both agent optimism and search recovery."""

from unittest.mock import Mock, patch

import pytest
from test_real_data import complete_validation

from parts_app.procedures import validate_row
from parts_app.requirements import footprint_check, requirement_checks


def product(**parameters):
    return {
        "symbol": "TEST",
        "parameters": [
            {"name": name, "values": [{"value": value}]} for name, value in parameters.items()
        ],
    }


@pytest.mark.parametrize("prefix", ["LED_SMD:LED", "Resistor_SMD:R", "Capacitor_SMD:C"])
def test_wrong_inch_or_metric_chip_case_fails(prefix):
    fields = {"Footprint": f"{prefix}_1206_3216Metric"}
    for parameters in [{"Case - inch": "0805"}, {"Case - mm": "2012"}]:
        assert footprint_check(fields, product(**parameters), {})["outcome"] == "fail"
    assert footprint_check(fields, product(), {})["outcome"] == "unknown"


def test_documented_alternatives_keep_missing_distinct_from_conflicting():
    fields = {"Footprint": "Capacitor_SMD:CP_Elec_5x4.5"}
    options = {
        "small": [("numeric_mm", "Diameter", "5")],
        "large": [("numeric_mm", "Diameter", "6.3")],
    }
    assert (
        footprint_check(fields, product(Diameter="6.3mm", Mounting="SMT"), options)["outcome"]
        == "pass"
    )
    assert (
        footprint_check(fields, product(Diameter="8mm", Mounting="SMD"), options)["outcome"]
        == "fail"
    )


@pytest.mark.parametrize(
    ("field", "expected", "actual", "outcome"),
    [
        ("Voltage", "25V", "16V", "fail"),
        ("Minimum Voltage", "25V", "50V", "pass"),
        ("Voltage", "25V", "50V", "fail"),
        ("LED colour", "green", "red", "fail"),
        ("Tolerance", "1%", "5%", "fail"),
        ("Voltage", "25V", "10...50V", "unknown"),
    ],
)
def test_explicit_requirements_are_compared_without_inventing_minimums(
    field, expected, actual, outcome
):
    parameter = field.removeprefix("Minimum ")
    checks = requirement_checks({field: expected}, product(**{parameter: actual}), {})
    assert checks[-1]["outcome"] == outcome


def test_overconfident_agent_cannot_hide_led_footprint_failure():
    row = {
        "selection": "TEST",
        "fields": {
            "Reference": "D1",
            "Value": "LED",
            "Footprint": "LED_SMD:LED_1206_3216Metric",
            "Final Item Count": "1",
        },
        "evidence": {"product": product(**{"Case - inch": "0805", "Mounting": "SMD"})},
    }
    with patch("parts_app.procedures.ask", return_value=complete_validation()):
        result, _, verdict = validate_row(row, Mock())
    assert verdict == "no_safe_match"
    assert result["confidence"] <= 20
    assert result["likely_bad"] is True
    assert any(c["category"] == "footprint" and c["outcome"] == "fail" for c in result["checks"])


def test_numeric_range_is_not_silently_treated_as_its_first_value():
    from parts_app.requirements import compare_value, requested_value

    assert requested_value({"Reference": "C1", "Value": "200-600pF"}) is None
    assert (
        compare_value({"Reference": "C1", "Value": "200pF"}, product(Capacitance="200...600pF"))[
            "outcome"
        ]
        == "unknown"
    )


def test_irrelevant_led_pinout_sources_cannot_pass_or_be_not_applicable():
    from parts_app.validation import audit_checks

    evidence = {
        "bom": {"Value": "LED", "Footprint": "LED_SMD:LED_1206_3216Metric"},
        "product": {"parameters": {"Front": "flat"}},
    }
    for outcome in ["pass", "not_applicable"]:
        result = complete_validation()
        check = result.checks[3]
        check.outcome = outcome
        check.sources = ["bom.Footprint", "product.parameters.Front"]
        audit_checks(result, evidence)
        assert check.outcome == "unknown"


def test_generic_component_cannot_inherit_ratings_or_technology_from_supplier():
    from parts_app.validation import audit_checks

    result = complete_validation()
    evidence = {
        "bom": {"Value": "LED", "Footprint": "LED_SMD:LED_1206_3216Metric"},
        "product": {"parameters": {"Operating voltage": "3.5V"}},
    }
    for index in [4, 5, 6]:
        result.checks[index].outcome = "pass"
        result.checks[index].sources = ["bom.Value", "product.parameters.Operating voltage"]
    audit_checks(result, evidence)
    assert all(result.checks[i].outcome == "not_applicable" for i in [4, 5, 6])


def test_explicit_custom_requirements_are_not_erased_as_generic():
    from parts_app.validation import audit_checks

    result = complete_validation()
    result.checks[4].outcome = "unknown"
    audit_checks(
        result,
        {"bom": {"Value": "LED", "Comment": "Needs 50mA current"}, "product": {"parameters": {}}},
    )
    assert result.checks[4].outcome == "unknown"


def test_missing_required_nominal_value_stays_unknown_for_generic_resistor():
    checks = requirement_checks({"Reference": "R1", "Value": "R"}, product(Resistance="10kΩ"), {})
    assert any(c["category"] == "value" and c["outcome"] == "unknown" for c in checks)


def test_missing_evidence_cannot_produce_a_green_ui_status():
    from parts_app.agents import Validation
    from parts_app.procedures import finalize_validation
    from parts_app.review import compatibility

    result = Validation(
        confidence=100,
        likely_bad=False,
        comment="Everything matches",
        uncertainties=[],
        checks=[
            {
                "category": "type",
                "requirement": "LED",
                "outcome": "pass",
                "sources": ["bom.Value", "product.description"],
                "evidence": "LED",
            }
        ],
    )
    evidence = {
        "bom": {"Value": "LED"},
        "product": {"description": "LED"},
        "deterministic_footprint_match": True,
        "deterministic_value_check": {"outcome": "unknown"},
    }
    saved, _, verdict = finalize_validation(result, evidence, {}, [])
    assert compatibility(saved, verdict) == "attention"
    assert saved["confidence"] <= 60


def test_not_applicable_source_paths_do_not_downgrade_compatibility():
    from parts_app.procedures import checklist_complete

    validation = complete_validation("not_applicable")
    validation.checks[4].sources = ["product.parameters.Not provided"]
    evidence = {"bom": {"Footprint": "0805"}, "product": {"footprint_match": True}}
    assert checklist_complete(validation, evidence)


def test_validator_cannot_approve_an_explicit_rating_conflict():
    row = {
        "selection": "TEST",
        "fields": {
            "Reference": "C1",
            "Value": "10uF",
            "Footprint": "0805",
            "Voltage": "25V",
            "Final Item Count": "1",
        },
        "evidence": {"product": product(Voltage="16V", Capacitance="10uF")},
    }
    with patch("parts_app.procedures.ask", return_value=complete_validation()):
        result, _, verdict = validate_row(row, Mock())
    assert verdict == "no_safe_match"
    assert result["confidence"] <= 20
    assert any(c["category"] == "ratings" and c["outcome"] == "fail" for c in result["checks"])
