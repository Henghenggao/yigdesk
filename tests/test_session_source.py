"""Adapter test: bound session's projection workbook -> evaluator ModelSource.

Exercises the real ``yigdesk.session.bind_synthetic_session`` API in a
``tmp_path`` vault (no mocking of session/importer), then checks that
``yigdesk.evaluator.session_source.source_for_active_session`` wraps the
resulting projection workbook as a ``ModelSource`` the blackboard can
price against.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from scripts.generate_sample_workbook import generate
from yigdesk.evaluator.session_source import source_for_active_session
from yigdesk.session import bind_synthetic_session, load_active_session


# Named refs into the "Deal Inputs" sheet that yigdesk.workbook.create_workbook
# writes for every bound session's projection.xlsx (see workbook.py:21-31).
INPUT_REFS = {
    "list_arr": "Deal Inputs!B2",
    "discount": "Deal Inputs!B3",
    "cogs": "Deal Inputs!B4",
    "floor": "Deal Inputs!B5",
    "requested_discount": "Deal Inputs!B6",
}


def test_adapter_wraps_the_bound_projection_workbook_as_a_model_source(tmp_path):
    source = generate(tmp_path / "northwind.xlsx")
    runtime = tmp_path / "runtime"

    receipt = bind_synthetic_session(
        source,
        runtime_root=runtime,
        requested_discount_pct=Decimal("2"),
        margin_floor_pct=Decimal("30"),
        current_discount_pct=Decimal("0"),
    )
    bound = load_active_session(runtime)

    model_source = source_for_active_session(runtime, INPUT_REFS)

    # (a) Fingerprint identity: a 64-char sha256 hex digest that matches
    # both what bind_synthetic_session reported and what the manifest
    # recorded for the projection workbook -- independently recomputed
    # here from the live file bytes, not just trusted from either.
    assert len(model_source.fingerprint) == 64
    assert model_source.fingerprint == receipt["projection_fingerprint"]
    assert model_source.fingerprint == bound.manifest["projection"]["sha256"]
    assert model_source.fingerprint == hashlib.sha256(
        bound.projection_path.read_bytes()
    ).hexdigest()
    # And it is genuinely the *projection*'s identity, not the raw
    # upload's -- the adapter must not point ModelSource at source.xlsx,
    # whose "P&L Report" sheet has no addressable "Deal Inputs!..." cells.
    assert model_source.fingerprint != receipt["source_fingerprint"]
    assert model_source.path == bound.projection_path

    # (b) base_inputs() resolves every configured ref against the real,
    # bound projection workbook -- values sourced from the session
    # manifest's own record of the scenario that was projected, not
    # hard-coded fixture numbers.
    scenario = bound.manifest["scenario"]
    inputs = model_source.base_inputs()
    assert set(inputs) == set(INPUT_REFS)
    assert inputs["list_arr"] == Decimal(scenario["list_arr_k"])
    assert inputs["discount"] == Decimal(scenario["current_discount_pct"])
    assert inputs["cogs"] == Decimal(scenario["cogs_k"])
    assert inputs["floor"] == Decimal(scenario["margin_floor_pct"])
    assert inputs["requested_discount"] == Decimal(scenario["requested_discount_pct"])

    # Sanity: these are genuine bound-session numbers, not a vacuous
    # all-None extraction.
    assert inputs["list_arr"] == Decimal("14658.2")
    assert inputs["cogs"] == Decimal("10031.0")
    assert inputs["requested_discount"] == Decimal("2")


def test_adapter_touches_neither_session_manifest_nor_projection_bytes(tmp_path):
    source = generate(tmp_path / "northwind.xlsx")
    runtime = tmp_path / "runtime"
    bind_synthetic_session(
        source,
        runtime_root=runtime,
        requested_discount_pct=Decimal("5"),
        margin_floor_pct=Decimal("25"),
    )
    bound_before = load_active_session(runtime)
    manifest_before = bound_before.manifest_path.read_bytes()
    projection_before = bound_before.projection_path.read_bytes()

    source_for_active_session(runtime, INPUT_REFS).base_inputs()

    assert bound_before.manifest_path.read_bytes() == manifest_before
    assert bound_before.projection_path.read_bytes() == projection_before
