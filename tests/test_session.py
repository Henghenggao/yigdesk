from __future__ import annotations

import json
import sys
from decimal import Decimal

import openpyxl
import pytest

from scripts.generate_sample_workbook import generate
from yigdesk import cli
from yigdesk.app import create_app
from yigdesk.importer import WorkbookImportError
from yigdesk.session import (
    bind_synthetic_session,
    load_active_session,
    resolve_council_audit_path,
)


def test_bind_creates_an_immutable_session_without_changing_the_source(tmp_path):
    source = generate(tmp_path / "northwind.xlsx")
    before = source.read_bytes()
    runtime = tmp_path / "runtime"

    receipt = bind_synthetic_session(
        source,
        runtime_root=runtime,
        requested_discount_pct=Decimal("2"),
        margin_floor_pct=Decimal("30"),
        current_discount_pct=Decimal("0"),
    )
    bound = load_active_session(runtime)

    assert receipt["status"] == "BOUND"
    assert receipt["session_id"] == bound.session_id
    assert receipt["revision_id"].startswith("live-")
    assert str(source.resolve()) not in json.dumps(bound.manifest)
    assert bound.source_path.read_bytes() == before == source.read_bytes()
    assert bound.manifest["source"]["extraction"]["revenue_k"] == "14658.2"
    assert bound.manifest["source"]["extraction"]["cogs_k"] == "10031.0"
    assert resolve_council_audit_path(runtime) == bound.audit_path
    assert not bound.audit_path.exists()


def test_app_loads_and_refreshes_the_active_bound_session(tmp_path):
    runtime = tmp_path / "runtime"
    first = generate(tmp_path / "first.xlsx")
    first_receipt = bind_synthetic_session(
        first,
        runtime_root=runtime,
        requested_discount_pct=Decimal("2"),
        margin_floor_pct=Decimal("30"),
    )
    app = create_app(runtime_dir=runtime)
    app.config.update(TESTING=True)
    client = app.test_client()

    initial = client.get("/api/state").get_json()
    assert initial["session"]["session_id"] == first_receipt["session_id"]
    assert initial["session"]["revision_id"] == first_receipt["revision_id"]
    assert initial["scenario"]["list_arr_k"] == "14658.2"

    second = generate(tmp_path / "second.xlsx")
    book = openpyxl.load_workbook(second)
    book["P&L Report"]["D5"] = 1163.4
    book.save(second)
    book.close()
    second_receipt = bind_synthetic_session(
        second,
        runtime_root=runtime,
        requested_discount_pct=Decimal("1.5"),
        margin_floor_pct=Decimal("30"),
    )

    refreshed = client.get("/api/state").get_json()
    assert refreshed["session"]["session_id"] == second_receipt["session_id"]
    assert refreshed["scenario"]["list_arr_k"] == "15658.2"
    assert refreshed["scenario"]["requested_discount_pct"] == "1.5"


def test_decision_inputs_change_the_revision_even_when_source_bytes_do_not(tmp_path):
    runtime = tmp_path / "runtime"
    source = generate(tmp_path / "northwind.xlsx")
    first = bind_synthetic_session(
        source,
        runtime_root=runtime,
        requested_discount_pct=Decimal("2"),
        margin_floor_pct=Decimal("30"),
    )
    second = bind_synthetic_session(
        source,
        runtime_root=runtime,
        requested_discount_pct=Decimal("2.2"),
        margin_floor_pct=Decimal("30"),
    )

    assert first["source_fingerprint"] == second["source_fingerprint"]
    assert first["projection_fingerprint"] != second["projection_fingerprint"]
    assert first["revision_id"] != second["revision_id"]


def test_failed_bind_does_not_replace_the_previous_active_session(tmp_path):
    runtime = tmp_path / "runtime"
    valid = generate(tmp_path / "valid.xlsx")
    receipt = bind_synthetic_session(
        valid,
        runtime_root=runtime,
        requested_discount_pct=Decimal("2"),
        margin_floor_pct=Decimal("30"),
    )
    invalid = generate(tmp_path / "invalid.xlsx")
    book = openpyxl.load_workbook(invalid)
    book["GL Detail"]["A1"] = "Not marked for the public demo"
    book.save(invalid)
    book.close()

    with pytest.raises(WorkbookImportError, match="generated workbooks marked SYNTHETIC"):
        bind_synthetic_session(
            invalid,
            runtime_root=runtime,
            requested_discount_pct=Decimal("2"),
            margin_floor_pct=Decimal("30"),
        )

    assert load_active_session(runtime).session_id == receipt["session_id"]


def test_cli_bind_is_an_offline_natural_language_intake_primitive(
    tmp_path, monkeypatch, capsys
):
    source = generate(tmp_path / "northwind.xlsx")
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "yigdesk.cli",
            "--runtime",
            str(runtime),
            "bind",
            "--file",
            str(source),
            "--discount",
            "2",
            "--floor",
            "30",
        ],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "BOUND"
    assert payload["source"]["source_cell_count"] == 48
    assert payload["decision"]["requested_discount_pct"] == "2"
    assert payload["next"]["council_audit"].endswith("codex-a2a-audit.jsonl")
