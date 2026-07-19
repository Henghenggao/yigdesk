from yigdesk.core.ledger import Ledger
from yigdesk.core.ops import OPEN_DECISION, REQUEST_RESOLVE

def test_append_assigns_monotonic_seq_and_persists(tmp_path):
    led = Ledger(tmp_path / "board.jsonl")
    a = led.append(OPEN_DECISION, "human:cfo", "owner", {"question": "q1"})
    b = led.append(OPEN_DECISION, "human:cfo", "owner", {"question": "q2"})
    assert (a.seq, b.seq) == (1, 2)
    assert [o.payload["question"] for o in Ledger(tmp_path / "board.jsonl").read()] == ["q1", "q2"]

def test_read_of_empty_ledger_is_empty(tmp_path):
    assert Ledger(tmp_path / "empty.jsonl").read() == []

def test_transaction_snapshots_then_appends_atomically(tmp_path):
    # The request_resolve pattern: read a snapshot, allocate the next seq, and
    # append a resolve op that pins base_seq to that snapshot -- all under one lock.
    led = Ledger(tmp_path / "board.jsonl")
    led.append(OPEN_DECISION, "human:cfo", "owner", {"question": "q1"})
    with led.transaction() as txn:
        snapshot = txn.read()
        pinned = snapshot[-1].seq
        op = txn.append(REQUEST_RESOLVE, "human:cfo", "owner",
                        {"decision_id": "d1"}, base_seq=pinned)
    assert [o.seq for o in snapshot] == [1]
    assert (op.seq, op.base_seq) == (2, 1)
    assert [(o.seq, o.kind) for o in Ledger(tmp_path / "board.jsonl").read()] == \
        [(1, OPEN_DECISION), (2, REQUEST_RESOLVE)]
