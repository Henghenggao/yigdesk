"""Independent verifier for the ephemeral Codex decision-council MCP audit."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any


EXPECTED_TOOL_SEQUENCES = {
    "finance_analyst": [
        "get_deal_context",
        "find_feasible_boundary",
        "evaluate_proposal",
        "inspect_evidence",
    ],
    "sales_advocate": [
        "get_deal_context",
        "evaluate_proposal",
        "evaluate_proposal",
    ],
    "risk_challenger": [
        "get_deal_context",
        "list_missing_evidence",
        "find_feasible_boundary",
        "stress_test_assumption",
        "inspect_evidence",
    ],
    "decision_optimizer": [
        "get_deal_context",
        "compare_proposals",
        "inspect_evidence",
    ],
}


class CouncilAuditError(ValueError):
    """The observed A2A tool trace cannot support a council result."""


def council_audit_status(
    events: list[dict[str, Any]],
    *,
    expected_revision: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a content-safe progress view for one actor-attributed audit."""

    expected_call_count = sum(len(tools) for tools in EXPECTED_TOOL_SEQUENCES.values())
    revision = dict(expected_revision or {})
    grouped: dict[str, list[str]] = defaultdict(list)
    identities: set[tuple[str, str, str]] = set()
    for event in events:
        actor = event.get("actor")
        if actor not in EXPECTED_TOOL_SEQUENCES:
            raise CouncilAuditError("Council audit contains an unattributed or unknown actor.")
        if event.get("ok") is not True:
            raise CouncilAuditError(f"Council audit contains a failed call for {actor}.")
        if any(
            not event.get(field)
            for field in ("revision_id", "source_fingerprint", "packet_id")
        ):
            raise CouncilAuditError(f"Council audit is missing revision proof for {actor}.")
        grouped[actor].append(str(event.get("tool", "")))
        identities.add(
            (
                str(event["revision_id"]),
                str(event["source_fingerprint"]),
                str(event["packet_id"]),
            )
        )
    if len(identities) > 1:
        raise CouncilAuditError("Council audit contains revision drift.")
    if identities:
        revision_id, source_fingerprint, packet_id = next(iter(identities))
        observed_revision = {
            "revision_id": revision_id,
            "source_fingerprint": source_fingerprint,
            "packet_id": packet_id,
        }
        if expected_revision is not None and observed_revision != expected_revision:
            raise CouncilAuditError("Council audit does not match the active revision.")
        revision = observed_revision

    roles: dict[str, dict[str, Any]] = {}
    accepted_progress_count = 0
    for actor, expected in EXPECTED_TOOL_SEQUENCES.items():
        actual = grouped.get(actor, [])
        completed = _matching_prefix_suffix_length(actual, expected)
        accepted_progress_count += completed
        roles[actor] = {
            "state": (
                "complete"
                if completed == len(expected)
                else "running" if actual else "pending"
            ),
            "completed": completed,
            "expected": len(expected),
            "tools": expected[:completed],
        }

    result = {
        "status": "idle" if not events else "running",
        "verified": False,
        "revision": revision,
        "observed_call_count": len(events),
        "accepted_progress_count": accepted_progress_count,
        "expected_call_count": expected_call_count,
        "roles": roles,
    }
    if events and accepted_progress_count == expected_call_count:
        verified = verify_council_audit(events)
        result.update(
            {
                "status": "verified",
                "verified": True,
                "revision": verified["revision"],
                "observed_call_count": verified["observed_call_count"],
                "accepted_progress_count": verified["accepted_call_count"],
            }
        )
    return result


def verify_council_audit(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Accept only exact per-role suffixes on one immutable revision.

    Earlier calls for a role remain visible as rejected attempts. This lets Codex
    rerun a non-conforming specialist without erasing the evidence of that attempt.
    """

    if not events:
        raise CouncilAuditError("Council MCP audit is empty.")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        actor = event.get("actor")
        if actor not in EXPECTED_TOOL_SEQUENCES:
            raise CouncilAuditError("Council audit contains an unattributed or unknown actor.")
        if event.get("ok") is not True:
            raise CouncilAuditError(f"Council audit contains a failed call for {actor}.")
        if any(not event.get(field) for field in ("revision_id", "source_fingerprint", "packet_id")):
            raise CouncilAuditError(f"Council audit is missing revision proof for {actor}.")
        grouped[actor].append(event)

    accepted: dict[str, list[dict[str, Any]]] = {}
    rejected_prior_calls: dict[str, int] = {}
    identities: set[tuple[str, str, str]] = set()
    for actor, expected in EXPECTED_TOOL_SEQUENCES.items():
        actor_events = grouped.get(actor, [])
        if len(actor_events) < len(expected):
            raise CouncilAuditError(f"Council audit is incomplete for {actor}.")
        suffix = actor_events[-len(expected) :]
        actual = [event.get("tool") for event in suffix]
        if actual != expected:
            raise CouncilAuditError(
                f"Council audit suffix for {actor} must be {', '.join(expected)}."
            )
        accepted[actor] = suffix
        rejected_prior_calls[actor] = len(actor_events) - len(expected)
        identities.update(
            (
                event["revision_id"],
                event["source_fingerprint"],
                event["packet_id"],
            )
            for event in suffix
        )
    if len(identities) != 1:
        raise CouncilAuditError("Accepted council calls contain revision drift.")
    submitted_discount_pct, alternative_discount_pct = _verify_call_parameters(accepted)
    revision_id, source_fingerprint, packet_id = identities.pop()
    return {
        "verified": True,
        "revision": {
            "revision_id": revision_id,
            "source_fingerprint": source_fingerprint,
            "packet_id": packet_id,
        },
        "accepted_call_count": sum(len(events) for events in accepted.values()),
        "observed_call_count": len(events),
        "submitted_discount_pct": str(submitted_discount_pct),
        "alternative_discount_pct": str(alternative_discount_pct),
        "rejected_prior_calls": rejected_prior_calls,
        "roles": {
            actor: [event["tool"] for event in role_events]
            for actor, role_events in accepted.items()
        },
    }


def _matching_prefix_suffix_length(actual: list[str], expected: list[str]) -> int:
    """Find current valid progress while allowing a prior role retry to remain visible."""

    for length in range(min(len(actual), len(expected)), 0, -1):
        if actual[-length:] == expected[:length]:
            return length
    return 0


def _verify_call_parameters(
    accepted: dict[str, list[dict[str, Any]]],
) -> tuple[Decimal, Decimal]:
    finance = accepted["finance_analyst"]
    sales = accepted["sales_advocate"]
    risk = accepted["risk_challenger"]
    optimizer = accepted["decision_optimizer"]

    if _decimal(finance[1], "step_pct") != Decimal("0.01") or _decimal(
        risk[2], "step_pct"
    ) != Decimal("0.01"):
        raise CouncilAuditError("Council boundary calls must use a 0.01-point step.")

    submitted = _decimal(finance[2], "requested_discount_pct")
    if _decimal(sales[1], "requested_discount_pct") != submitted or _decimal(
        risk[3], "requested_discount_pct"
    ) != submitted:
        raise CouncilAuditError("Council roles did not test the same submitted discount.")
    alternative = _decimal(sales[2], "requested_discount_pct")
    if alternative == submitted:
        raise CouncilAuditError("Sales alternative must differ from the submitted discount.")
    if _decimal(risk[3], "cogs_change_pct") != Decimal("5"):
        raise CouncilAuditError("Risk challenge must apply exactly +5% COGS.")
    safe_boundary = _decimal(finance[1], "largest_safe_step_pct")
    if _decimal(risk[2], "largest_safe_step_pct") != safe_boundary:
        raise CouncilAuditError("Council roles did not observe the same safe boundary.")

    for event in (finance[3], risk[4], optimizer[2]):
        if event.get("address") != "Deal Model!B4":
            raise CouncilAuditError("Council evidence inspection must use Deal Model!B4.")

    raw_comparison = optimizer[1].get("discounts_pct")
    if not isinstance(raw_comparison, list):
        raise CouncilAuditError("Optimizer audit is missing its compared proposals.")
    compared = [_decimal_value(value, "discounts_pct") for value in raw_comparison]
    if len(compared) != len(set(compared)):
        raise CouncilAuditError("Optimizer must compare unique proposals.")
    first_unsafe = safe_boundary + Decimal("0.01")
    if set(compared) != {submitted, alternative, safe_boundary, first_unsafe}:
        raise CouncilAuditError(
            "Optimizer comparison must match the submitted, sales alternative, safe-boundary, and first-unsafe proposals."
        )
    return submitted, alternative


def _decimal(event: dict[str, Any], field: str) -> Decimal:
    if field not in event:
        raise CouncilAuditError(f"Council audit is missing {field}.")
    return _decimal_value(event[field], field)


def _decimal_value(raw: Any, field: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as error:
        raise CouncilAuditError(f"Council audit contains invalid {field}.") from error
    if not value.is_finite():
        raise CouncilAuditError(f"Council audit contains invalid {field}.")
    return value
