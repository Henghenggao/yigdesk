from __future__ import annotations

from pathlib import Path

from yigdesk.board import build_blackboard
from yigdesk.continuation import continue_decision


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "data" / "scenarios" / "council_discount"
POLICY = {
    "required_approvals": [{"role": "cfo", "verdict": "approve"}],
    "required_claims": [{"type": "risk"}],
    "candidate_selector": "max:headroom",
}


def test_continuation_resolves_after_durable_browser_approval(tmp_path):
    bb = build_blackboard(SCENARIO, tmp_path / "board.jsonl")
    bb.open_decision("d1", "Approve Northwind?", "council_discount", POLICY,
                     actor="orchestrator", role="owner")
    bb.propose_candidate("d1", "d12", {"overrides": {"discount": 12}},
                         actor="finance", role="proposer")
    bb.post_claim("d1", "risk", "risk", "d12", "COGS sensitivity",
                  ["Deal Inputs!B4"], actor="risk", role="critic")
    approval, _ = bb.cast_action_approval(
        "d1", "approve", "d1", actor="human:web", role="cfo",
        action_id="approve-1", correlation_id="journey-1",
        action_type="approve_candidate", candidate_id="d12",
    )

    result = continue_decision(
        bb, "d1", after_seq=approval.seq - 1, timeout_seconds=0.1
    )

    assert result["status"] == "resolved"
    assert result["action_id"] == "approve-1"
    assert result["record"]["chosen_candidate_id"] == "d12"
    assert bb.project().decisions["d1"].resolution is not None


def test_continuation_timeout_is_an_explicit_terminal_pending_result(tmp_path):
    bb = build_blackboard(SCENARIO, tmp_path / "board.jsonl")

    result = continue_decision(
        bb, "missing", after_seq=0, timeout_seconds=0.01, poll_seconds=0.001
    )

    assert result == {
        "status": "pending",
        "decision_id": "missing",
        "reason": "human action deadline missed",
    }
