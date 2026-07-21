from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from scripts.council_frontend import launch_frontend


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "data" / "scenarios" / "council_discount"


def _json(url: str):
    with urlopen(url, timeout=2) as response:
        return response.status, json.loads(response.read())


def test_launcher_starts_an_unseeded_identity_bound_frontend(tmp_path):
    ledger = tmp_path / "board.jsonl"
    lease = launch_frontend(
        scenario=SCENARIO,
        ledger=ledger,
        runtime_dir=tmp_path / "runtime",
        host="127.0.0.1",
        port=0,
        timeout_seconds=2,
    )
    try:
        health_status, health = _json(f"{lease.url}/api/health")
        board_status, board = _json(f"{lease.url}/api/board")

        assert health_status == board_status == 200
        assert health["instance_id"] == lease.instance_id
        assert health["ledger"] == str(ledger.resolve())
        assert board == {"decisions": {}}
    finally:
        lease.close()
