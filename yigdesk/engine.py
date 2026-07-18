"""Five-formula synthetic adapter for the public demo.

This deliberately small module is not the Yigrid kernel. It implements only
the fixed arithmetic required by the bundled synthetic story.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


MONEY = Decimal("0.01")
PERCENT = Decimal("0.1")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _percent(value: Decimal) -> Decimal:
    return value.quantize(PERCENT, rounding=ROUND_HALF_UP)


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
    current_net_arr = _money(inputs.list_arr_k * (Decimal("1") - inputs.current_discount_pct / 100))
    proposed_net_arr = _money(inputs.list_arr_k * (Decimal("1") - inputs.requested_discount_pct / 100))
    arr_impact = _money(proposed_net_arr - current_net_arr)
    current_gross_profit = None if inputs.cogs_k is None else _money(current_net_arr - inputs.cogs_k)
    current_margin = (
        None if current_gross_profit is None or current_net_arr == 0 else _percent(current_gross_profit / current_net_arr * 100)
    )
    current_headroom = None if current_margin is None else _percent(current_margin - inputs.margin_floor_pct)

    gross_profit = None
    gross_margin_pct = None
    margin_headroom_pct = None
    if inputs.cogs_k is not None and proposed_net_arr != 0:
        gross_profit = _money(proposed_net_arr - inputs.cogs_k)
        gross_margin_pct = _percent(gross_profit / proposed_net_arr * 100)
        margin_headroom_pct = _percent(gross_margin_pct - inputs.margin_floor_pct)

    complete = inputs.cogs_k is not None
    constraint_pass = complete and margin_headroom_pct is not None and margin_headroom_pct >= 0
    reviewable = bool(complete and constraint_pass)
    verdict = "READY FOR CFO" if reviewable else "HOLD"
    reason = (
        "Evidence is complete and the proposed margin remains above the configured floor."
        if reviewable
        else "Cost evidence is missing, so margin and policy headroom cannot be proven."
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
