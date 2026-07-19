from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

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


def _worker_env():
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + existing if existing else "")
    return env


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
