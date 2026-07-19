from __future__ import annotations
from pathlib import Path
import pytest
from openpyxl import Workbook
from yigdesk.app import create_app
from yigdesk.board import build_blackboard

ROOT = Path(__file__).resolve().parents[1]
POLICY = {"decision_type": "council_discount",
          "required_approvals": [{"role": "cfo", "verdict": "approve"}],
          "required_claims": [{"type": "risk"}],
          "candidate_selector": "max:headroom"}

def _scenario(tmp_path):
    scn = tmp_path / "scn"; scn.mkdir()
    (scn / "model.json").write_text(
        (ROOT / "data" / "scenarios" / "council_discount" / "model.json").read_text("utf-8"), "utf-8")
    wb = Workbook(); ws = wb.active; ws.title = "Deal Inputs"
    for a, v in {"B2": 1000, "B3": 0, "B4": 480, "B5": 40}.items():
        ws[a] = v
    wb.save(scn / "council_deal.xlsx")
    return scn

@pytest.fixture
def client(tmp_path, monkeypatch):
    scn = _scenario(tmp_path)
    ledger = tmp_path / "board.jsonl"
    monkeypatch.setenv("YIGDESK_SCENARIO", str(scn))
    monkeypatch.setenv("YIGDESK_LEDGER", str(ledger))
    bb = build_blackboard(scn, ledger)     # seed the shared ledger as the agent side would
    bb.open_decision("d1", "Approve the discount?", "council_discount", POLICY,
                     actor="agent:mcp", role="owner")
    bb.propose_candidate("d1", "c1", {"overrides": {"discount": 2}}, actor="finance", role="proposer")
    bb.post_claim("d1", "k1", "risk", "c1", "cogs may rise", ["Deal Inputs!B4"], actor="risk", role="critic")
    app = create_app(runtime_dir=tmp_path)   # adapt to the real create_app signature
    return app.test_client()

def test_get_board_returns_policy_and_priced_candidate(client):
    d = client.get("/api/board").get_json()["decisions"]["d1"]
    assert d["decision_type"] == "council_discount"
    assert d["policy"]["candidate_selector"] == "max:headroom"
    assert d["candidates"]["c1"]["consequence"]["verdict"] == "ok"
    assert d["claims"]["k1"]["status"] == "grounded"

def test_resolve_pends_before_approval_then_commits_then_replays(client):
    pend = client.post("/api/board/op", json={"decision_id": "d1", "kind": "request_resolve", "payload": {}}).get_json()
    assert pend["result"]["pending"]
    ok = client.post("/api/board/op", json={"decision_id": "d1", "kind": "cast_approval",
        "payload": {"verdict": "approve", "scope": "d1", "role": "cfo"}}).get_json()
    assert ok["result"] is None
    done = client.post("/api/board/op", json={"decision_id": "d1", "kind": "request_resolve", "payload": {}}).get_json()
    assert done["result"]["record"]["chosen_candidate_id"] == "c1"
    assert done["result"]["replayed"] is False
    again = client.post("/api/board/op", json={"decision_id": "d1", "kind": "request_resolve", "payload": {}}).get_json()
    assert again["result"]["replayed"] is True
    assert again["result"]["record"] == done["result"]["record"]

@pytest.mark.parametrize("body", [
    {"decision_id": "d1", "kind": "propose_candidate", "payload": {}},                                  # not allowlisted
    {"decision_id": "nope", "kind": "request_resolve", "payload": {}},                                  # unknown decision
    {"decision_id": "d1", "kind": "cast_approval", "payload": {"verdict": "maybe", "scope": "d1", "role": "cfo"}},   # bad verdict
    {"decision_id": "d1", "kind": "cast_approval", "payload": {"verdict": "approve", "scope": "ghost", "role": "cfo"}}, # unknown candidate scope
    {"decision_id": "d1", "kind": "cast_approval", "payload": {"verdict": "approve", "scope": "d1", "role": "intern"}}, # role not in policy
])
def test_board_op_rejects_bad_requests(client, body):
    assert client.post("/api/board/op", json=body).status_code == 400
