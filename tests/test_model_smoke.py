from yigdesk.core.model import Consequence, Metric, Board

def test_consequence_and_board_construct():
    c = Consequence(verdict="ok", metrics=[Metric("net_arr","Net ARR","880","900","880","$k")],
                    evidence_refs=["Deal Inputs!B2"], fingerprint="abc")
    assert c.verdict == "ok" and Board(decisions={}).decisions == {}
