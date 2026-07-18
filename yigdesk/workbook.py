"""Synthetic XLSX fixture creation and read-only inspection."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .engine import DealInputs, evaluate


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_workbook(path: Path, scenario: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    inputs = book.active
    inputs.title = "Deal Inputs"
    inputs.append(["Field", "Value", "Unit"])
    inputs.append(["List ARR", Decimal(scenario["list_arr_k"]), "$k"])
    inputs.append(["Discount", Decimal(scenario["current_discount_pct"]), "%"])
    inputs.append(["COGS", None if scenario["cogs_k"] is None else Decimal(scenario["cogs_k"]), "$k"])
    inputs.append(["Margin floor", Decimal(scenario["margin_floor_pct"]), "%"])
    inputs.append(["Requested discount", Decimal(scenario["requested_discount_pct"]), "%"])

    model = book.create_sheet("Deal Model")
    model.append(["Metric", "Engine value", "Formula definition"])
    model.append(["Net ARR", None, "List ARR × (1 − Discount)"])
    model.append(["Gross profit", None, "Net ARR − COGS"])
    model.append(["Gross margin", None, "Gross profit ÷ Net ARR"])
    model.append(["Margin headroom", None, "Gross margin − Margin floor"])

    for sheet in (inputs, model):
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="163D35")
            cell.alignment = Alignment(vertical="center")
        sheet.column_dimensions["A"].width = 24
        sheet.column_dimensions["B"].width = 20
        sheet.column_dimensions["C"].width = 34

    _set_current_model_values(book)
    book.save(path)
    book.close()


def read_inputs(path: Path) -> DealInputs:
    book = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = book["Deal Inputs"]
        cogs = sheet["B4"].value
        return DealInputs(
            list_arr_k=Decimal(str(sheet["B2"].value)),
            current_discount_pct=Decimal(str(sheet["B3"].value)),
            requested_discount_pct=Decimal(str(sheet["B6"].value)),
            cogs_k=None if cogs is None else Decimal(str(cogs)),
            margin_floor_pct=Decimal(str(sheet["B5"].value)),
        )
    finally:
        book.close()


def workbook_snapshot(path: Path) -> dict[str, Any]:
    inputs = read_inputs(path)
    result = evaluate(inputs)
    values = result["values"]
    cells = [
        _cell("Deal Inputs!B2", "List ARR", f"${inputs.list_arr_k:,.0f}k", None, [], ["Deal Model!B2"]),
        _cell("Deal Inputs!B3", "Discount", f"{inputs.current_discount_pct:.1f}%", None, [], ["Deal Model!B2"]),
        _cell(
            "Deal Inputs!B4",
            "COGS",
            "Missing" if inputs.cogs_k is None else f"${inputs.cogs_k:,.0f}k",
            None,
            [],
            ["Deal Model!B3"],
        ),
        _cell("Deal Inputs!B5", "Margin floor", f"{inputs.margin_floor_pct:.1f}%", None, [], ["Deal Model!B5"]),
        _cell("Deal Inputs!B6", "Requested discount", f"{inputs.requested_discount_pct:.1f}%", None, [], ["Deal Model!B2"]),
        _cell(
            "Deal Model!B2",
            "Net ARR",
            f"${Decimal(values['current_net_arr_k']):,.0f}k",
            "List ARR × (1 − Discount)",
            ["Deal Inputs!B2", "Deal Inputs!B3"],
            ["Deal Model!B3", "Deal Model!B4"],
        ),
        _cell(
            "Deal Model!B3",
            "Gross profit",
            "Missing" if inputs.cogs_k is None else f"${Decimal(values['current_net_arr_k']) - inputs.cogs_k:,.0f}k",
            "Net ARR − COGS",
            ["Deal Model!B2", "Deal Inputs!B4"],
            ["Deal Model!B4"],
        ),
        _cell("Deal Model!B4", "Gross margin", _current_margin(inputs), "Gross profit ÷ Net ARR", ["Deal Model!B3", "Deal Model!B2"], ["Deal Model!B5"]),
        _cell("Deal Model!B5", "Margin headroom", _current_headroom(inputs), "Gross margin − Margin floor", ["Deal Model!B4", "Deal Inputs!B5"], []),
    ]
    return {"fingerprint": fingerprint(path), "mode": "read-only", "cells": cells}


def inspect_cell(path: Path, address: str) -> dict[str, Any]:
    cell = next((item for item in workbook_snapshot(path)["cells"] if item["address"] == address), None)
    if cell is None:
        raise ValueError("Object is not present in the synthetic view.")
    return {**cell, "value_verified": True, "source": "synthetic XLSX fixture"}


def _cell(address: str, label: str, value: str, formula: str | None, precedents: list[str], dependents: list[str]) -> dict[str, Any]:
    return {"address": address, "label": label, "value": value, "formula": formula, "precedents": precedents, "dependents": dependents}


def _current_margin(inputs: DealInputs) -> str:
    if inputs.cogs_k is None:
        return "Missing"
    net = inputs.list_arr_k * (1 - inputs.current_discount_pct / 100)
    return f"{(net - inputs.cogs_k) / net * 100:.1f}%"


def _current_headroom(inputs: DealInputs) -> str:
    margin = _current_margin(inputs)
    if margin == "Missing":
        return margin
    return f"{Decimal(margin.removesuffix('%')) - inputs.margin_floor_pct:.1f}%"


def _set_current_model_values(book: Workbook) -> None:
    sheet = book["Deal Inputs"]
    cogs = sheet["B4"].value
    inputs = DealInputs(
        list_arr_k=Decimal(str(sheet["B2"].value)),
        current_discount_pct=Decimal(str(sheet["B3"].value)),
        requested_discount_pct=Decimal(str(sheet["B3"].value)),
        cogs_k=None if cogs is None else Decimal(str(cogs)),
        margin_floor_pct=Decimal(str(sheet["B5"].value)),
    )
    values = evaluate(inputs)["values"]
    model = book["Deal Model"]
    model["B2"] = _as_float(values["proposed_net_arr_k"])
    model["B3"] = _as_float(values["gross_profit_k"])
    model["B4"] = _as_ratio(values["gross_margin_pct"])
    model["B5"] = _as_ratio(values["margin_headroom_pct"])


def _as_float(value: str | None) -> float | None:
    return None if value is None else float(Decimal(value))


def _as_ratio(value: str | None) -> float | None:
    return None if value is None else float(Decimal(value) / 100)
