from decimal import Decimal

from yigdesk.engine import DealInputs, evaluate


def test_complete_evidence_produces_exact_demo_values_and_five_bound_cells():
    result = evaluate(
        DealInputs(
            list_arr_k=Decimal("1000"),
            current_discount_pct=Decimal("10"),
            requested_discount_pct=Decimal("12"),
            cogs_k=Decimal("480"),
            margin_floor_pct=Decimal("40"),
        )
    )

    assert result["verdict"] == "READY FOR CFO"
    assert result["reviewable"] is True
    assert result["values"]["proposed_net_arr_k"] == "880.00"
    assert result["values"]["arr_impact_k"] == "-20.00"
    assert result["display"]["arr_impact"] == "-$20k"
    assert result["values"]["gross_margin_pct"] == "45.5"
    assert result["values"]["margin_headroom_pct"] == "5.5"
    assert [cell["address"] for cell in result["evidence_cells"]] == [
        "Deal Inputs!B6",
        "Deal Model!B2",
        "Deal Model!B3",
        "Deal Model!B4",
        "Deal Model!B5",
    ]
    assert next(cell for cell in result["evidence_cells"] if cell["address"] == "Deal Model!B2") == {
        "address": "Deal Model!B2",
        "role": "net ARR",
        "before": "$900k",
        "after": "$880k",
    }


def test_missing_cost_evidence_holds_without_fabricating_values():
    result = evaluate(
        DealInputs(
            list_arr_k=Decimal("1000"),
            current_discount_pct=Decimal("10"),
            requested_discount_pct=Decimal("12"),
            cogs_k=None,
            margin_floor_pct=Decimal("40"),
        )
    )

    assert result["verdict"] == "HOLD"
    assert result["reviewable"] is False
    assert result["values"]["gross_margin_pct"] is None
    assert result["display"]["gross_margin"] == "Unavailable"
    assert next(cell for cell in result["evidence_cells"] if cell["address"] == "Deal Model!B4")["after"] == "Unavailable"
