"""Read-only MCP surface for Codex and other compatible agent clients."""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .mcp_tools import ToolCallError, YigdeskToolClient
from .session import resolve_council_audit_path


INSTRUCTIONS = """Yigdesk is a read-only decision blackboard for synthetic workbook evidence.
Start with get_deal_context and keep every proposal bound to its returned revision.
Use preview_consequence for the submitted request, inspect_evidence for lineage,
and the proposal tools for deterministic challenge and comparison. Exact unrounded
metrics decide constraints; displayed metrics are presentation only. Treat HOLD as
terminal. Financial feasibility is not proof of commercial optimality. This server
has no approval, send, signing, messaging, or write-back tools."""

mcp = FastMCP("Yigdesk", instructions=INSTRUCTIONS, json_response=True)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
ALLOWED_ACTORS = frozenset(
    (
        "finance_analyst",
        "sales_advocate",
        "risk_challenger",
        "decision_optimizer",
    )
)
Actor = Annotated[
    Literal[
        "finance_analyst",
        "sales_advocate",
        "risk_challenger",
        "decision_optimizer",
    ]
    | None,
    Field(
        description=(
            "Declared Codex council role for audit attribution. This label is not an "
            "authentication credential."
        )
    ),
]


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


def _audit(event: dict[str, Any], actor: Actor = None) -> None:
    path = os.environ.get("YIGDESK_AUDIT_FILE")
    if not path:
        path = str(
            resolve_council_audit_path(
                os.environ.get(
                    "YIGDESK_RUNTIME", Path(__file__).resolve().parents[1] / "runtime"
                )
            )
        )
    if actor in ALLOWED_ACTORS:
        event = {"actor": actor, **event}
    audit_path = Path(path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _audit_failure(tool: str, error: Exception, actor: Actor, **fields: Any) -> None:
    """Persist a stable diagnostic category without leaking exception detail."""

    if isinstance(error, ToolCallError):
        message = str(error)
        if "unknown or inactive" in message:
            error_code = "REVISION_NOT_ACTIVE"
        elif "unavailable" in message:
            error_code = "YIGDESK_UNAVAILABLE"
        else:
            error_code = "YIGDESK_REJECTED"
    else:
        error_code = "INVALID_TOOL_RESPONSE"
    _audit(
        {"tool": tool, "ok": False, "error_code": error_code, **fields}, actor
    )


@mcp.tool(annotations=READ_ONLY)
def get_deal_context(actor: Actor = None) -> dict[str, Any]:
    """Read the current synthetic request and immutable workbook revision.

    Call this first to learn the scenario, evidence addresses, and source
    fingerprint. Do not use it as a substitute for preview_consequence because
    it contains current state, not proposed consequences.
    """

    try:
        result = _client().get_deal_context()
    except Exception as error:
        _audit_failure("get_deal_context", error, actor)
        raise
    _audit(
        {
            "tool": "get_deal_context",
            "ok": True,
            "scenario_id": result["scenario_id"],
            "fingerprint": result["workbook"]["fingerprint"],
            **_revision(result),
        },
        actor,
    )
    return result


@mcp.tool(annotations=READ_ONLY)
def preview_consequence(actor: Actor = None) -> dict[str, Any]:
    """Produce the authoritative read-only consequence packet for the request.

    Use every displayed figure and the READY/HOLD verdict exactly as returned;
    never recompute or repair them. The packet proves whether workbook bytes
    remained unchanged and identifies the evidence that should be inspected.
    """

    try:
        result = _client().preview_consequence()
    except Exception as error:
        _audit_failure("preview_consequence", error, actor)
        raise
    _audit(
        {
            "tool": "preview_consequence",
            "ok": True,
            "packet_id": result["packet_id"],
            "verdict": result["consequence"]["verdict"],
            **_revision(result),
        },
        actor,
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
    actor: Actor = None,
) -> dict[str, Any]:
    """Inspect one workbook object, including formula and lineage.

    Use this after preview_consequence to verify a decision-relevant evidence
    cell. Do not guess addresses; choose an exact address returned by an earlier
    tool call and follow the recovery guidance if it is unavailable.
    """

    try:
        result = _client().inspect_evidence(address)
    except Exception as error:
        _audit_failure("inspect_evidence", error, actor, address=address)
        raise
    _audit(
        {
            "tool": "inspect_evidence",
            "ok": True,
            "address": result["address"],
            **_revision(result),
        },
        actor,
    )
    return result


@mcp.tool(annotations=READ_ONLY)
def evaluate_proposal(
    requested_discount_pct: Annotated[
        float,
        Field(
            ge=0,
            le=100,
            description="Candidate discount percentage to evaluate against the current revision.",
        ),
    ],
    actor: Actor = None,
) -> dict[str, Any]:
    """Evaluate one discount proposal using exact policy math.

    Use this when an agent proposes a concrete percentage. The response separates
    unrounded constraint values from display values and never changes the workbook.
    """

    try:
        result = _client().evaluate_proposal(str(requested_discount_pct))
    except Exception as error:
        _audit_failure("evaluate_proposal", error, actor)
        raise
    _audit(
        {
            "tool": "evaluate_proposal",
            "ok": True,
            "requested_discount_pct": str(requested_discount_pct),
            "constraint_pass": result["proposal"]["constraint_pass"],
            **_revision(result),
        },
        actor,
    )
    return result


@mcp.tool(annotations=READ_ONLY)
def compare_proposals(
    discounts_pct: Annotated[
        list[float],
        Field(
            min_length=2,
            max_length=12,
            description="Two to twelve unique candidate discount percentages to compare.",
        ),
    ],
    actor: Actor = None,
) -> dict[str, Any]:
    """Compare candidate discounts on one immutable evidence revision.

    Use this after agents submit competing proposals. It reports financial
    feasibility consistently but does not invent market evidence or name a
    commercially optimal proposal.
    """

    try:
        result = _client().compare_proposals([str(value) for value in discounts_pct])
    except Exception as error:
        _audit_failure("compare_proposals", error, actor)
        raise
    _audit(
        {
            "tool": "compare_proposals",
            "ok": True,
            "proposal_count": len(discounts_pct),
            "discounts_pct": [str(value) for value in discounts_pct],
            "highest_feasible_proposal_pct": result["comparison"][
                "highest_feasible_proposal_pct"
            ],
            **_revision(result),
        },
        actor,
    )
    return result


@mcp.tool(annotations=READ_ONLY)
def find_feasible_boundary(
    step_pct: Annotated[
        float,
        Field(
            gt=0,
            le=100,
            description="Discount increment used to floor the exact safe boundary, usually 0.01.",
        ),
    ] = 0.01,
    actor: Actor = None,
) -> dict[str, Any]:
    """Find the exact maximum discount allowed by the configured margin floor.

    The result includes the largest step-aligned safe proposal and the first
    unsafe proposal, both re-evaluated with unrounded constraint math.
    """

    try:
        result = _client().find_feasible_boundary(str(step_pct))
    except Exception as error:
        _audit_failure("find_feasible_boundary", error, actor)
        raise
    _audit(
        {
            "tool": "find_feasible_boundary",
            "ok": True,
            "step_pct": str(step_pct),
            "status": result["boundary"]["status"],
            "largest_safe_step_pct": result["boundary"]["largest_safe_step_pct"],
            **_revision(result),
        },
        actor,
    )
    return result


@mcp.tool(annotations=READ_ONLY)
def stress_test_assumption(
    requested_discount_pct: Annotated[
        float,
        Field(ge=0, le=100, description="Candidate discount percentage to stress."),
    ],
    cogs_change_pct: Annotated[
        float,
        Field(
            ge=-100,
            le=1000,
            description="Temporary relative COGS change percentage, such as 5 for a 5% increase.",
        ),
    ],
    actor: Actor = None,
) -> dict[str, Any]:
    """Stress a proposal under one explicit, non-persistent COGS assumption.

    Use this for a risk challenge. The response always labels the assumption and
    never writes the stressed value back to source evidence.
    """

    try:
        result = _client().stress_test_assumption(
            str(requested_discount_pct), str(cogs_change_pct)
        )
    except Exception as error:
        _audit_failure("stress_test_assumption", error, actor)
        raise
    _audit(
        {
            "tool": "stress_test_assumption",
            "ok": True,
            "requested_discount_pct": str(requested_discount_pct),
            "cogs_change_pct": str(cogs_change_pct),
            "status": result["stress_test"]["status"],
            **_revision(result),
        },
        actor,
    )
    return result


@mcp.tool(annotations=READ_ONLY)
def list_missing_evidence(actor: Actor = None) -> dict[str, Any]:
    """List missing inputs that block review, boundary, or stress-test claims.

    Call this before asking another agent to fill a gap. A HOLD response is
    terminal for the current revision; do not infer or estimate the missing fact.
    """

    try:
        result = _client().list_missing_evidence()
    except Exception as error:
        _audit_failure("list_missing_evidence", error, actor)
        raise
    _audit(
        {
            "tool": "list_missing_evidence",
            "ok": True,
            "status": result["status"],
            "missing_count": len(result["missing"]),
            **_revision(result),
        },
        actor,
    )
    return result


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
