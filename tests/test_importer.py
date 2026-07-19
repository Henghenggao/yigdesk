from __future__ import annotations

from decimal import Decimal

import openpyxl
import pytest

from scripts.generate_sample_workbook import generate
from yigdesk.importer import WorkbookImportError, import_synthetic_workbook


def test_importer_extracts_real_quarter_cells_and_preserves_source_bytes(tmp_path):
    path = generate(tmp_path / "northwind.xlsx")
    before = path.read_bytes()

    imported = import_synthetic_workbook(
        path,
        original_filename="northwind.xlsx",
        requested_discount_pct=Decimal("2"),
        margin_floor_pct=Decimal("30"),
    )

    assert imported.scenario["list_arr_k"] == "14658.2"
    assert imported.scenario["cogs_k"] == "10031.0"
    assert imported.source["analysis_bytes_unchanged"] is True
    assert imported.source["synthetic_marker_verified"] is True
    assert imported.source["extraction"]["revenue_cells"][0] == "P&L Report!D5"
    assert len(imported.source["extraction"]["revenue_cells"]) == 28
    assert len(imported.source["extraction"]["cogs_cells"]) == 20
    assert path.read_bytes() == before


def test_importer_values_come_from_cells_not_a_preloaded_case(tmp_path):
    path = generate(tmp_path / "changed.xlsx")
    book = openpyxl.load_workbook(path)
    book["P&L Report"]["D5"] = 1163.4
    book.save(path)
    book.close()

    imported = import_synthetic_workbook(
        path,
        original_filename="changed.xlsx",
        requested_discount_pct=Decimal("2"),
        margin_floor_pct=Decimal("30"),
    )

    assert imported.scenario["list_arr_k"] == "15658.2"
    assert imported.source["extraction"]["revenue_k"] == "15658.2"


def test_importer_rejects_unmarked_workbooks_before_analysis(tmp_path):
    path = generate(tmp_path / "unmarked.xlsx")
    book = openpyxl.load_workbook(path)
    book["GL Detail"]["A1"] = "Unclassified workbook"
    book.save(path)
    book.close()

    with pytest.raises(WorkbookImportError) as raised:
        import_synthetic_workbook(
            path,
            original_filename="unmarked.xlsx",
            requested_discount_pct=Decimal("2"),
            margin_floor_pct=Decimal("30"),
        )

    assert raised.value.code == "SYNTHETIC_MARKER_MISSING"
