from __future__ import annotations
import json
from pathlib import Path
from openpyxl import Workbook
from yigdesk.board import build_blackboard, decision_dict

ROOT = Path(__file__).resolve().parents[1]
POLICY = {"decision_type": "council_discount",
          "required_approvals": [{"role": "cfo", "verdict": "approve"}],
          "required_claims": [{"type": "risk"}],
          "candidate_selector": "max:headroom"}

def _scenario(tmp_path) -> Path:
    scn = tmp_path / "scn"; scn.mkdir()
    (scn / "model.json").write_text(
        (ROOT / "data" / "scenarios" / "council_discount" / "model.json").read_text("utf-8"), "utf-8")
    wb = Workbook(); ws = wb.active; ws.title = "Deal Inputs"
    for a, v in {"B2": 1000, "B3": 0, "B4": 480, "B5": 40}.items():
        ws[a] = v
    wb.save(scn / "council_deal.xlsx")
    return scn

def test_build_blackboard_prices_and_serializes_policy(tmp_path):
    scn = _scenario(tmp_path)
    bb = build_blackboard(scn, tmp_path / "board.jsonl")
    bb.open_decision("d1", "Approve discount?", "council_discount", POLICY,
                     actor="agent:mcp", role="owner")
    bb.propose_candidate("d1", "c1", {"overrides": {"discount": 2}},
                         actor="agent:mcp", role="proposer")
    d = bb.project().decisions["d1"]
    dto = decision_dict(d)
    assert dto["decision_type"] == "council_discount"
    assert dto["policy"]["candidate_selector"] == "max:headroom"
    assert dto["candidates"]["c1"]["consequence"]["verdict"] in {"ok", "hold"}
    assert "claims" in dto and "approvals" in dto
