"""Read-only MCP surface for Codex and other compatible agent clients."""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .mcp_tools import YigdeskToolClient


INSTRUCTIONS = """Yigdesk previews consequences from synthetic workbook evidence.
Call get_deal_context before analysis, call preview_consequence for the only
authoritative figures and verdict, and inspect at least one evidence address
before explaining a result. Never calculate figures yourself. Treat HOLD as
terminal. This server has no approval, send, signing, or write-back tools."""

mcp = FastMCP("Yigdesk", instructions=INSTRUCTIONS, json_response=True)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _client() -> YigdeskToolClient:
    return YigdeskToolClient(
        os.environ.get("YIGDESK_URL", "http://127.0.0.1:8787"),
        revision_id=os.environ.get("YIGDESK_REVISION_ID"),
    )


def _revision(result: dict[str, Any]) -> dict[str, str]:
    revision = result.get("revision") or result
    return {
        "revision_id": revision["revision_id"],
        "source_fingerprint": revision["source_fingerprint"],
        "packet_id": revision["packet_id"],
    }


def _audit(event: dict[str, Any]) -> None:
    path = os.environ.get("YIGDESK_AUDIT_FILE")
    if not path:
        return
    audit_path = Path(path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


@mcp.tool(annotations=READ_ONLY)
def get_deal_context() -> dict[str, Any]:
    """Read the current synthetic request and immutable workbook revision.

    Call this first to learn the scenario, evidence addresses, and source
    fingerprint. Do not use it as a substitute for preview_consequence because
    it contains current state, not proposed consequences.
    """

    try:
        result = _client().get_deal_context()
    except Exception:
        _audit({"tool": "get_deal_context", "ok": False})
        raise
    _audit(
        {
            "tool": "get_deal_context",
            "ok": True,
            "scenario_id": result["scenario_id"],
            "fingerprint": result["workbook"]["fingerprint"],
            **_revision(result),
        }
    )
    return result


@mcp.tool(annotations=READ_ONLY)
def preview_consequence() -> dict[str, Any]:
    """Produce the authoritative read-only consequence packet for the request.

    Use every displayed figure and the READY/HOLD verdict exactly as returned;
    never recompute or repair them. The packet proves whether workbook bytes
    remained unchanged and identifies the evidence that should be inspected.
    """

    try:
        result = _client().preview_consequence()
    except Exception:
        _audit({"tool": "preview_consequence", "ok": False})
        raise
    _audit(
        {
            "tool": "preview_consequence",
            "ok": True,
            "packet_id": result["packet_id"],
            "verdict": result["consequence"]["verdict"],
            **_revision(result),
        }
    )
    return result


@mcp.tool(annotations=READ_ONLY)
def inspect_evidence(
    address: Annotated[
        str,
        Field(
            description=(
                "Canonical workbook address returned by get_deal_context or the consequence "
                "packet, for example 'Deal Model!B4'."
            )
        ),
    ],
) -> dict[str, Any]:
    """Inspect one workbook object, including formula and lineage.

    Use this after preview_consequence to verify a decision-relevant evidence
    cell. Do not guess addresses; choose an exact address returned by an earlier
    tool call and follow the recovery guidance if it is unavailable.
    """

    try:
        result = _client().inspect_evidence(address)
    except Exception:
        _audit({"tool": "inspect_evidence", "ok": False, "address": address})
        raise
    _audit(
        {
            "tool": "inspect_evidence",
            "ok": True,
            "address": result["address"],
            **_revision(result),
        }
    )
    return result


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
