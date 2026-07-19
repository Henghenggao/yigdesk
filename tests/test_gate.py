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
