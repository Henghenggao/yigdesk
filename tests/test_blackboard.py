from openpyxl import Workbook
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
