from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from openpyxl import Workbook

from yigdesk.board import build_blackboard
from yigdesk.core.gate import Pending
from yigdesk.core.ledger import Ledger

# The worker is written into tmp_path and run as a standalone script, so its
# sys.path[0] is tmp_path (not the repo root) and `import yigdesk` would fail.
# yigdesk is not pip-installed here (pytest only injects the repo via
# pyproject `pythonpath`), so the subprocess needs PYTHONPATH set explicitly.
REPO_ROOT = Path(__file__).resolve().parents[1]

WORKER = """
import sys
from yigdesk.core.ledger import Ledger
led = Ledger(sys.argv[1])
actor = sys.argv[2]
for i in range(int(sys.argv[3])):
    led.append("open_decision", actor, "owner", {"decision_id": f"{actor}-{i}"})
"""


# A resolution folds the WHOLE ledger, so the ops these writers append have to be
# well-formed (unrelated) decisions rather than the bare payloads WORKER uses. The
# pause keeps each writer in flight long enough to overlap the resolution.
RESOLVE_WORKER = """
import sys
import time
from yigdesk.core.ledger import Ledger
led = Ledger(sys.argv[1])
actor = sys.argv[2]
for i in range(int(sys.argv[3])):
    led.append("open_decision", actor, "owner", {
        "decision_id": f"{actor}-{i}",
        "question": "unrelated",
        "decision_type": "unrelated",
        "policy": {},
    })
    time.sleep(0.01)
"""

POLICY = {
    "decision_type": "council_discount",
    "required_approvals": [{"role": "cfo", "verdict": "approve"}],
    "required_claims": [{"type": "risk"}],
    "candidate_selector": "max:headroom",
}


def _worker_env():
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + existing if existing else "")
    return env


def _resolvable_board(tmp_path, ledger):
    """Seed `ledger` with a council_discount d1 driven to a resolvable state.

    Mirrors tests/test_blackboard.py::_resolvable_blackboard and the
    tests/test_board_api.py fixture: discount=2 prices to an `ok` verdict, the
    risk claim grounds, and the cfo approval satisfies `max:headroom`.
    """
    scn = tmp_path / "scn"
    scn.mkdir()
    (scn / "model.json").write_text(
        (REPO_ROOT / "data" / "scenarios" / "council_discount" / "model.json").read_text("utf-8"),
        "utf-8",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Deal Inputs"
    for addr, value in {"B2": 1000, "B3": 0, "B4": 480, "B5": 40}.items():
        sheet[addr] = value
    workbook.save(scn / "council_deal.xlsx")

    bb = build_blackboard(scn, ledger)
    bb.open_decision("d1", "Approve the discount?", "council_discount", POLICY,
                     actor="agent:mcp", role="owner")
    bb.propose_candidate("d1", "c1", {"overrides": {"discount": 2}},
                         actor="finance", role="proposer")
    claim = bb.post_claim("d1", "k1", "risk", "c1", "cogs may rise", ["Deal Inputs!B4"],
                          actor="risk", role="critic")
    assert claim.status == "grounded"
    bb.cast_approval("d1", "approve", "d1", actor="human:web", role="cfo")
    return bb


def test_resolution_under_concurrent_writers_keeps_record_seq_equal_to_its_op(tmp_path):
    """Spec §7: a concurrently exercised resolution has
    `DecisionRecord.seq == resolved_op.seq`.

    The resolution transaction spans snapshot + gate evaluation + seq assignment +
    append, so writers hammering the SAME ledger from other processes can never
    slip an op between the seq stamped into the record and the `resolved` op that
    carries it — and none of their own ops may be lost to the contention."""
    ledger = tmp_path / "board.jsonl"
    bb = _resolvable_board(tmp_path, ledger)
    seeded = len(Ledger(ledger).read())

    worker = tmp_path / "resolve_worker.py"
    worker.write_text(RESOLVE_WORKER, encoding="utf-8")
    writers, per = 3, 30
    env = _worker_env()
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(ledger), f"w{n}", str(per)],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        for n in range(writers)
    ]

    # Resolve only once the writers are demonstrably live on the shared ledger.
    deadline = time.monotonic() + 30
    while len(Ledger(ledger).read()) == seeded:
        assert time.monotonic() < deadline, "writers never appended to the shared ledger"
        time.sleep(0.005)

    record = bb.request_resolve("d1", actor="human:web", role="cfo")
    still_writing = [p for p in procs if p.poll() is None]

    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, f"worker exited {p.returncode}:\n{err}"

    assert not isinstance(record, Pending), f"resolution did not commit: {record}"
    assert still_writing, "the resolution never overlapped an in-flight writer"

    ops = Ledger(ledger).read()
    resolved_ops = [op for op in ops if op.kind == "resolved"]
    assert len(resolved_ops) == 1                    # exactly one resolution committed
    assert record.seq == resolved_ops[0].seq         # record seq == its own resolved op

    for n in range(writers):                         # every writer op survived
        appended = [op for op in ops if op.actor == f"w{n}"]
        assert len(appended) == per, f"w{n} lost ops: {len(appended)} != {per}"

    seqs = [op.seq for op in ops]
    assert len(ops) == seeded + writers * per + 1
    assert len(set(seqs)) == len(seqs)               # unique
    assert seqs == sorted(seqs)                      # strictly increasing
    assert seqs == list(range(1, len(ops) + 1))      # contiguous, no gaps


def test_parallel_writers_produce_unique_increasing_seqs(tmp_path):
    ledger = tmp_path / "board.jsonl"
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER, encoding="utf-8")
    per = 40
    env = _worker_env()
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(ledger), f"w{n}", str(per)],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        for n in range(3)
    ]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, f"worker exited {p.returncode}:\n{err}"
    lines = [l for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    ops = [json.loads(l) for l in lines]      # every line must be complete JSON
    seqs = [o["seq"] for o in ops]
    assert len(ops) == 3 * per
    assert len(set(seqs)) == len(seqs)
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, 3 * per + 1))


def test_reader_polling_during_writes_never_sees_torn_state(tmp_path):
    """Spec §7: a reader may poll read() while the writers append. Every observed
    snapshot must parse cleanly (no torn trailing line) and be a strictly
    increasing contiguous prefix [1..k] whose length only grows over time."""
    ledger = tmp_path / "board.jsonl"
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER, encoding="utf-8")
    per = 40
    total = 3 * per
    env = _worker_env()

    errors: list[str] = []
    max_seen = 0
    reads = 0
    stop = threading.Event()

    def poll():
        nonlocal max_seen, reads
        led = Ledger(ledger)
        last_len = 0
        try:
            while not stop.is_set():
                seqs = [op.seq for op in led.read()]   # must never raise mid-write
                reads += 1
                # a consistent snapshot is always the contiguous prefix [1..k]
                assert seqs == list(range(1, len(seqs) + 1)), f"non-prefix snapshot: {seqs}"
                # the ledger only grows: a later read never sees fewer ops
                assert len(seqs) >= last_len, f"count regressed: {len(seqs)} < {last_len}"
                last_len = len(seqs)
                max_seen = max(max_seen, len(seqs))
        except BaseException as exc:                   # noqa: BLE001 - report, never mask
            errors.append(repr(exc))

    reader = threading.Thread(target=poll, daemon=True)
    reader.start()
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(ledger), f"w{n}", str(per)],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        for n in range(3)
    ]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, f"worker exited {p.returncode}:\n{err}"
    stop.set()
    reader.join(timeout=30)
    assert not reader.is_alive(), "polling reader thread did not stop"

    assert not errors, f"reader observed a torn/inconsistent ledger snapshot: {errors}"
    assert reads > 0, "reader never polled"
    assert max_seen >= 1, "reader never overlapped with in-flight writes"
    final = [op.seq for op in Ledger(ledger).read()]
    assert final == list(range(1, total + 1))
