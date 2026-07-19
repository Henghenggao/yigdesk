from __future__ import annotations

import pytest

from yigdesk.a2a import (
    CouncilAuditError,
    EXPECTED_TOOL_SEQUENCES,
    council_audit_status,
    verify_council_audit,
)


def events_for(actor, tools, *, packet="packet-1"):
    evaluations = 0
    events = []
    for tool in tools:
        event = {
            "actor": actor,
            "tool": tool,
            "ok": True,
            "revision_id": "revision-1",
            "source_fingerprint": "fingerprint-1",
            "packet_id": packet,
        }
        if tool == "find_feasible_boundary":
            event["step_pct"] = "0.01"
            event["largest_safe_step_pct"] = "2.23"
        elif tool == "evaluate_proposal":
            evaluations += 1
            event["requested_discount_pct"] = (
                "2.1" if actor == "sales_advocate" and evaluations == 2 else "2"
            )
        elif tool == "stress_test_assumption":
            event["requested_discount_pct"] = "2"
            event["cogs_change_pct"] = "5"
        elif tool == "inspect_evidence":
            event["address"] = "Deal Model!B4"
        elif tool == "compare_proposals":
            event["discounts_pct"] = ["2", "2.1", "2.23", "2.24"]
        events.append(event)
    return events


def complete_audit():
    return [
        event
        for actor, tools in EXPECTED_TOOL_SEQUENCES.items()
        for event in events_for(actor, tools)
    ]


def test_verifier_accepts_exact_role_sequences_on_one_revision():
    result = verify_council_audit(complete_audit())

    assert result["verified"] is True
    assert result["accepted_call_count"] == 15
    assert result["observed_call_count"] == 15
    assert set(result["roles"]) == set(EXPECTED_TOOL_SEQUENCES)


def test_council_status_reports_partial_role_progress_without_tool_arguments():
    events = [
        *events_for(
            "finance_analyst",
            ["get_deal_context", "find_feasible_boundary"],
        ),
        *events_for("sales_advocate", ["get_deal_context"]),
    ]

    status = council_audit_status(events)

    assert status["status"] == "running"
    assert status["observed_call_count"] == 3
    assert status["accepted_progress_count"] == 3
    assert status["roles"]["finance_analyst"] == {
        "state": "running",
        "completed": 2,
        "expected": 4,
        "tools": ["get_deal_context", "find_feasible_boundary"],
    }
    assert status["roles"]["decision_optimizer"]["state"] == "pending"
    assert "step_pct" not in str(status)


def test_council_status_promotes_only_a_fully_verified_audit():
    status = council_audit_status(complete_audit())

    assert status["status"] == "verified"
    assert status["verified"] is True
    assert status["accepted_progress_count"] == 15
    assert status["revision"]["revision_id"] == "revision-1"


def test_verifier_keeps_an_earlier_nonconforming_attempt_visible():
    events = complete_audit()
    sales_start = len(EXPECTED_TOOL_SEQUENCES["finance_analyst"])
    events[sales_start:sales_start] = events_for(
        "sales_advocate", ["get_deal_context", "preview_consequence"]
    )

    result = verify_council_audit(events)

    assert result["verified"] is True
    assert result["observed_call_count"] == 17
    assert result["rejected_prior_calls"]["sales_advocate"] == 2


def test_verifier_rejects_revision_drift_or_unattributed_calls():
    drifted = complete_audit()
    drifted[-1] = {**drifted[-1], "packet_id": "packet-2"}
    with pytest.raises(CouncilAuditError, match="revision drift"):
        verify_council_audit(drifted)

    unattributed = complete_audit() + [
        {
            "tool": "get_deal_context",
            "ok": True,
            "revision_id": "revision-1",
            "source_fingerprint": "fingerprint-1",
            "packet_id": "packet-1",
        }
    ]
    with pytest.raises(CouncilAuditError, match="unattributed"):
        verify_council_audit(unattributed)


@pytest.mark.parametrize(
    ("actor", "tool", "field", "value", "message"),
    [
        ("finance_analyst", "find_feasible_boundary", "step_pct", "0.1", "0.01"),
        ("risk_challenger", "stress_test_assumption", "cogs_change_pct", "4", "5%"),
        ("risk_challenger", "inspect_evidence", "address", "Deal Model!B3", "B4"),
        (
            "decision_optimizer",
            "compare_proposals",
            "discounts_pct",
            ["2", "2.2", "2.23", "2.24"],
            "sales alternative",
        ),
    ],
)
def test_verifier_rejects_parameter_drift(actor, tool, field, value, message):
    events = complete_audit()
    event = next(item for item in events if item["actor"] == actor and item["tool"] == tool)
    event[field] = value

    with pytest.raises(CouncilAuditError, match=message):
        verify_council_audit(events)
