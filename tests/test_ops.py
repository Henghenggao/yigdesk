from yigdesk.core.ops import Op, op_to_json, op_from_json, KINDS, PROPOSE_CANDIDATE

def test_op_roundtrip_is_canonical_and_stable():
    op = Op(seq=3, kind=PROPOSE_CANDIDATE, actor="agent:a", role="proposer",
            payload={"b": 2, "a": 1}, base_seq=2)
    line = op_to_json(op)
    assert line == '{"actor":"agent:a","base_seq":2,"kind":"propose_candidate","payload":{"a":1,"b":2},"role":"proposer","seq":3}'
    assert op_from_json(line) == op

def test_kinds_are_the_six_ops_plus_resolved():
    assert KINDS == {"open_decision","propose_candidate","post_claim","cast_approval","request_resolve","resolved"}
