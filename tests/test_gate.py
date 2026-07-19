from yigdesk.core.model import Decision, Candidate, Consequence, Metric, Approval
from yigdesk.core.gate import resolve, Pending

def _decision(selector, approvals):
    d = Decision("d1", "q", "discount",
                 {"required_approvals":[{"role":"cfo","verdict":"approve"}],
                  "candidate_selector": selector, "constraint_metric":"headroom"})
    d.candidates["c1"] = Candidate("c1","agent:a",{"overrides":{"discount":12}},
        Consequence("ok",[Metric("headroom","Headroom","5.45","6.0","5.45","pt")],[],"fp"))
    d.approvals = approvals
    return d

def test_pending_when_required_approval_missing():
    r = resolve(_decision("max:headroom", []), "expr:rev")
    assert isinstance(r, Pending) and "approval" in r.reason

def test_policy_selector_closes_deterministically():
    d = _decision("max:headroom", [Approval("human:cfo","cfo","approve","c1")])
    rec = resolve(d, "expr:rev")
    assert not isinstance(rec, Pending)
    assert rec.chosen_candidate_id == "c1" and rec.closed_by == "policy" and rec.evaluator_revision == "expr:rev"

def _decision_hs(approvals, hold=False):
    from yigdesk.core.model import Decision, Candidate, Consequence, Metric
    d = Decision("d1","q","x",{"candidate_selector":"human_selected"})
    d.candidates["c_ok"] = Candidate("c_ok","a",{}, Consequence("ok",[Metric("m","M","1","1","1","")],[],"f"))
    d.candidates["c_hold"] = Candidate("c_hold","a",{}, Consequence("hold",[Metric("m","M",None,None,None,"")],[],"f"), status="hold")
    d.approvals = approvals
    return d

def test_human_selected_cannot_pick_hold_candidate():
    from yigdesk.core.model import Approval
    r = resolve(_decision_hs([Approval("h","cfo","approve","c_hold")]), "rev")
    assert isinstance(r, Pending)  # C1: HOLD candidate must not be selected

def test_human_selected_picks_eligible_candidate():
    from yigdesk.core.model import Approval
    rec = resolve(_decision_hs([Approval("h","cfo","approve","c_ok")]), "rev")
    assert not isinstance(rec, Pending) and rec.chosen_candidate_id == "c_ok" and rec.closed_by == "human"
