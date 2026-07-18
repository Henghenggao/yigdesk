from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from .engine import evaluate
from .workbook import create_workbook, fingerprint, inspect_cell, read_inputs, workbook_snapshot


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = ROOT / "runtime"


def _load_scenarios() -> dict[str, dict[str, Any]]:
    return json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))


@dataclass
class DemoState:
    """Synthetic fixture state; the public API never mutates an analyzed model."""

    runtime_dir: Path
    scenarios: dict[str, dict[str, Any]] = field(default_factory=_load_scenarios)
    scenario_id: str = "ready"
    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def workbook_path(self) -> Path:
        return self.runtime_dir / "yigdesk-demo.xlsx"

    def reset_fixture(self, scenario_id: str = "ready") -> None:
        if scenario_id not in self.scenarios:
            raise ValueError("unknown scenario")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        create_workbook(self.workbook_path, self.scenarios[scenario_id])
        self.scenario_id = scenario_id


def create_app(*, runtime_dir: Path | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="")
    state = DemoState(Path(runtime_dir or os.environ.get("YIGDESK_RUNTIME", DEFAULT_RUNTIME)))
    state.reset_fixture("ready")
    app.config["YIGDESK_STATE"] = state

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "mode": "public-preview"})

    @app.get("/api/state")
    def get_state():
        with state.lock:
            return jsonify(_state_payload(state))

    @app.post("/api/reset")
    def reset():
        body = request.get_json(silent=True) or {}
        try:
            with state.lock:
                state.reset_fixture(body.get("scenario_id", "ready"))
                return jsonify(_state_payload(state))
        except ValueError:
            return jsonify({"code": "UNKNOWN_SCENARIO", "error": "Choose ready or hold."}), 400

    @app.get("/api/inspect")
    def inspect():
        address = str(request.args.get("address", ""))
        try:
            with state.lock:
                return jsonify(inspect_cell(state.workbook_path, address))
        except ValueError as error:
            return jsonify({"code": "UNKNOWN_OBJECT", "error": str(error)}), 404

    @app.post("/api/analyze")
    def analyze():
        with state.lock:
            before = fingerprint(state.workbook_path)
            consequence = evaluate(read_inputs(state.workbook_path))
            after = fingerprint(state.workbook_path)
            packet_core = {
                "protocol_version": "demo-consequence-packet/v1",
                "scenario_id": state.scenario_id,
                "source_fingerprint": before,
                "consequence": consequence,
                "implementation_scope": "synthetic-five-formula-adapter",
            }
            packet_id = "cpkt-" + hashlib.sha256(
                json.dumps(packet_core, sort_keys=True).encode("utf-8")
            ).hexdigest()[:12]
            packet = {
                "packet_id": packet_id,
                **packet_core,
                "analysis_bytes_unchanged": before == after,
                "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "capabilities": ["read_view", "inspect", "preview_consequence"],
            }
            return jsonify({"packet": packet})

    return app


def _state_payload(state: DemoState) -> dict[str, Any]:
    return {
        "scenario_id": state.scenario_id,
        "scenario": state.scenarios[state.scenario_id],
        "workbook": workbook_snapshot(state.workbook_path),
        "capabilities": ["read_view", "inspect", "preview_consequence"],
    }


def main() -> None:
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "127.0.0.1")
    create_app().run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
