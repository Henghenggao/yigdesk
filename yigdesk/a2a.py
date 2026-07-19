"""Independent verifier for the ephemeral Codex decision-council MCP audit."""

from __future__ import annotations

from collections import defaultdict
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
        "rejected_prior_calls": rejected_prior_calls,
        "roles": {
            actor: [event["tool"] for event in role_events]
            for actor, role_events in accepted.items()
        },
    }
