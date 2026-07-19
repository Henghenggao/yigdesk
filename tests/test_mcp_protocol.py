from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_mcp_tools import live_demo


TOOL_NAMES = [
    "get_deal_context",
    "preview_consequence",
    "inspect_evidence",
    "evaluate_proposal",
    "compare_proposals",
    "find_feasible_boundary",
    "stress_test_assumption",
    "list_missing_evidence",
]


async def protocol_roundtrip(
    base_url: str,
    audit_path,
    *,
    minimal_environment: bool = False,
    actor: str | None = None,
):
    explicit = {
        "YIGDESK_URL": base_url,
        "YIGDESK_AUDIT_FILE": str(audit_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "yigdesk.mcp_server"],
        env=explicit if minimal_environment else {**os.environ, **explicit},
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool(
                "preview_consequence",
                {} if actor is None else {"actor": actor},
            )
            return tools, result


def test_stdio_mcp_lists_narrow_tools_and_returns_structured_packet(tmp_path):
    audit_path = tmp_path / "mcp-audit.jsonl"
    with live_demo(tmp_path) as base_url:
        tools, result = asyncio.run(protocol_roundtrip(base_url, audit_path))

    assert [tool.name for tool in tools.tools] == TOOL_NAMES
    for tool in tools.tools:
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
        assert tool.outputSchema is not None
    assert result.isError is False
    assert result.structuredContent["packet_id"].startswith("cpkt-")
    assert result.structuredContent["consequence"]["verdict"] == "READY FOR CFO"
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert events == [
        {
            "tool": "preview_consequence",
            "ok": True,
            "packet_id": result.structuredContent["packet_id"],
            "revision_id": result.structuredContent["revision_id"],
            "source_fingerprint": result.structuredContent["source_fingerprint"],
            "verdict": "READY FOR CFO",
        }
    ]


def test_stdio_mcp_starts_with_only_explicit_non_secret_environment(tmp_path):
    audit_path = tmp_path / "minimal-mcp-audit.jsonl"
    with live_demo(tmp_path) as base_url:
        tools, result = asyncio.run(
            protocol_roundtrip(base_url, audit_path, minimal_environment=True)
        )

    assert [tool.name for tool in tools.tools] == TOOL_NAMES
    assert result.isError is False


def test_stdio_mcp_audit_attributes_an_allowlisted_codex_agent(tmp_path):
    audit_path = tmp_path / "actor-audit.jsonl"
    with live_demo(tmp_path) as base_url:
        asyncio.run(
            protocol_roundtrip(
                base_url,
                audit_path,
                actor="risk_challenger",
            )
        )

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["actor"] == "risk_challenger"
