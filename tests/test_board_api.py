from __future__ import annotations
from pathlib import Path
import hashlib
import threading
import time

import pytest
from openpyxl import Workbook
from yigdesk.app import create_app
from yigdesk.board import build_blackboard
from yigdesk.core.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
POLICY = {"decision_type": "council_discount",
          "required_approvals": [{"role": "cfo", "verdict": "approve"}],
          "required_claims": [{"type": "risk"}],
          "candidate_selector": "max:headroom"}
HS_POLICY = {**POLICY, "candidate_selector": "human_selected"}

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

def test_get_board_omits_rejected_claims(client, tmp_path):
    """Spec §7: `GET /api/board` "omits rejected claims".

    `post_claim` rejects a claim whose refs do not ground against the read-only
    source. The op is still appended to the shared ledger (and returned to the
    agent that posted it), but `fold` deliberately keeps it out of the board, so
    the web surface never renders an ungrounded claim (spec §5).
    """
    bb = build_blackboard(tmp_path / "scn", tmp_path / "board.jsonl")  # same shared ledger

    ghost = bb.post_claim("d1", "k_ghost", "risk", "c1", "cites an empty cell",
                          ["Deal Inputs!Z99"], actor="risk", role="critic")
    empty = bb.post_claim("d1", "k_empty", "risk", "c1", "cites nothing at all",
                          [], actor="risk", role="critic")
    assert ghost.status == "rejected"   # a well-formed address that holds no value
    assert empty.status == "rejected"   # no refs at all

    # Both rejected claims reached the ledger: the omission is the projection's doing.
    posted = [op.payload["claim_id"] for op in Ledger(tmp_path / "board.jsonl").read()
              if op.kind == "post_claim"]
    assert posted == ["k1", "k_ghost", "k_empty"]

    claims = client.get("/api/board").get_json()["decisions"]["d1"]["claims"]
    assert claims["k1"]["status"] == "grounded"   # the seeded, really-grounded claim
    assert "k_ghost" not in claims
    assert "k_empty" not in claims
    assert set(claims) == {"k1"}


def test_source_bytes_are_unchanged_by_the_web_flow(client, tmp_path):
    """Spec §6 (D1) / §7: driving the whole web flow never mutates the source."""
    workbook = tmp_path / "scn" / "council_deal.xlsx"
    before = workbook.read_bytes()
    before_hash = hashlib.sha256(before).hexdigest()

    assert client.get("/api/board").status_code == 200
    approved = client.post("/api/board/op", json={"decision_id": "d1", "kind": "cast_approval",
        "payload": {"verdict": "approve", "scope": "d1", "role": "cfo"}})
    assert approved.status_code == 200
    resolved = client.post("/api/board/op",
                           json={"decision_id": "d1", "kind": "request_resolve", "payload": {}})
    # The flow really ran all the way to a committed record, not a pend.
    assert resolved.get_json()["result"]["record"]["chosen_candidate_id"] == "c1"

    after = workbook.read_bytes()
    assert hashlib.sha256(after).hexdigest() == before_hash
    assert after == before


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


def test_web_rejects_mutations_after_resolution(client):
    client.post("/api/board/op", json={"decision_id": "d1", "kind": "cast_approval",
        "payload": {"verdict": "approve", "scope": "d1", "role": "cfo"}})
    resolved = client.post(
        "/api/board/op",
        json={"decision_id": "d1", "kind": "request_resolve", "payload": {}},
    )
    assert resolved.get_json()["result"]["record"]

    late = client.post("/api/board/op", json={"decision_id": "d1", "kind": "cast_approval",
        "payload": {"verdict": "hold", "scope": "d1", "role": "cfo"}})

    assert late.status_code == 400
    assert late.get_json()["code"] == "DECISION_RESOLVED"


def test_concurrent_resolve_marks_exactly_one_response_as_replayed(tmp_path, monkeypatch):
    scn = _scenario(tmp_path)
    ledger = tmp_path / "board.jsonl"
    monkeypatch.setenv("YIGDESK_SCENARIO", str(scn))
    monkeypatch.setenv("YIGDESK_LEDGER", str(ledger))
    bb = build_blackboard(scn, ledger)
    bb.open_decision("d1", "Approve?", "council_discount", POLICY, actor="agent", role="owner")
    bb.propose_candidate("d1", "c1", {"overrides": {"discount": 2}}, actor="finance", role="proposer")
    bb.post_claim("d1", "k1", "risk", "c1", "risk", ["Deal Inputs!B4"], actor="risk", role="critic")
    bb.cast_approval("d1", "approve", "d1", actor="human", role="cfo")
    app = create_app(runtime_dir=tmp_path / "runtime")
    results = []

    def resolve_once():
        with app.test_client() as thread_client:
            response = thread_client.post(
                "/api/board/op",
                json={"decision_id": "d1", "kind": "request_resolve", "payload": {}},
            )
            results.append(response.get_json()["result"])

    # Hold the ledger lock until both requests have read the unresolved board and
    # are waiting to enter the core resolution transaction.
    with Ledger(ledger).transaction():
        threads = [threading.Thread(target=resolve_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        time.sleep(0.2)
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert len([op for op in Ledger(ledger).read() if op.kind == "resolved"]) == 1

@pytest.mark.parametrize("body", [
    {"decision_id": "d1", "kind": "propose_candidate", "payload": {}},                                  # not allowlisted
    {"decision_id": "nope", "kind": "request_resolve", "payload": {}},                                  # unknown decision
    {"decision_id": "d1", "kind": "cast_approval", "payload": {"verdict": "maybe", "scope": "d1", "role": "cfo"}},   # bad verdict
    {"decision_id": "d1", "kind": "cast_approval", "payload": {"verdict": "approve", "scope": "ghost", "role": "cfo"}}, # unknown candidate scope
    {"decision_id": "d1", "kind": "cast_approval", "payload": {"verdict": "approve", "scope": "d1", "role": "intern"}}, # role not in policy
])
def test_board_op_rejects_bad_requests(client, body):
    assert client.post("/api/board/op", json=body).status_code == 400

@pytest.mark.parametrize("bad_payload", ["x", []])
def test_board_op_rejects_non_dict_payload(client, bad_payload):
    r = client.post("/api/board/op",
                    json={"decision_id": "d1", "kind": "cast_approval", "payload": bad_payload})
    assert r.status_code == 400
    assert r.get_json()["code"] == "BAD_PAYLOAD"

def test_board_routes_return_503_when_board_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("YIGDESK_SCENARIO", raising=False)   # no seeded board env
    client = create_app(runtime_dir=tmp_path).test_client()
    got = client.get("/api/board")
    assert got.status_code == 503
    assert got.get_json()["code"] == "BOARD_NOT_CONFIGURED"
    posted = client.post("/api/board/op",
                         json={"decision_id": "d1", "kind": "request_resolve", "payload": {}})
    assert posted.status_code == 503
    assert posted.get_json()["code"] == "BOARD_NOT_CONFIGURED"

@pytest.fixture
def hs_client(tmp_path, monkeypatch):
    scn = _scenario(tmp_path)
    ledger = tmp_path / "board.jsonl"
    monkeypatch.setenv("YIGDESK_SCENARIO", str(scn))
    monkeypatch.setenv("YIGDESK_LEDGER", str(ledger))
    bb = build_blackboard(scn, ledger)
    bb.open_decision("d1", "Approve the discount?", "council_discount", HS_POLICY,
                     actor="agent:mcp", role="owner")
    # c1 (discount 2) has the higher headroom; c2 (discount 5) is the human's pick.
    bb.propose_candidate("d1", "c1", {"overrides": {"discount": 2}}, actor="finance", role="proposer")
    bb.propose_candidate("d1", "c2", {"overrides": {"discount": 5}}, actor="finance", role="proposer")
    bb.propose_candidate("d1", "hold", {"overrides": {"discount": "invalid"}}, actor="finance", role="proposer")
    bb.post_claim("d1", "k1", "risk", "c1", "cogs may rise", ["Deal Inputs!B4"], actor="risk", role="critic")
    return create_app(runtime_dir=tmp_path).test_client()

def test_human_selected_requires_scope_and_resolves_to_the_approved_candidate(hs_client):
    board = hs_client.get("/api/board").get_json()["decisions"]["d1"]
    assert board["candidates"]["c1"]["consequence"]["verdict"] == "ok"   # max-headroom candidate
    assert board["candidates"]["c2"]["consequence"]["verdict"] == "ok"
    # decision-scoped approve is rejected: human_selected demands a candidate scope
    rej = hs_client.post("/api/board/op", json={"decision_id": "d1", "kind": "cast_approval",
        "payload": {"verdict": "approve", "scope": "d1", "role": "cfo"}})
    assert rej.status_code == 400
    assert rej.get_json()["code"] == "SELECTION_REQUIRED"
    # candidate-scoped approve on c2 (NOT the max-headroom c1) succeeds
    ok = hs_client.post("/api/board/op", json={"decision_id": "d1", "kind": "cast_approval",
        "payload": {"verdict": "approve", "scope": "c2", "role": "cfo"}}).get_json()
    assert ok["result"] is None
    # resolve commits the human-selected candidate, not the policy-max one
    done = hs_client.post("/api/board/op", json={"decision_id": "d1", "kind": "request_resolve",
        "payload": {}}).get_json()
    assert done["result"]["record"]["chosen_candidate_id"] == "c2"
    assert done["result"]["record"]["closed_by"] == "human"


def test_human_selected_rejects_approval_for_hold_candidate(hs_client):
    response = hs_client.post("/api/board/op", json={
        "decision_id": "d1", "kind": "cast_approval",
        "payload": {"verdict": "approve", "scope": "hold", "role": "cfo"},
    })

    assert response.status_code == 400
    assert response.get_json()["code"] == "INELIGIBLE_SCOPE"
    board = hs_client.get("/api/board").get_json()["decisions"]["d1"]
    assert all(a["scope"] != "hold" for a in board["approvals"])
