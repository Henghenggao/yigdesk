"""Typed local adapter from a human intent to the existing six board operations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .board import board_dict
from .core.gate import Pending


ACTION_VERSION = "yigdesk-agent-action/v1"
ACTION_TYPES = {"approve_candidate", "hold", "request_revision", "resolve"}


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class AgentAction:
    action_id: str
    correlation_id: str
    decision_id: str
    action_type: str
    candidate_id: str | None
    role: str
    verdict: str | None
    note: str | None


def parse_action(value: Any) -> AgentAction:
    if not isinstance(value, dict):
        raise ActionError("action must be an object")
    allowed = {"version", "action_id", "correlation_id", "decision_id", "action_type", "candidate_id", "human"}
    extra = set(value) - allowed
    if extra:
        raise ActionError(f"unsupported action fields: {sorted(extra)}")
    if value.get("version") != ACTION_VERSION:
        raise ActionError("unsupported action version")
    human = value.get("human", {})
    if not isinstance(human, dict) or set(human) - {"role", "note", "verdict"}:
        raise ActionError("human input contains unsupported fields")
    required = ("action_id", "correlation_id", "decision_id", "action_type")
    if not all(isinstance(value.get(k), str) and value[k] for k in required):
        raise ActionError("action_id, correlation_id, decision_id, and action_type are required strings")
    if len(value["action_id"]) > 160 or len(value["correlation_id"]) > 160:
        raise ActionError("idempotency metadata is too long")
    if value["action_type"] not in ACTION_TYPES:
        raise ActionError("unsupported action type")
    candidate_id = value.get("candidate_id")
    if candidate_id is not None and not isinstance(candidate_id, str):
        raise ActionError("candidate_id must be a string")
    if value["action_type"] == "approve_candidate" and not candidate_id:
        raise ActionError("approve_candidate requires candidate_id")
    if value["action_type"] != "approve_candidate" and candidate_id is not None:
        raise ActionError("candidate_id is only valid for approve_candidate")
    role = human.get("role", "reviewer")
    if not isinstance(role, str) or not role:
        raise ActionError("human.role must be a non-empty string")
    note = human.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 500):
        raise ActionError("human.note must be a string up to 500 characters")
    verdict = human.get("verdict")
    if verdict is not None and verdict not in {"approve", "hold", "reject"}:
        raise ActionError("human.verdict must be approve, hold, or reject")
    expected_verdict = {"approve_candidate": "approve", "hold": "hold", "request_revision": "reject"}.get(value["action_type"])
    if verdict is not None and verdict != expected_verdict:
        raise ActionError("human.verdict conflicts with action_type")
    return AgentAction(value["action_id"], value["correlation_id"], value["decision_id"], value["action_type"], candidate_id, role, verdict, note)


class LocalContinuationAdapter:
    """Expose the durable hand-off without pretending to call a private Codex API."""

    def continue_workflow(self, action: AgentAction) -> dict[str, Any]:
        return {
            "status": "awaiting_codex_watcher",
            "mode": "append-only-ledger",
            "integration_point": "python -m yigdesk.continuation",
            "intent": {"action_id": action.action_id,
                       "decision_id": action.decision_id, "action_type": action.action_type,
                       "candidate_id": action.candidate_id, "note": action.note,
                       "verdict": action.verdict, "correlation_id": action.correlation_id},
        }


def execute_action(
    blackboard,
    action: AgentAction,
    adapter: LocalContinuationAdapter,
    *,
    actor: str = "human:web",
) -> dict[str, Any]:
    existing = blackboard.find_action(action.action_id)
    if existing is not None:
        payload = existing.payload
        same_request = (
            existing.actor == actor
            and payload.get("correlation_id") == action.correlation_id
            and payload.get("decision_id") == action.decision_id
            and payload.get("action_type") == action.action_type
            and payload.get("candidate_id") == action.candidate_id
            and payload.get("note") == action.note
            and existing.role == action.role
        )
        if not same_request:
            raise ActionError("action_id was already used with another request")
        outcome = None
        if action.action_type == "resolve":
            result, _replayed = blackboard.request_resolve_with_status(
                action.decision_id, actor=actor, role=action.role,
                action_id=action.action_id,
                correlation_id=action.correlation_id,
                action_type=action.action_type,
                candidate_id=action.candidate_id, note=action.note,
            )
            outcome = (
                {"pending": result.reason}
                if isinstance(result, Pending)
                else {"record": result.__dict__, "replayed": True}
            )
        return {
            "board": board_dict(blackboard.project()),
            "result": outcome,
            "continuation": adapter.continue_workflow(action),
            "action_replayed": True,
        }
    decision = blackboard.project().decisions.get(action.decision_id)
    if decision is None:
        raise ActionError("no such decision")
    required_roles = sorted({
        requirement["role"]
        for requirement in decision.policy.get("required_approvals", [])
    })
    if required_roles and action.role not in required_roles:
        raise ActionError(f"human role must be one of {required_roles}")
    if action.action_type == "resolve":
        result, replayed = blackboard.request_resolve_with_status(
            action.decision_id, actor=actor, role=action.role,
            action_id=action.action_id,
            correlation_id=action.correlation_id,
            action_type=action.action_type,
            candidate_id=action.candidate_id, note=action.note,
        )
        outcome = {"pending": result.reason} if isinstance(result, Pending) else {"record": result.__dict__, "replayed": replayed}
        action_replayed = replayed
    else:
        if decision.resolution is not None:
            raise ActionError("resolved decisions are immutable")
        if action.action_type == "approve_candidate":
            candidate = decision.candidates.get(action.candidate_id)
            if candidate is None:
                raise ActionError("no such candidate")
            if candidate.consequence is None or candidate.consequence.verdict != "ok":
                raise ActionError("candidate does not pass deterministic constraints")
            scope = action.candidate_id if decision.policy.get("candidate_selector", "human_selected") == "human_selected" else action.decision_id
            _op, action_replayed = blackboard.cast_action_approval(
                action.decision_id, "approve", scope, actor=actor, role=action.role,
                action_id=action.action_id, correlation_id=action.correlation_id,
                action_type=action.action_type, candidate_id=action.candidate_id, note=action.note,
            )
        elif action.action_type == "hold":
            _op, action_replayed = blackboard.cast_action_approval(
                action.decision_id, "hold", action.decision_id, actor=actor, role=action.role,
                action_id=action.action_id, correlation_id=action.correlation_id,
                action_type=action.action_type, note=action.note,
            )
        else:  # request_revision is an explicit reject intent; the continuation owns the revision work.
            _op, action_replayed = blackboard.cast_action_approval(
                action.decision_id, "reject", action.decision_id, actor=actor, role=action.role,
                action_id=action.action_id, correlation_id=action.correlation_id,
                action_type=action.action_type, note=action.note,
            )
        outcome = None
    return {"board": board_dict(blackboard.project()), "result": outcome,
            "continuation": adapter.continue_workflow(action),
            "action_replayed": action_replayed}
