from __future__ import annotations

import argparse
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict

from yigdesk.action_bridge import (
    ACTION_VERSION,
    ActionError,
    LocalContinuationAdapter,
    execute_action,
    parse_action,
)
from yigdesk.agent_identity import identity_from_env
from yigdesk.board import board_dict, build_blackboard_from_env
from yigdesk.core.gate import Pending
from yigdesk.decision_manifest import build_manifest


INSTRUCTIONS = """Yigdesk is a deterministic decision blackboard. Propose candidates and post
grounded claims; never invent figures - the engine prices every candidate and rejects ungrounded
claims. Treat HOLD as terminal for a candidate. Decisions close only through request_resolve; this
server never writes back to the source model. Use read_board whenever a human needs the live
decision workbench. After a human approval, call request_resolve to run the deterministic gate."""

WIDGET_URI = "ui://yigdesk/decision-workbench-v1.html"
WIDGET_MIME_TYPE = "text/html;profile=mcp-app"
WIDGET_PATH = Path(__file__).resolve().parent / "static" / "chatgpt-widget.html"


class DecisionToolOutput(BaseModel):
    """Shared result shape for agents and the mounted decision widget.

    ``decisions`` preserves the original six-op blackboard contract. ``view`` is
    the allow-listed presentation manifest. Extra event-specific fields remain
    backwards compatible with the original tool results.
    """

    model_config = ConfigDict(extra="allow")

    decisions: dict[str, Any]
    view: dict[str, Any]
    stateVersion: int
    event: dict[str, Any]


def _port() -> int:
    try:
        value = int(os.environ.get("YIGDESK_MCP_PORT", "8787"))
    except ValueError as error:
        raise ValueError("YIGDESK_MCP_PORT must be an integer") from error
    if not 1 <= value <= 65535:
        raise ValueError("YIGDESK_MCP_PORT must be between 1 and 65535")
    return value


mcp = FastMCP(
    "Yigdesk",
    instructions=INSTRUCTIONS,
    host=os.environ.get("YIGDESK_MCP_HOST", "127.0.0.1"),
    port=_port(),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


WIDGET_RESOURCE_META = {
    "ui": {
        "prefersBorder": True,
        "csp": {"connectDomains": [], "resourceDomains": []},
    },
    "openai/widgetDescription": (
        "A focused, live Yigdesk decision surface showing deterministic candidate "
        "comparisons, evidence, risk boundaries, the human gate, and the committed record."
    ),
    "openai/widgetPrefersBorder": True,
    "openai/widgetCSP": {"connect_domains": [], "resource_domains": []},
}


def _tool_meta(
    invoking: str,
    invoked: str,
    *,
    widget: bool = False,
    app_visible: bool = False,
) -> dict[str, Any]:
    ui: dict[str, Any] = {
        "visibility": ["model", "app"] if app_visible else ["model"]
    }
    meta: dict[str, Any] = {
        "ui": ui,
        "openai/toolInvocation/invoking": invoking,
        "openai/toolInvocation/invoked": invoked,
    }
    if widget:
        ui["resourceUri"] = WIDGET_URI
        meta["openai/outputTemplate"] = WIDGET_URI
        meta["openai/widgetAccessible"] = True
    return meta


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
BOUNDED_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@mcp.resource(
    WIDGET_URI,
    name="yigdesk-decision-workbench",
    title="Yigdesk decision workbench",
    description="Trusted UI for one live deterministic decision and its audit proof.",
    mime_type=WIDGET_MIME_TYPE,
    meta=WIDGET_RESOURCE_META,
)
def decision_workbench_widget() -> str:
    return WIDGET_PATH.read_text(encoding="utf-8")


def _snapshot(blackboard) -> tuple[dict[str, Any], dict[str, Any], int]:
    board = board_dict(blackboard.project())
    view = build_manifest(board, evaluator_revision=blackboard.ev.revision)
    operations = blackboard.ledger.read()
    state_version = operations[-1].seq if operations else 0
    return board, view, state_version


def _result(
    blackboard,
    *,
    message: str,
    event: dict[str, Any],
    fields: dict[str, Any] | None = None,
    widget_meta: dict[str, Any] | None = None,
) -> DecisionToolOutput:
    board, view, state_version = _snapshot(blackboard)
    structured: dict[str, Any] = {
        **board,
        "view": view,
        "stateVersion": state_version,
        "event": event,
        "callerIdentity": identity_from_env().to_dict(),
    }
    structured.update(fields or {})
    result = CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent=structured,
        _meta=widget_meta or {},
    )
    # FastMCP uses the annotated Pydantic model as outputSchema, then validates
    # the explicit CallToolResult. The explicit result is needed for content and
    # widget-only metadata to remain separate.
    return cast(DecisionToolOutput, result)


def _require_legacy_approval(
    blackboard, decision_id: str, verdict: str, scope: str, role: str
) -> None:
    if verdict not in {"approve", "hold", "reject"}:
        raise ValueError("verdict must be approve, hold, or reject")
    decision = blackboard.project().decisions.get(decision_id)
    if decision is None:
        raise KeyError(f"decision {decision_id!r} does not exist")
    if decision.resolution is not None:
        raise ValueError("resolved decisions are immutable")
    required_roles = {
        requirement["role"]
        for requirement in decision.policy.get("required_approvals", [])
    }
    if required_roles and role not in required_roles:
        raise ValueError(f"role must be one of {sorted(required_roles)}")
    if scope != decision_id:
        candidate = decision.candidates.get(scope)
        if candidate is None:
            raise ValueError("scope must be a candidate id or the decision id")
        if (
            verdict == "approve"
            and decision.policy.get("candidate_selector", "human_selected")
            == "human_selected"
            and (
                candidate.consequence is None
                or candidate.consequence.verdict != "ok"
            )
        ):
            raise ValueError("approved candidate must pass deterministic constraints")
    elif (
        verdict == "approve"
        and decision.policy.get("candidate_selector", "human_selected")
        == "human_selected"
    ):
        raise ValueError("human_selected approval requires a candidate scope")


@mcp.tool(
    title="Open decision",
    description="Use this when a new measurable decision must be opened on the append-only board.",
    annotations=BOUNDED_WRITE,
    meta=_tool_meta("Opening the decision…", "Decision opened."),
)
def open_decision(
    decision_id: str,
    question: str,
    decision_type: str = "",
    policy: dict | None = None,
) -> DecisionToolOutput:
    blackboard = build_blackboard_from_env()
    identity = identity_from_env()
    blackboard.open_decision(
        decision_id,
        question,
        decision_type,
        policy or {},
        actor=identity.actor,
        role="owner",
        agent_identity=identity,
    )
    return _result(
        blackboard,
        message=f"Opened decision {decision_id}.",
        event={"kind": "open_decision", "decision_id": decision_id},
        fields={"opened": decision_id},
    )


@mcp.tool(
    title="Propose candidate",
    description=(
        "Use this when an agent has a concrete candidate override to price through "
        "the deterministic engine."
    ),
    annotations=BOUNDED_WRITE,
    meta=_tool_meta("Pricing the candidate…", "Candidate priced."),
)
def propose_candidate(
    decision_id: str, candidate_id: str, overrides: dict
) -> DecisionToolOutput:
    blackboard = build_blackboard_from_env()
    identity = identity_from_env()
    operation = blackboard.propose_candidate(
        decision_id,
        candidate_id,
        {"overrides": overrides},
        actor=identity.actor,
        role="proposer",
        agent_identity=identity,
    )
    consequence = operation.payload["consequence"]
    return _result(
        blackboard,
        message=(
            f"Priced candidate {candidate_id}: deterministic verdict "
            f"{consequence['verdict']}."
        ),
        event={
            "kind": "propose_candidate",
            "decision_id": decision_id,
            "candidate_id": candidate_id,
        },
        fields={"candidate_id": candidate_id, "consequence": consequence},
    )


@mcp.tool(
    title="Post grounded claim",
    description=(
        "Use this when an agent has an evidence-backed claim whose references must "
        "all ground to real model cells."
    ),
    annotations=BOUNDED_WRITE,
    meta=_tool_meta("Grounding the claim…", "Claim checked."),
)
def post_claim(
    decision_id: str,
    claim_id: str,
    type: str,
    target: str,
    body: str,
    refs: list[str],
) -> DecisionToolOutput:
    blackboard = build_blackboard_from_env()
    identity = identity_from_env()
    claim = blackboard.post_claim(
        decision_id,
        claim_id,
        type,
        target,
        body,
        refs,
        actor=identity.actor,
        role="critic",
        agent_identity=identity,
    )
    return _result(
        blackboard,
        message=f"Claim {claim_id} is {claim.status}.",
        event={
            "kind": "post_claim",
            "decision_id": decision_id,
            "claim_id": claim_id,
            "status": claim.status,
        },
        fields={"claim_id": claim_id, "status": claim.status},
    )


@mcp.tool(
    title="Cast human approval",
    description=(
        "Use this when a human has explicitly approved, held, or rejected the current "
        "decision scope. Include action metadata for retry-safe ChatGPT UI calls."
    ),
    annotations=BOUNDED_WRITE,
    meta=_tool_meta(
        "Recording human intent…",
        "Human intent recorded.",
        app_visible=True,
    ),
)
def cast_approval(
    decision_id: str,
    verdict: str,
    scope: str,
    role: str = "reviewer",
    action_id: str = "",
    correlation_id: str = "",
    action_type: str = "",
    candidate_id: str | None = None,
    note: str | None = None,
) -> DecisionToolOutput:
    blackboard = build_blackboard_from_env()
    if any((action_id, correlation_id, action_type, candidate_id is not None, note is not None)):
        if not action_id or not correlation_id or not action_type:
            raise ActionError(
                "action_id, correlation_id, and action_type are required together"
            )
        action = parse_action(
            {
                "version": ACTION_VERSION,
                "action_id": action_id,
                "correlation_id": correlation_id,
                "decision_id": decision_id,
                "action_type": action_type,
                "candidate_id": candidate_id,
                "human": {"role": role, "verdict": verdict, "note": note},
            }
        )
        response = execute_action(
            blackboard,
            action,
            LocalContinuationAdapter(),
            actor="human:chatgpt",
        )
        if action_type == "approve_candidate":
            message = (
                "Human approval recorded. The decision is not closed; call "
                f"request_resolve for {decision_id} to run the deterministic gate."
            )
        elif action_type == "hold":
            message = "Human hold recorded. Treat hold as terminal."
        else:
            message = "Human revision request recorded. Continue the agent workflow with a revised proposal."
        return _result(
            blackboard,
            message=message,
            event={
                "kind": "cast_approval",
                "decision_id": decision_id,
                "verdict": verdict,
                "scope": scope,
                "action_id": action_id,
                "replayed": response["action_replayed"],
            },
            fields={
                "recorded": verdict,
                "action_replayed": response["action_replayed"],
            },
            widget_meta={
                "yigdesk": {
                    "actionReceipt": {
                        "actionId": action_id,
                        "correlationId": correlation_id,
                        "replayed": response["action_replayed"],
                    },
                    "continuation": response["continuation"],
                }
            },
        )

    _require_legacy_approval(blackboard, decision_id, verdict, scope, role)
    identity = identity_from_env()
    blackboard.cast_approval(
        decision_id,
        verdict,
        scope,
        actor=identity.actor,
        role=role,
        agent_identity=identity,
    )
    return _result(
        blackboard,
        message=(
            f"Recorded {verdict} for {decision_id}. "
            "Call request_resolve only when the deterministic gate should run."
        ),
        event={
            "kind": "cast_approval",
            "decision_id": decision_id,
            "verdict": verdict,
            "scope": scope,
            "replayed": False,
        },
        fields={"recorded": verdict, "action_replayed": False},
    )


@mcp.tool(
    title="Resolve decision",
    description=(
        "Use this when the current decision should be evaluated by the deterministic "
        "gate; it returns pending(reason) or the committed DecisionRecord."
    ),
    annotations=IDEMPOTENT_WRITE,
    meta=_tool_meta(
        "Running the deterministic gate…",
        "Deterministic gate complete.",
        widget=True,
        app_visible=True,
    ),
)
def request_resolve(decision_id: str) -> DecisionToolOutput:
    blackboard = build_blackboard_from_env()
    identity = identity_from_env()
    result, replayed = blackboard.request_resolve_with_status(
        decision_id, actor=identity.actor, role="resolver", agent_identity=identity,
    )
    if isinstance(result, Pending):
        return _result(
            blackboard,
            message=f"Decision {decision_id} is pending: {result.reason}.",
            event={
                "kind": "request_resolve",
                "decision_id": decision_id,
                "outcome": "pending",
                "reason": result.reason,
            },
            fields={"pending": result.reason},
        )
    record = asdict(result)
    return _result(
        blackboard,
        message=(
            f"Decision {decision_id} committed deterministically as "
            f"{record['chosen_candidate_id']}."
        ),
        event={
            "kind": "request_resolve",
            "decision_id": decision_id,
            "outcome": "committed",
            "replayed": replayed,
        },
        fields={"record": record, "replayed": replayed},
    )


@mcp.tool(
    title="Read decision board",
    description=(
        "Use this when agents or humans need the authoritative board projection and "
        "the live focused decision workbench."
    ),
    annotations=READ_ONLY,
    meta=_tool_meta(
        "Reading the decision board…",
        "Decision board ready.",
        widget=True,
        app_visible=True,
    ),
)
def read_board() -> DecisionToolOutput:
    blackboard = build_blackboard_from_env()
    return _result(
        blackboard,
        message="Showing the current deterministic decision board.",
        event={"kind": "read_board"},
    )


def _transport(value: str) -> str:
    if value not in {"stdio", "sse", "streamable-http"}:
        raise argparse.ArgumentTypeError(
            "transport must be stdio, sse, or streamable-http"
        )
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Yigdesk MCP server.")
    parser.add_argument(
        "--transport",
        type=_transport,
        default=os.environ.get("YIGDESK_MCP_TRANSPORT", "stdio"),
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)


def http_main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
