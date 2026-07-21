from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yigdesk.blackboard_mcp import (
    WIDGET_MIME_TYPE,
    WIDGET_URI,
    cast_approval,
    mcp,
    open_decision,
    propose_candidate,
    read_board,
    request_resolve,
)
from yigdesk.core.ledger import Ledger


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "data" / "scenarios" / "discount_approval"
SIX_OPS = {
    "open_decision",
    "propose_candidate",
    "post_claim",
    "cast_approval",
    "request_resolve",
    "read_board",
}


def _configure(monkeypatch, tmp_path):
    ledger = tmp_path / "board.jsonl"
    monkeypatch.setenv("YIGDESK_SCENARIO", str(SCENARIO))
    monkeypatch.setenv("YIGDESK_LEDGER", str(ledger))
    return ledger


def _open_approval_decision(monkeypatch, tmp_path, *, discount=12):
    ledger = _configure(monkeypatch, tmp_path)
    open_decision(
        "d1",
        "Approve the submitted discount?",
        "discount",
        {
            "candidate_selector": "human_selected",
            "required_approvals": [{"role": "cfo", "verdict": "approve"}],
        },
    )
    proposed = propose_candidate("d1", "c1", {"discount": discount})
    return ledger, proposed


def test_chatgpt_app_keeps_six_tools_and_declares_complete_impact_hints():
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    assert mcp.settings.streamable_http_path == "/mcp"
    assert mcp.settings.stateless_http is True
    assert set(tools) == SIX_OPS
    for tool in tools.values():
        assert tool.description.startswith("Use this when")
        assert tool.outputSchema["required"] == [
            "decisions",
            "view",
            "stateVersion",
            "event",
        ]
        assert tool.annotations.readOnlyHint is not None
        assert tool.annotations.destructiveHint is not None
        assert tool.annotations.openWorldHint is not None

    assert tools["read_board"].annotations.readOnlyHint is True
    assert tools["read_board"].annotations.idempotentHint is True
    assert tools["request_resolve"].annotations.idempotentHint is True
    assert tools["cast_approval"].annotations.idempotentHint is False
    assert tools["read_board"].meta["ui"]["resourceUri"] == WIDGET_URI
    assert tools["request_resolve"].meta["ui"]["resourceUri"] == WIDGET_URI
    assert "resourceUri" not in tools["cast_approval"].meta["ui"]
    assert tools["cast_approval"].meta["ui"]["visibility"] == ["model", "app"]


def test_widget_resource_is_versioned_sandboxed_and_bridge_first():
    resources = asyncio.run(mcp.list_resources())
    assert len(resources) == 1
    resource = resources[0]
    assert str(resource.uri) == WIDGET_URI
    assert resource.mimeType == WIDGET_MIME_TYPE
    assert resource.meta["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [],
    }

    contents = list(asyncio.run(mcp.read_resource(WIDGET_URI)))
    assert len(contents) == 1
    html = contents[0].content
    assert contents[0].mime_type == WIDGET_MIME_TYPE
    assert "ui/initialize" in html
    assert "ui/notifications/initialized" in html
    assert "ui/notifications/tool-result" in html
    assert 'rpcRequest("tools/call"' in html
    assert 'rpcNotify("ui/message"' in html
    assert "window.openai.callTool" in html
    assert "fetch(" not in html
    assert "innerHTML" not in html
    assert "eval(" not in html
    assert "<script src=" not in html


def test_read_board_preserves_agent_projection_and_adds_valid_widget_view(
    monkeypatch, tmp_path
):
    _open_approval_decision(monkeypatch, tmp_path)

    result = read_board()
    output = result.structuredContent

    assert output["decisions"]["d1"]["candidates"]["c1"]["consequence"][
        "verdict"
    ] == "ok"
    assert output["view"]["version"] == "yigdesk-decision-view/v3"
    assert output["view"]["decisions"][0]["blocks"][-2]["role"] == "cfo"
    assert output["stateVersion"] == 2
    assert output["event"] == {"kind": "read_board"}


def test_chatgpt_action_is_ledger_idempotent_and_resolution_stays_deterministic(
    monkeypatch, tmp_path
):
    ledger, _ = _open_approval_decision(monkeypatch, tmp_path)
    arguments = {
        "decision_id": "d1",
        "verdict": "approve",
        "scope": "c1",
        "role": "cfo",
        "action_id": "chatgpt-action-1",
        "correlation_id": "chatgpt-correlation-1",
        "action_type": "approve_candidate",
        "candidate_id": "c1",
    }

    first = cast_approval(**arguments)
    replay = cast_approval(**arguments)

    assert first.structuredContent["action_replayed"] is False
    assert replay.structuredContent["action_replayed"] is True
    assert first.structuredContent["stateVersion"] == replay.structuredContent[
        "stateVersion"
    ]
    approvals = [op for op in Ledger(ledger).read() if op.kind == "cast_approval"]
    assert len(approvals) == 1
    assert approvals[0].actor == "human:chatgpt"
    assert approvals[0].payload["action_id"] == "chatgpt-action-1"
    assert first.structuredContent["view"]["decisions"][0]["status"] == (
        "awaiting_resolution"
    )
    assert first.meta["yigdesk"]["continuation"]["status"] == (
        "awaiting_codex_watcher"
    )

    resolved = request_resolve("d1")
    repeated = request_resolve("d1")

    assert resolved.structuredContent["record"]["chosen_candidate_id"] == "c1"
    assert resolved.structuredContent["view"]["decisions"][0]["status"] == "resolved"
    assert repeated.structuredContent["replayed"] is True
    assert len([op for op in Ledger(ledger).read() if op.kind == "resolved"]) == 1


def test_chatgpt_action_cannot_approve_candidate_outside_deterministic_boundary(
    monkeypatch, tmp_path
):
    _, proposed = _open_approval_decision(monkeypatch, tmp_path, discount=90)
    assert proposed.structuredContent["consequence"]["verdict"] == "hold"

    with pytest.raises(ValueError, match="does not pass deterministic constraints"):
        cast_approval(
            decision_id="d1",
            verdict="approve",
            scope="c1",
            role="cfo",
            action_id="chatgpt-action-hold",
            correlation_id="chatgpt-action-hold",
            action_type="approve_candidate",
            candidate_id="c1",
        )


def test_partial_action_metadata_is_rejected_fail_closed(monkeypatch, tmp_path):
    _open_approval_decision(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="required together"):
        cast_approval(
            decision_id="d1",
            verdict="approve",
            scope="c1",
            role="cfo",
            action_id="incomplete",
        )
