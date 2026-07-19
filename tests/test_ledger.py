from yigdesk.core.ledger import Ledger
from yigdesk.core.ops import OPEN_DECISION

def test_append_assigns_monotonic_seq_and_persists(tmp_path):
    led = Ledger(tmp_path / "board.jsonl")
    a = led.append(OPEN_DECISION, "human:cfo", "owner", {"question": "q1"})
    b = led.append(OPEN_DECISION, "human:cfo", "owner", {"question": "q2"})
    assert (a.seq, b.seq) == (1, 2)
    assert [o.payload["question"] for o in Ledger(tmp_path / "board.jsonl").read()] == ["q1", "q2"]

def test_read_of_empty_ledger_is_empty(tmp_path):
    assert Ledger(tmp_path / "empty.jsonl").read() == []
