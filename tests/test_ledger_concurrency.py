from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

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
