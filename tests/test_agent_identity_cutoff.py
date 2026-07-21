from __future__ import annotations

import threading
from pathlib import Path

import pytest

from yigdesk.action_bridge import (
    ACTION_VERSION,
    LocalContinuationAdapter,
    execute_action,
    parse_action,
)
from yigdesk.agent_identity import (
    AgentIdentityError,
    identity_from_env,
    validate_agent_identity,
)
from yigdesk.board import board_dict, build_blackboard
from yigdesk.core.blackboard import LateWriteError
from yigdesk.core.gate import Pending
from yigdesk.decision_manifest import build_manifest
from yigdesk.blackboard_mcp import (
    open_decision as mcp_open_decision,
    propose_candidate as mcp_propose_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "data" / "scenarios" / "council_discount"


def _identity(agent_id: str, *, run_id: str = "council-run-1"):
    return identity_from_env({
        "YIGDESK_AGENT_ID": agent_id,
        "YIGDESK_AGENT_RUN_ID": run_id,
        "YIGDESK_AGENT_INSTANCE_ID": f"{agent_id}-instance-1",
        "YIGDESK_AGENT_PROFILE": agent_id,
        "YIGDESK_AGENT_MODEL": "gpt-5.6-terra",
        "YIGDESK_PROMPT_REVISION": f"{agent_id}-prompt-v1",
        "YIGDESK_SKILLS_REVISION": "yigdesk-council-v1",
        "YIGDESK_MEMORY_REVISION": "isolated-none",
    })


def _declare_mcp_identity(monkeypatch, agent_id: str):
    identity = _identity(agent_id)
    names = {
        "agent_id": "YIGDESK_AGENT_ID",
        "run_id": "YIGDESK_AGENT_RUN_ID",
        "instance_id": "YIGDESK_AGENT_INSTANCE_ID",
        "profile": "YIGDESK_AGENT_PROFILE",
        "model": "YIGDESK_AGENT_MODEL",
        "prompt_revision": "YIGDESK_PROMPT_REVISION",
        "skills_revision": "YIGDESK_SKILLS_REVISION",
        "memory_revision": "YIGDESK_MEMORY_REVISION",
    }
    for field, name in names.items():
        monkeypatch.setenv(name, getattr(identity, field))
    return identity


def _board(tmp_path, *, required_claims=False, selector="max:headroom"):
    bb = build_blackboard(SCENARIO, tmp_path / "board.jsonl")
    policy = {
        "required_approvals": [{"role": "cfo", "verdict": "approve"}],
        "candidate_selector": selector,
    }
    if required_claims:
        policy["required_claims"] = [{"type": "risk"}]
    owner = _identity("orchestrator")
    bb.open_decision(
        "d1", "Approve the discount?", "council_discount", policy,
        actor=owner.actor, role="owner", agent_identity=owner,
    )
    return bb


def test_identity_is_declared_versioned_and_rejects_unsafe_or_extra_fields():
    identity = _identity("finance_analyst")

    assert identity.actor == "agent:finance_analyst"
    assert identity.assurance == "local_config_declared"
    assert validate_agent_identity(identity)["run_id"] == "council-run-1"

    with pytest.raises(AgentIdentityError, match="safe identifier"):
        identity_from_env({"YIGDESK_AGENT_ID": "<script>"})
    with pytest.raises(AgentIdentityError, match="exactly the v1 fields"):
        validate_agent_identity({**identity.to_dict(), "html": "<script>"})


def test_mcp_process_identity_is_bound_to_actor_and_persisted(monkeypatch, tmp_path):
    ledger = tmp_path / "mcp-board.jsonl"
    monkeypatch.setenv("YIGDESK_SCENARIO", str(SCENARIO))
    monkeypatch.setenv("YIGDESK_LEDGER", str(ledger))
    orchestrator = _declare_mcp_identity(monkeypatch, "orchestrator")
    mcp_open_decision("d1", "Question", "council_discount", {})
    finance = _declare_mcp_identity(monkeypatch, "finance_analyst")
    result = mcp_propose_candidate("d1", "submitted", {"discount": 12})

    operations = build_blackboard(SCENARIO, ledger).ledger.read()
    assert [operation.actor for operation in operations] == [
        orchestrator.actor, finance.actor,
    ]
    assert operations[0].payload["agent_identity"] == orchestrator.to_dict()
    assert operations[1].payload["agent_identity"] == finance.to_dict()
    assert result.structuredContent["callerIdentity"] == finance.to_dict()
    assert result.structuredContent["decisions"]["d1"]["candidates"][
        "submitted"
    ]["agent_identity"] == finance.to_dict()

    before = operations
    blackboard = build_blackboard(SCENARIO, ledger)
    with pytest.raises(ValueError, match="does not match declared identity"):
        blackboard.propose_candidate(
            "d1", "spoofed", {"overrides": {"discount": 10}},
            actor="agent:sales_advocate", role="proposer",
            agent_identity=finance,
        )
    assert blackboard.ledger.read() == before
    monkeypatch.setenv("YIGDESK_AGENT_ID", "<script>")
    with pytest.raises(AgentIdentityError):
        mcp_propose_candidate("d1", "unsafe", {"discount": 10})
    assert build_blackboard(SCENARIO, ledger).ledger.read() == before


def test_cutoff_pins_agent_provenance_and_audits_late_candidate_and_claim(tmp_path):
    bb = _board(tmp_path, required_claims=True)
    finance = _identity("finance_analyst")
    risk = _identity("risk_challenger")
    sales = _identity("sales_advocate")

    bb.propose_candidate(
        "d1", "submitted", {"overrides": {"discount": 12}},
        actor=finance.actor, role="proposer", agent_identity=finance,
    )
    bb.post_claim(
        "d1", "risk-1", "risk", "d1", "COGS sensitivity",
        ["Deal Inputs!B4"], actor=risk.actor, role="critic", agent_identity=risk,
    )
    approval = bb.cast_approval(
        "d1", "approve", "submitted", actor="human:cfo", role="cfo",
    )

    assert approval.payload["cutoff_seq"] == approval.seq
    with pytest.raises(LateWriteError, match="late propose_candidate was rejected"):
        bb.propose_candidate(
            "d1", "late-sales", {"overrides": {"discount": 15}},
            actor=sales.actor, role="proposer", agent_identity=sales,
        )
    with pytest.raises(LateWriteError, match="late post_claim was rejected"):
        bb.post_claim(
            "d1", "late-risk", "risk", "d1", "late",
            ["Deal Inputs!B4"], actor=risk.actor, role="critic", agent_identity=risk,
        )

    decision = bb.project().decisions["d1"]
    assert decision.cutoff_seq == approval.seq
    assert set(decision.candidates) == {"submitted"}
    assert set(decision.claims) == {"risk-1"}
    assert [write.target_id for write in decision.late_writes] == [
        "late-sales", "late-risk",
    ]
    assert all(write.seq > decision.cutoff_seq for write in decision.late_writes)
    assert all(write.cutoff_seq == decision.cutoff_seq for write in decision.late_writes)

    record = bb.request_resolve("d1", actor="human:cfo", role="cfo")
    assert record.chosen_candidate_id == "submitted"
    assert record.cutoff_seq == approval.seq
    assert {item["agent_id"] for item in record.agent_identities} == {
        "orchestrator", "finance_analyst", "risk_challenger",
    }
    assert "sales_advocate" not in {
        item["agent_id"] for item in record.agent_identities
    }
    output = board_dict(bb.project())["decisions"]["d1"]
    assert output["cutoff"]["seq"] == approval.seq
    assert len(output["late_writes"]) == 2
    manifest = build_manifest({"decisions": {"d1": output}})["decisions"][0]
    proof = next(block for block in manifest["blocks"] if block["type"] == "proof")
    warning = next(block for block in manifest["blocks"] if block["type"] == "warning")
    assert proof["record"]["cutoff_seq"] == approval.seq
    assert proof["record"]["agent_count"] == 3
    assert "2 late agent write(s); none entered the gate" in warning["body"]


def test_first_resolution_request_durably_freezes_inputs_even_when_pending(tmp_path):
    bb = _board(tmp_path, selector="human_selected")
    finance = _identity("finance_analyst")
    resolver = _identity("orchestrator")
    bb.propose_candidate(
        "d1", "submitted", {"overrides": {"discount": 12}},
        actor=finance.actor, role="proposer", agent_identity=finance,
    )

    pending = bb.request_resolve(
        "d1", actor=resolver.actor, role="resolver", agent_identity=resolver,
    )
    assert isinstance(pending, Pending)
    assert pending.reason == "required approval missing"
    cutoff = bb.ledger.read()[-1]
    assert cutoff.kind == "request_resolve"
    assert cutoff.payload["cutoff"] is True
    assert bb.project().decisions["d1"].cutoff_seq == cutoff.seq

    with pytest.raises(LateWriteError):
        bb.propose_candidate(
            "d1", "too-late", {"overrides": {"discount": 10}},
            actor=finance.actor, role="proposer", agent_identity=finance,
        )
    bb.cast_approval(
        "d1", "approve", "submitted", actor="human:cfo", role="cfo",
    )
    record = bb.request_resolve(
        "d1", actor=resolver.actor, role="resolver", agent_identity=resolver,
    )
    assert record.chosen_candidate_id == "submitted"
    assert record.cutoff_seq == cutoff.seq
    assert len([op for op in bb.ledger.read() if op.kind == "request_resolve"]) == 1


def test_request_revision_does_not_cut_off_the_replacement_work(tmp_path):
    bb = _board(tmp_path)
    finance = _identity("finance_analyst")
    bb.propose_candidate(
        "d1", "submitted", {"overrides": {"discount": 12}},
        actor=finance.actor, role="proposer", agent_identity=finance,
    )
    action = parse_action({
        "version": ACTION_VERSION,
        "action_id": "revision-1",
        "correlation_id": "council-run-1",
        "decision_id": "d1",
        "action_type": "request_revision",
        "human": {"role": "cfo", "verdict": "reject", "note": "Try 10%."},
    })
    execute_action(bb, action, LocalContinuationAdapter(), actor="human:web")

    assert bb.project().decisions["d1"].cutoff_seq is None
    blocked_approval = parse_action({
        "version": ACTION_VERSION,
        "action_id": "approval-too-soon",
        "correlation_id": "council-run-1",
        "decision_id": "d1",
        "action_type": "approve_candidate",
        "candidate_id": "submitted",
        "human": {"role": "cfo"},
    })
    with pytest.raises(ValueError, match="human action already recorded"):
        execute_action(
            bb, blocked_approval, LocalContinuationAdapter(), actor="human:web"
        )
    bb.propose_candidate(
        "d1", "revision", {"overrides": {"discount": 10}},
        actor=finance.actor, role="proposer", agent_identity=finance,
    )
    assert "revision" in bb.project().decisions["d1"].candidates
    manifest = build_manifest(board_dict(bb.project()))["decisions"][0]
    actions = next(block for block in manifest["blocks"] if block["type"] == "actions")
    assert any(item["enabled"] for item in actions["items"])

    approved = execute_action(
        bb, blocked_approval, LocalContinuationAdapter(), actor="human:web"
    )
    assert approved["action_replayed"] is False
    decision = bb.project().decisions["d1"]
    assert decision.cutoff_seq is not None
    assert [approval.verdict for approval in decision.approvals] == [
        "reject", "approve",
    ]
    record = bb.request_resolve("d1", actor="orchestrator", role="owner")
    assert record.chosen_candidate_id == "revision"


def test_write_started_before_cutoff_but_landed_after_it_is_rejected(tmp_path):
    bb = _board(tmp_path)
    finance = _identity("finance_analyst")
    bb.propose_candidate(
        "d1", "submitted", {"overrides": {"discount": 12}},
        actor=finance.actor, role="proposer", agent_identity=finance,
    )
    delegate = bb.ev
    started = threading.Event()
    release = threading.Event()

    class BlockingEvaluator:
        revision = delegate.revision

        def price(self, action, source):
            started.set()
            assert release.wait(5)
            return delegate.price(action, source)

        def ground(self, ref, source):
            return delegate.ground(ref, source)

    bb.ev = BlockingEvaluator()
    errors = []

    def late_writer():
        try:
            bb.propose_candidate(
                "d1", "racing", {"overrides": {"discount": 15}},
                actor="agent:sales_advocate", role="proposer",
                agent_identity=_identity("sales_advocate"),
            )
        except Exception as error:  # noqa: BLE001 - asserted below
            errors.append(error)

    thread = threading.Thread(target=late_writer)
    thread.start()
    assert started.wait(5)
    approval = bb.cast_approval(
        "d1", "approve", "submitted", actor="human:cfo", role="cfo",
    )
    release.set()
    thread.join(5)

    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], LateWriteError)
    decision = bb.project().decisions["d1"]
    assert set(decision.candidates) == {"submitted"}
    assert decision.late_writes[0].target_id == "racing"
    assert decision.late_writes[0].seq > approval.seq
