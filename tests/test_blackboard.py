from openpyxl import Workbook
import pytest

from yigdesk.core.blackboard import Blackboard
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource

MODEL = {
    "input_refs": {"list_arr":"Deal Inputs!B2","discount":"Deal Inputs!B3","cogs":"Deal Inputs!B4","floor":"Deal Inputs!B5"},
    "metrics": [
        {"id":"net_arr","label":"Net ARR","formula":"list_arr * (1 - discount/100)","unit":"$k"},
        {"id":"gross_margin","label":"Gross margin","formula":"(net_arr - cogs) / net_arr * 100","requires":["cogs"],"unit":"%"},
        {"id":"headroom","label":"Headroom","formula":"gross_margin - floor","requires":["cogs"],"unit":"pt"},
    ],
    "constraints": [{"metric":"headroom","op":">=","value":0}],
}

def _bb(tmp_path):
    wb = Workbook(); ws = wb.active; ws.title="Deal Inputs"
    ws["B2"]=1000; ws["B3"]=10; ws["B4"]=480; ws["B5"]=40
    p = tmp_path/"deal.xlsx"; wb.save(p)
    ev = ExpressionEvaluator(MODEL); src = ModelSource(p, MODEL["input_refs"])
    return Blackboard(tmp_path/"board.jsonl", ev, src)

def test_end_to_end_open_propose_approve_resolve(tmp_path):
    bb = _bb(tmp_path)
    bb.open_decision("d1","Approve 12%?","discount",
                     {"required_approvals":[{"role":"cfo","verdict":"approve"}],"candidate_selector":"max:headroom"},
                     actor="human:cfo", role="owner")
    bb.propose_candidate("d1","c1",{"overrides":{"discount":12}}, actor="agent:a", role="proposer")
    assert bb.project().decisions["d1"].candidates["c1"].consequence.verdict == "ok"
    assert bb.request_resolve("d1", actor="agent:a", role="proposer").reason
    bb.cast_approval("d1","approve","c1", actor="human:cfo", role="cfo")
    rec = bb.request_resolve("d1", actor="human:cfo", role="cfo")
    assert rec.chosen_candidate_id == "c1"
    assert bb.project().decisions["d1"].status == "resolved"

def test_ungrounded_claim_is_rejected(tmp_path):
    bb = _bb(tmp_path)
    bb.open_decision("d1","q","discount",{}, actor="h", role="owner")
    claim = bb.post_claim("d1","cl1","evidence","d1","note",["Deal Inputs!Z99"], actor="agent:a", role="critic")
    assert claim.status == "rejected"


@pytest.mark.parametrize("ref", ["not-a-ref", "Missing!A1", "Deal Inputs!A1:A2"])
def test_malformed_or_non_cell_claim_refs_fail_closed(tmp_path, ref):
    bb = _bb(tmp_path)
    bb.open_decision("d1", "q", "discount", {}, actor="h", role="owner")

    claim = bb.post_claim(
        "d1", "cl1", "evidence", "d1", "note", [ref],
        actor="agent:a", role="critic",
    )

    assert claim.status == "rejected"
    op = bb.ledger.read()[-1]
    assert op.kind == "post_claim"
    assert op.payload["status"] == "rejected"
    assert op.payload["grounded_refs"] == []

def _resolvable_blackboard(tmp_path):
    """A council-style d1 driven to a fully resolvable state.

    Mirrors tests/test_council_audit.py: the council_discount policy requires a
    cfo approval + a grounded risk claim + selector max:headroom. The candidate
    overrides discount to 2%, which prices to headroom ~+11.02pt (>= 0 -> "ok"),
    so request_resolve closes it via max:headroom.
    """
    bb = _bb(tmp_path)
    bb.open_decision("d1","Approve the requested discount?","council_discount",
                     {"required_approvals":[{"role":"cfo","verdict":"approve"}],
                      "required_claims":[{"type":"risk"}],
                      "candidate_selector":"max:headroom"},
                     actor="human:cfo", role="owner")
    bb.propose_candidate("d1","c1",{"overrides":{"discount":2}}, actor="agent:finance", role="proposer")
    assert bb.project().decisions["d1"].candidates["c1"].consequence.verdict == "ok"
    claim = bb.post_claim("d1","risk1","risk","d1",
                          "A +5% COGS move erodes the thin discount headroom.",
                          ["Deal Inputs!B4"], actor="agent:risk", role="critic")
    assert claim.status == "grounded"
    bb.cast_approval("d1","approve","c1", actor="human:cfo", role="cfo")
    return bb

def test_request_resolve_is_idempotent(tmp_path):
    bb = _resolvable_blackboard(tmp_path)   # helper below: a d1 that WILL resolve
    from yigdesk.core.gate import Pending
    first = bb.request_resolve("d1", actor="human:web", role="cfo")
    assert not isinstance(first, Pending)
    resolved_1 = [o for o in bb.ledger.read() if o.kind == "resolved"]
    assert len(resolved_1) == 1
    assert first.seq == resolved_1[0].seq          # record seq == resolved op seq

    second = bb.request_resolve("d1", actor="human:web", role="cfo")
    resolved_2 = [o for o in bb.ledger.read() if o.kind == "resolved"]
    assert len(resolved_2) == 1                     # NO second resolved op
    assert second == first                          # same record


def test_resolved_decision_cannot_be_reopened(tmp_path):
    bb = _resolvable_blackboard(tmp_path)
    first = bb.request_resolve("d1", actor="human:web", role="cfo")

    with pytest.raises(ValueError, match="already exists"):
        bb.open_decision(
            "d1", "A replacement question", "council_discount", {},
            actor="human:web", role="owner",
        )

    decision = bb.project().decisions["d1"]
    assert decision.status == "resolved"
    assert decision.resolution == first
    assert len([op for op in bb.ledger.read() if op.kind == "resolved"]) == 1


def test_resolved_decision_rejects_all_later_mutations(tmp_path):
    bb = _resolvable_blackboard(tmp_path)
    bb.request_resolve("d1", actor="human:web", role="cfo")
    before = bb.ledger.read()

    with pytest.raises(ValueError, match="resolved"):
        bb.propose_candidate(
            "d1", "late-candidate", {"overrides": {"discount": 1}},
            actor="agent:late", role="proposer",
        )
    with pytest.raises(ValueError, match="resolved"):
        bb.post_claim(
            "d1", "late-claim", "risk", "d1", "late",
            ["Deal Inputs!B4"], actor="agent:late", role="critic",
        )
    with pytest.raises(ValueError, match="resolved"):
        bb.cast_approval(
            "d1", "hold", "d1", actor="human:web", role="cfo",
        )

    assert bb.ledger.read() == before


def test_projection_keeps_a_resolved_record_terminal_if_log_contains_late_ops(tmp_path):
    bb = _resolvable_blackboard(tmp_path)
    record = bb.request_resolve("d1", actor="human:web", role="cfo")

    # A ledger imported from an older build may already contain invalid late
    # operations. Replaying it must preserve the first committed record.
    bb.ledger.append(
        "open_decision", "legacy", "owner",
        {"decision_id": "d1", "question": "replacement", "decision_type": "x", "policy": {}},
    )
    bb.ledger.append(
        "cast_approval", "legacy", "cfo",
        {"decision_id": "d1", "verdict": "hold", "scope": "d1"},
    )

    decision = bb.project().decisions["d1"]
    assert decision.status == "resolved"
    assert decision.resolution == record
    assert decision.question == "Approve the requested discount?"
    assert len(decision.approvals) == 1
