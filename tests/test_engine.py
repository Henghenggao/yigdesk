from decimal import Decimal

from yigdesk.engine import (
    DealInputs,
    compare_proposals,
    evaluate,
    find_feasible_boundary,
    stress_test_cogs,
)


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


def northwind_inputs():
    return DealInputs(
        list_arr_k=Decimal("14658.2"),
        current_discount_pct=Decimal("0"),
        requested_discount_pct=Decimal("2"),
        cogs_k=Decimal("10031.0"),
        margin_floor_pct=Decimal("30"),
    )


def test_exact_margin_not_rounded_display_controls_the_constraint():
    safe = evaluate(
        DealInputs(**{**northwind_inputs().__dict__, "requested_discount_pct": Decimal("2.23")})
    )
    unsafe = evaluate(
        DealInputs(**{**northwind_inputs().__dict__, "requested_discount_pct": Decimal("2.24")})
    )

    assert safe["constraint_pass"] is True
    assert unsafe["constraint_pass"] is False
    assert unsafe["display"]["gross_margin"] == "30.0%"
    assert Decimal(unsafe["exact"]["gross_margin_pct"]) < Decimal("30")
    assert "unrounded" in unsafe["reason"]


def test_boundary_floors_to_the_largest_safe_increment_and_rechecks_both_sides():
    boundary = find_feasible_boundary(northwind_inputs(), Decimal("0.01"))

    assert boundary["exact_max_discount_pct"] == "2.239020"
    assert boundary["largest_safe_step_pct"] == "2.23"
    assert boundary["largest_safe_proposal"]["constraint_pass"] is True
    assert boundary["first_unsafe_proposal"]["requested_discount_pct"] == "2.24"
    assert boundary["first_unsafe_proposal"]["constraint_pass"] is False


def test_comparison_does_not_mislabel_financial_feasibility_as_commercial_optimum():
    comparison = compare_proposals(
        northwind_inputs(), [Decimal("2"), Decimal("2.23"), Decimal("2.24")]
    )

    assert comparison["highest_feasible_proposal_pct"] == "2.23"
    assert "commercial optimality" in comparison["selection_note"]


def test_stress_test_is_explicit_and_non_persistent():
    result = stress_test_cogs(
        northwind_inputs(), Decimal("2"), Decimal("5")
    )

    assert result["assumption"]["persistent"] is False
    assert result["assumption"]["base_cogs_k"] == "10031.0"
    assert result["proposal"]["verdict"] == "HOLD"
    assert northwind_inputs().cogs_k == Decimal("10031.0")
