"""Exercise category/facet/filter recovery at supplier and model boundaries."""

from unittest.mock import Mock, patch

import pytest

from parts_app import tme
from parts_app.agents import CategoryChoice, FilterPlan, ParameterFilter, SearchPlan
from parts_app.sourcing import find_candidates, resolve_filters

FIELDS = {
    "Value": "LED",
    "Reference": "D1",
    "Footprint": "LED_SMD:LED_1206_3216Metric",
    "Final Item Count": "1",
}
FACETS = [
    {
        "name": "Case - inch",
        "id": 2932,
        "values": [{"id": 12, "value": "1206"}, {"id": 8, "value": "0805"}],
    },
    {"name": "Mounting", "id": 10, "values": [{"id": 1, "value": "SMD"}]},
]


def test_mandatory_case_filter_cannot_be_omitted_or_relaxed_by_model():
    plan = FilterPlan(
        filters=[ParameterFilter(parameter="Case - inch", values=["0805"], requirement="wrong")],
        unknowns=[],
        reason="Cheap",
    )
    filters, applied = resolve_filters(FACETS, plan, FIELDS, {})
    assert {"parameter": "Case - inch", "values": ["1206"]} in applied
    assert ("parameters[0][values][]", "12") in filters
    assert ("parameters[0][values][]", "8") not in filters


def test_invented_filter_value_is_rejected():
    plan = FilterPlan(
        filters=[ParameterFilter(parameter="Mounting", values=["invented"], requirement="x")],
        unknowns=[],
        reason="x",
    )
    with pytest.raises(ValueError, match="not available"):
        resolve_filters(FACETS, plan, {}, {})


def test_search_recovers_using_real_failure_feedback_and_keeps_hard_filters():
    catalogue = {"data": {"elements": [{"id": 1, "name": "SMD LEDs", "parent_id": 0}]}}
    discovery = {
        "counters": {"categories": {"elements": [{"id": 1, "products_count": 2}]}},
        "parameters": {"elements": FACETS},
    }
    good = {
        "symbol": "GOOD",
        "parameters": [
            {"name": "Case - inch", "values": [{"value": "1206"}]},
            {"name": "Mounting", "values": [{"value": "SMD"}]},
        ],
    }
    bad = good | {
        "symbol": "BAD",
        "parameters": [{"name": "Case - inch", "values": [{"value": "0805"}]}],
    }
    seen = []

    def respond(output, prompt, payload, **kwargs):
        seen.append(payload)
        if output is CategoryChoice:
            return CategoryChoice(category_id=1, reason="LED category")
        return FilterPlan(filters=[], unknowns=[], reason="Case enforced by Python")

    plan = SearchPlan(
        phrases=["LED 1206", "LED"], requirements=["1206"], unknowns=[], interpretation="LED"
    )
    with (
        patch("parts_app.procedures.access", return_value="token"),
        patch("parts_app.tme.get", return_value=catalogue),
        patch("parts_app.tme.search_catalogue", return_value=discovery),
        patch("parts_app.sourcing.ask", side_effect=respond),
        patch("parts_app.tme.search", side_effect=[[bad], [good]]) as search,
    ):
        candidates, history = find_candidates(FIELDS, plan, {}, Mock())
    assert set(candidates) == {"GOOD"}
    assert len(history) == 2
    assert history[0]["rejected"][0]["symbol"] == "BAD"
    assert seen[2]["previous_attempts"][0]["eligible"] == 0
    assert search.call_args.kwargs["category_id"] == 1
    assert ("parameters[0][values][]", "12") in search.call_args.kwargs["filters"]


def test_api_requests_use_category_filters_and_price_sort_on_every_page():
    filters = [("parameters[0][id]", "2932"), ("parameters[0][values][]", "12")]
    pages = [
        {"data": {"products": {"elements": [{"symbol": str(i)} for i in range(100)]}}},
        {"data": {"products": {"elements": []}}},
    ]
    with patch("parts_app.tme.get", side_effect=pages) as get:
        tme.products("LED", "token", category_id=42, filters=filters)
    assert get.call_count == 2
    for call in get.call_args_list:
        pairs = call.args[2]
        assert ("category_id", "42") in pairs
        assert ("sort[property]", "PRICE_FIRST_QUANTITY") in pairs
        assert all(f in pairs for f in filters)


def test_nominal_value_filter_is_required_even_if_model_omits_it():
    facets = [
        {
            "name": "Resistance",
            "id": 7,
            "values": [{"id": 1, "value": "10kΩ"}, {"id": 2, "value": "47kΩ"}],
        }
    ]
    plan = FilterPlan(filters=[], unknowns=[], reason="No filters")
    filters, _ = resolve_filters(facets, plan, {"Value": "10k", "Reference": "R1"}, {})
    assert ("parameters[0][values][]", "1") in filters
    assert ("parameters[0][values][]", "2") not in filters


def test_minimum_rating_filter_keeps_only_qualifying_values():
    facets = [
        {
            "name": "Voltage",
            "id": 7,
            "values": [
                {"id": 1, "value": "16V"},
                {"id": 2, "value": "25V"},
                {"id": 3, "value": "50V"},
            ],
        }
    ]
    plan = FilterPlan(filters=[], unknowns=[], reason="No filters")
    filters, _ = resolve_filters(facets, plan, {"Minimum Voltage": "25V"}, {})
    assert ("parameters[0][values][]", "1") not in filters
    assert ("parameters[0][values][]", "2") in filters
    assert ("parameters[0][values][]", "3") in filters
