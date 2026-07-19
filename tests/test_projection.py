from yigdesk.core.ops import Op, OPEN_DECISION, PROPOSE_CANDIDATE, CAST_APPROVAL
from yigdesk.core.projection import fold

def _ops():
    return [
        Op(1, OPEN_DECISION, "human:cfo", "owner", {"decision_id":"d1","question":"Approve 12%?","decision_type":"discount","policy":{}}),
        Op(2, PROPOSE_CANDIDATE, "agent:a", "proposer", {"decision_id":"d1","candidate_id":"c1","action":{"overrides":{"discount":12}}}),
        Op(3, CAST_APPROVAL, "human:cfo", "cfo", {"decision_id":"d1","verdict":"approve","scope":"c1"}),
    ]

def test_fold_builds_decision_with_candidate_and_approval():
    board = fold(_ops())
    d = board.decisions["d1"]
    assert d.question == "Approve 12%?" and "c1" in d.candidates
    assert d.approvals[0].verdict == "approve"

def test_fold_is_pure_same_input_same_output():
    assert fold(_ops()) == fold(_ops())
