"""Five-formula synthetic adapter for the public demo.

This deliberately small module is not the Yigrid kernel. It implements only
the fixed arithmetic required by the bundled synthetic story.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any


MONEY = Decimal("0.01")
PERCENT = Decimal("0.1")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _percent(value: Decimal) -> Decimal:
    return value.quantize(PERCENT, rounding=ROUND_HALF_UP)


def _decimal_text(value: Decimal, places: str = "0.000000") -> str:
    return format(value.quantize(Decimal(places), rounding=ROUND_HALF_UP), "f")


def _money_text(value: Decimal | None) -> str:
    if value is None:
        return "Unavailable"
    prefix = "-$" if value < 0 else "$"
    return f"{prefix}{abs(value):,.0f}k"


def _percent_text(value: Decimal | None) -> str:
    return "Unavailable" if value is None else f"{value:.1f}%"


@dataclass(frozen=True)
class DealInputs:
    list_arr_k: Decimal
    current_discount_pct: Decimal
    requested_discount_pct: Decimal
    cogs_k: Decimal | None
    margin_floor_pct: Decimal


def evaluate(inputs: DealInputs) -> dict[str, Any]:
    current_net_arr_exact = inputs.list_arr_k * (
        Decimal("1") - inputs.current_discount_pct / 100
    )
    proposed_net_arr_exact = inputs.list_arr_k * (
        Decimal("1") - inputs.requested_discount_pct / 100
    )
    current_net_arr = _money(current_net_arr_exact)
    proposed_net_arr = _money(proposed_net_arr_exact)
    arr_impact = _money(proposed_net_arr - current_net_arr)
    current_gross_profit_exact = (
        None if inputs.cogs_k is None else current_net_arr_exact - inputs.cogs_k
    )
    current_gross_profit = (
        None if current_gross_profit_exact is None else _money(current_gross_profit_exact)
    )
    current_margin_exact = (
        None
        if current_gross_profit_exact is None or current_net_arr_exact == 0
        else current_gross_profit_exact / current_net_arr_exact * 100
    )
    current_margin = None if current_margin_exact is None else _percent(current_margin_exact)
    current_headroom = None if current_margin is None else _percent(current_margin - inputs.margin_floor_pct)

    gross_profit = None
    gross_margin_pct = None
    margin_headroom_pct = None
    gross_profit_exact = None
    gross_margin_exact = None
    margin_headroom_exact = None
    if inputs.cogs_k is not None and proposed_net_arr_exact != 0:
        gross_profit_exact = proposed_net_arr_exact - inputs.cogs_k
        gross_margin_exact = gross_profit_exact / proposed_net_arr_exact * 100
        margin_headroom_exact = gross_margin_exact - inputs.margin_floor_pct
        gross_profit = _money(gross_profit_exact)
        gross_margin_pct = _percent(gross_margin_exact)
        margin_headroom_pct = _percent(margin_headroom_exact)

    complete = inputs.cogs_k is not None
    constraint_pass = bool(
        complete
        and margin_headroom_exact is not None
        and margin_headroom_exact >= 0
    )
    reviewable = bool(complete and constraint_pass)
    verdict = "READY FOR CFO" if reviewable else "HOLD"
    if reviewable:
        reason = "Evidence is complete and the proposed margin remains above the configured floor."
    elif not complete:
        reason = "Cost evidence is missing, so margin and policy headroom cannot be proven."
    else:
        reason = (
            "Evidence is complete, but the unrounded proposed margin is below the "
            "configured floor."
        )

    evidence_cells = [
        {
            "address": "Deal Inputs!B6",
            "role": "requested discount",
            "before": _percent_text(inputs.requested_discount_pct),
            "after": _percent_text(inputs.requested_discount_pct),
        },
        {
            "address": "Deal Model!B2",
            "role": "net ARR",
            "before": _money_text(current_net_arr),
            "after": _money_text(proposed_net_arr),
        },
        {
            "address": "Deal Model!B3",
            "role": "gross profit",
            "before": _money_text(current_gross_profit),
            "after": _money_text(gross_profit),
        },
        {
            "address": "Deal Model!B4",
            "role": "gross margin",
            "before": _percent_text(current_margin),
            "after": _percent_text(gross_margin_pct),
        },
        {
            "address": "Deal Model!B5",
            "role": "margin headroom",
            "before": _percent_text(current_headroom),
            "after": _percent_text(margin_headroom_pct),
        },
    ]

    return {
        "verdict": verdict,
        "reviewable": reviewable,
        "complete": complete,
        "constraint_pass": constraint_pass,
        "reason": reason,
        "values": {
            "current_net_arr_k": str(current_net_arr),
            "proposed_net_arr_k": str(proposed_net_arr),
            "arr_impact_k": str(arr_impact),
            "gross_profit_k": None if gross_profit is None else str(gross_profit),
            "gross_margin_pct": None if gross_margin_pct is None else str(gross_margin_pct),
            "margin_headroom_pct": None if margin_headroom_pct is None else str(margin_headroom_pct),
        },
        "exact": {
            "constraint_basis": "unrounded_decimal",
            "proposed_net_arr_k": _decimal_text(proposed_net_arr_exact),
            "gross_margin_pct": (
                None if gross_margin_exact is None else _decimal_text(gross_margin_exact)
            ),
            "margin_headroom_pct": (
                None if margin_headroom_exact is None else _decimal_text(margin_headroom_exact)
            ),
        },
        "display": {
            "net_arr": _money_text(proposed_net_arr),
            "arr_impact": _money_text(arr_impact),
            "gross_profit": _money_text(gross_profit),
            "gross_margin": _percent_text(gross_margin_pct),
            "headroom": _percent_text(margin_headroom_pct),
            "requested_discount": _percent_text(inputs.requested_discount_pct),
        },
        "evidence_cells": evidence_cells,
    }


def evaluate_proposal(inputs: DealInputs, requested_discount_pct: Decimal) -> dict[str, Any]:
    """Evaluate one proposal against the same immutable input revision."""

    result = evaluate(replace(inputs, requested_discount_pct=requested_discount_pct))
    return {
        "requested_discount_pct": str(requested_discount_pct),
        "verdict": result["verdict"],
        "reviewable": result["reviewable"],
        "constraint_pass": result["constraint_pass"],
        "display": result["display"],
        "values": result["values"],
        "exact": result["exact"],
    }


def compare_proposals(
    inputs: DealInputs, requested_discounts_pct: list[Decimal]
) -> dict[str, Any]:
    """Compare bounded proposals without selecting a commercial winner."""

    proposals = [
        evaluate_proposal(inputs, discount) for discount in requested_discounts_pct
    ]
    feasible = [item for item in proposals if item["constraint_pass"]]
    return {
        "proposals": proposals,
        "highest_feasible_proposal_pct": (
            None
            if not feasible
            else str(max(Decimal(item["requested_discount_pct"]) for item in feasible))
        ),
        "selection_note": (
            "Financial feasibility is proven here; commercial optimality requires "
            "separate market and customer evidence."
        ),
    }


def find_feasible_boundary(inputs: DealInputs, step_pct: Decimal) -> dict[str, Any]:
    """Return the exact margin boundary and the largest step-aligned safe proposal."""

    if step_pct <= 0 or step_pct > 100:
        raise ValueError("step_pct must be greater than zero and no more than 100")
    if inputs.cogs_k is None:
        return {
            "status": "HOLD",
            "reason": "Cost evidence is missing, so no discount boundary can be proven.",
            "exact_max_discount_pct": None,
            "step_pct": str(step_pct),
            "largest_safe_step_pct": None,
        }
    denominator = Decimal("1") - inputs.margin_floor_pct / 100
    if denominator <= 0 or inputs.list_arr_k <= 0:
        return {
            "status": "HOLD",
            "reason": "The configured inputs do not define a feasible positive-revenue boundary.",
            "exact_max_discount_pct": None,
            "step_pct": str(step_pct),
            "largest_safe_step_pct": None,
        }
    exact_max = (
        Decimal("1") - inputs.cogs_k / (inputs.list_arr_k * denominator)
    ) * 100
    exact_max = min(Decimal("100"), max(Decimal("0"), exact_max))
    safe_step = (exact_max / step_pct).to_integral_value(rounding=ROUND_FLOOR) * step_pct
    return {
        "status": "READY",
        "reason": (
            "The largest safe step is floored from the unrounded margin boundary; "
            "display rounding never determines policy compliance."
        ),
        "exact_max_discount_pct": _decimal_text(exact_max),
        "step_pct": str(step_pct),
        "largest_safe_step_pct": str(safe_step),
        "largest_safe_proposal": evaluate_proposal(inputs, safe_step),
        "first_unsafe_proposal": evaluate_proposal(inputs, safe_step + step_pct),
    }


def stress_test_cogs(
    inputs: DealInputs,
    requested_discount_pct: Decimal,
    cogs_change_pct: Decimal,
) -> dict[str, Any]:
    """Evaluate a proposal with a transparent, non-persistent COGS assumption."""

    if cogs_change_pct < -100 or cogs_change_pct > 1000:
        raise ValueError("cogs_change_pct must be between -100 and 1000")
    if inputs.cogs_k is None:
        return {
            "status": "HOLD",
            "reason": "Base COGS evidence is missing, so a COGS stress test is not grounded.",
            "assumption": {"cogs_change_pct": str(cogs_change_pct)},
            "proposal": None,
        }
    stressed_cogs = inputs.cogs_k * (Decimal("1") + cogs_change_pct / 100)
    proposal = evaluate_proposal(
        replace(inputs, cogs_k=stressed_cogs), requested_discount_pct
    )
    return {
        "status": "READY",
        "assumption": {
            "cogs_change_pct": str(cogs_change_pct),
            "base_cogs_k": str(inputs.cogs_k),
            "stressed_cogs_k": _decimal_text(stressed_cogs),
            "persistent": False,
        },
        "proposal": proposal,
    }
