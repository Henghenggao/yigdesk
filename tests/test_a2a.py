from __future__ import annotations

import pytest

from yigdesk.a2a import CouncilAuditError, EXPECTED_TOOL_SEQUENCES, verify_council_audit


def events_for(actor, tools, *, packet="packet-1"):
    return [
        {
            "actor": actor,
            "tool": tool,
            "ok": True,
            "revision_id": "revision-1",
            "source_fingerprint": "fingerprint-1",
            "packet_id": packet,
        }
        for tool in tools
    ]


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
