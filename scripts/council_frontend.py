"""Start an identity-bound, unseeded Yigdesk council frontend."""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from urllib.error import URLError
from urllib.request import urlopen

from werkzeug.serving import BaseWSGIServer, make_server

from yigdesk.app import create_app


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class FrontendLease:
    url: str
    instance_id: str
    scenario: Path
    ledger: Path
    _server: BaseWSGIServer
    _thread: Thread

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=2)
        self._server.server_close()


def _is_200(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.5) as response:
            response.read()
            return response.status == 200
    except (OSError, URLError):
        return False


def launch_frontend(
    *,
    scenario: Path,
    ledger: Path,
    runtime_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8787,
    timeout_seconds: float = 10,
) -> FrontendLease:
    """Launch and require both health and board HTTP 200 before returning."""
    scenario = Path(scenario).resolve()
    ledger = Path(ledger).resolve()
    if not (scenario / "model.json").is_file():
        raise FileNotFoundError(f"scenario is not built: {scenario}")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(runtime_dir).resolve()
    instance_id = str(uuid.uuid4())
    app = create_app(
        runtime_dir=runtime_dir,
        scenario_dir=scenario,
        ledger_path=ledger,
        instance_id=instance_id,
    )
    try:
        server = make_server(host, port, app, threaded=True)
    except OSError:
        if port == 0:
            raise
        server = make_server(host, 0, app, threaded=True)
    actual_port = server.server_port
    url = f"http://{host}:{actual_port}"
    thread = Thread(target=server.serve_forever, name=f"yigdesk-{instance_id}", daemon=True)
    thread.start()
    lease = FrontendLease(url, instance_id, scenario, ledger, server, thread)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _is_200(f"{url}/api/health") and _is_200(f"{url}/api/board"):
            return lease
        time.sleep(0.05)
    lease.close()
    raise TimeoutError("frontend deadline missed: /api/health and /api/board were not both HTTP 200")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", type=Path,
        default=ROOT / "data" / "scenarios" / "council_discount",
    )
    parser.add_argument("--ledger", type=Path, default=ROOT / "runtime" / "board.jsonl")
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime" / "frontend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--ttl", type=float, default=900)
    parser.add_argument("--decision-id")
    parser.add_argument("--ready-file", type=Path)
    args = parser.parse_args()
    lease = launch_frontend(
        scenario=args.scenario,
        ledger=args.ledger,
        runtime_dir=args.runtime,
        host=args.host,
        port=args.port,
        timeout_seconds=args.timeout,
    )
    browser_url = (
        f"{lease.url}/?decision_id={args.decision_id}"
        if args.decision_id
        else f"{lease.url}/"
    )
    payload = {
        "status": "ready",
        "url": browser_url,
        "instance_id": lease.instance_id,
        "scenario": str(lease.scenario),
        "ledger": str(lease.ledger),
        "health_http_status": 200,
        "board_http_status": 200,
    }
    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.ready_file.with_name(
            f"{args.ready_file.name}.{lease.instance_id}.tmp"
        )
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temporary, args.ready_file)
    print(f"YIGDESK_FRONTEND_READY {json.dumps(payload, sort_keys=True)}", flush=True)
    try:
        time.sleep(max(args.ttl, 0))
    except KeyboardInterrupt:
        pass
    finally:
        lease.close()


if __name__ == "__main__":
    main()
