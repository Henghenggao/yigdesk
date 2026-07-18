from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from .agent import AgentExecutionError, AgentVerificationError, CodexRunner
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


@dataclass(frozen=True)
class AgentSnapshot:
    """Immutable inputs and derived read model for one real-agent revision."""

    revision_id: str
    scenario_id: str
    scenario_json: str
    workbook_bytes: bytes
    workbook_json: str
    packet_json: str

    def scenario(self) -> dict[str, Any]:
        return json.loads(self.scenario_json)

    def workbook(self) -> dict[str, Any]:
        return json.loads(self.workbook_json)

    def packet(self) -> dict[str, Any]:
        return json.loads(self.packet_json)


class AgentRunStore:
    """Ephemeral demo trace; deliberately not a production audit service."""

    def __init__(self, *, cooldown_seconds: float = 10, max_records: int = 20) -> None:
        self.lock = threading.RLock()
        self.runs: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, AgentSnapshot] = {}
        self.active_id: str | None = None
        self.cooldown_seconds = cooldown_seconds
        self.max_records = max_records
        self.last_created = 0.0

    def create(self, snapshot: AgentSnapshot) -> dict[str, Any]:
        with self.lock:
            if self.active_id is not None:
                raise RuntimeError("agent busy")
            if time.monotonic() - self.last_created < self.cooldown_seconds:
                raise RuntimeError("agent rate limited")
            while len(self.runs) >= self.max_records:
                expired = self.runs.pop(next(iter(self.runs)))
                self.snapshots.pop(expired["revision_id"], None)
            packet = snapshot.packet()
            run_id = "arun-" + secrets.token_hex(6)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            run = {
                "run_id": run_id,
                "status": "queued",
                "stage": "reading_request",
                "created_at": now,
                "updated_at": now,
                "packet_id": packet["packet_id"],
                "source_fingerprint": packet["source_fingerprint"],
                "revision_id": snapshot.revision_id,
            }
            self.runs[run_id] = run
            self.snapshots[snapshot.revision_id] = snapshot
            self.active_id = run_id
            self.last_created = time.monotonic()
            return deepcopy(run)

    def update(self, run_id: str, **changes: Any) -> None:
        with self.lock:
            run = self.runs[run_id]
            run.update(changes)
            run["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if run.get("status") in {"completed", "failed"} and self.active_id == run_id:
                self.active_id = None

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            run = self.runs.get(run_id)
            return None if run is None else deepcopy(run)

    def get_snapshot(self, revision_id: str) -> AgentSnapshot | None:
        with self.lock:
            return self.snapshots.get(revision_id)

    def register_snapshot(self, snapshot: AgentSnapshot) -> None:
        """Register an immutable revision for an in-process trusted harness."""

        with self.lock:
            self.snapshots[snapshot.revision_id] = snapshot


def create_app(
    *,
    runtime_dir: Path | None = None,
    scenarios: dict[str, dict[str, Any]] | None = None,
    agent_enabled: bool | None = None,
    agent_runner: Any | None = None,
) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="")
    state = DemoState(
        Path(runtime_dir or os.environ.get("YIGDESK_RUNTIME", DEFAULT_RUNTIME)),
        scenarios=scenarios or _load_scenarios(),
    )
    state.reset_fixture("ready")
    requested_agent = (
        agent_enabled
        if agent_enabled is not None
        else os.environ.get("YIGDESK_CODEX_ENABLED", "0") == "1"
    )
    configured = bool(requested_agent and _is_loopback_host(os.environ.get("HOST", "127.0.0.1")))
    runner = agent_runner or (CodexRunner() if configured else None)
    agent = {
        "available": bool(configured and runner is not None),
        "mode": "codex-mcp" if configured and runner is not None else "local-preview",
        "model": getattr(runner, "model", None) if configured else None,
    }
    agent_runs = AgentRunStore(
        cooldown_seconds=float(os.environ.get("YIGDESK_AGENT_COOLDOWN_SEC", "10"))
    )
    app.config["YIGDESK_STATE"] = state
    app.config["YIGDESK_AGENT"] = agent
    app.config["YIGDESK_AGENT_RUNS"] = agent_runs

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
        snapshot = _requested_snapshot(agent_runs)
        if snapshot is not None:
            packet = snapshot.packet()
            return jsonify(
                {
                    "scenario_id": snapshot.scenario_id,
                    "scenario": snapshot.scenario(),
                    "workbook": snapshot.workbook(),
                    "capabilities": ["read_view", "inspect", "preview_consequence"],
                    "revision": _revision_fields(packet),
                    "agent": agent,
                }
            )
        with state.lock:
            return jsonify(_state_payload(state, agent))

    @app.post("/api/reset")
    def reset():
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return jsonify({"code": "INVALID_REQUEST", "error": "JSON body must be an object."}), 400
        try:
            with state.lock:
                state.reset_fixture(body.get("scenario_id", "ready"))
                return jsonify(_state_payload(state, agent))
        except ValueError:
            return jsonify({"code": "UNKNOWN_SCENARIO", "error": "Choose ready or hold."}), 400

    @app.get("/api/inspect")
    def inspect():
        address = str(request.args.get("address", ""))
        try:
            snapshot = _requested_snapshot(agent_runs)
            if snapshot is not None:
                packet = snapshot.packet()
                cell = next(
                    (item for item in snapshot.workbook()["cells"] if item["address"] == address),
                    None,
                )
                if cell is None:
                    raise ValueError("Object is not present in the synthetic view.")
                return jsonify(
                    {
                        **cell,
                        "value_verified": True,
                        "source": "immutable synthetic XLSX revision",
                        **_revision_fields(packet),
                    }
                )
            with state.lock:
                packet = _consequence_packet(state)
                return jsonify(
                    {
                        **inspect_cell(state.workbook_path, address),
                        **_revision_fields(packet),
                    }
                )
        except ValueError as error:
            return jsonify({"code": "UNKNOWN_OBJECT", "error": str(error)}), 404

    @app.post("/api/analyze")
    def analyze():
        snapshot = _requested_snapshot(agent_runs)
        if snapshot is not None:
            return jsonify({"packet": snapshot.packet()})
        with state.lock:
            return jsonify({"packet": _consequence_packet(state)})

    @app.post("/api/agent-runs")
    def create_agent_run():
        if not agent["available"] or runner is None:
            return (
                jsonify(
                    {
                        "code": "CODEX_UNAVAILABLE",
                        "error": "Real Codex mode is not configured. Use the explicitly labeled local preview.",
                    }
                ),
                503,
            )
        with state.lock:
            snapshot = _capture_agent_snapshot(state)
            packet = snapshot.packet()
        try:
            run = agent_runs.create(snapshot)
        except RuntimeError as error:
            if str(error) == "agent rate limited":
                return (
                    jsonify(
                        {
                            "code": "AGENT_RATE_LIMITED",
                            "error": "Wait before starting another Codex run.",
                        }
                    ),
                    429,
                )
            return jsonify({"code": "AGENT_BUSY", "error": "One Codex run is already active."}), 409
        internal_url = os.environ.get(
            "YIGDESK_INTERNAL_URL",
            f"http://127.0.0.1:{os.environ.get('PORT', '8787')}",
        )

        def execute_agent_run() -> None:
            agent_runs.update(run["run_id"], status="running", stage="calling_yigdesk_tools")
            try:
                agent_result = runner.run(
                    packet,
                    base_url=internal_url,
                    revision_id=snapshot.revision_id,
                )
            except (AgentExecutionError, AgentVerificationError) as error:
                message = (
                    "Codex output did not pass Yigdesk verification."
                    if isinstance(error, AgentVerificationError)
                    else "Codex did not produce a verified result."
                )
                agent_runs.update(
                    run["run_id"],
                    status="failed",
                    stage="rejected",
                    error={"code": type(error).__name__, "message": message},
                )
                return
            except Exception:
                agent_runs.update(
                    run["run_id"],
                    status="failed",
                    stage="rejected",
                    error={"code": "AGENT_FAILURE", "message": "Codex run failed safely."},
                )
                return
            agent_runs.update(
                run["run_id"],
                status="completed",
                stage="review_ready",
                packet=packet,
                agent=agent_result,
            )

        threading.Thread(target=execute_agent_run, daemon=True).start()
        return jsonify(run), 202

    @app.get("/api/agent-runs/<run_id>")
    def get_agent_run(run_id: str):
        run = agent_runs.get(run_id)
        if run is None:
            return jsonify({"code": "UNKNOWN_AGENT_RUN", "error": "Agent run was not found."}), 404
        return jsonify(run)

    return app


def _consequence_packet(state: DemoState) -> dict[str, Any]:
    return _consequence_packet_for_path(state.workbook_path, state.scenario_id)


def _consequence_packet_for_path(workbook_path: Path, scenario_id: str) -> dict[str, Any]:
    before = fingerprint(workbook_path)
    consequence = evaluate(read_inputs(workbook_path))
    after = fingerprint(workbook_path)
    packet_core = {
        "protocol_version": "demo-consequence-packet/v1",
        "scenario_id": scenario_id,
        "source_fingerprint": before,
        "consequence": consequence,
        "implementation_scope": "synthetic-five-formula-adapter",
    }
    packet_id = "cpkt-" + hashlib.sha256(
        json.dumps(packet_core, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return {
        "packet_id": packet_id,
        **packet_core,
        "revision_id": "live-" + before[:24],
        "analysis_bytes_unchanged": before == after,
        "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capabilities": ["read_view", "inspect", "preview_consequence"],
    }


def _capture_agent_snapshot(state: DemoState) -> AgentSnapshot:
    revision_id = "rev-" + secrets.token_hex(12)
    source_bytes = state.workbook_path.read_bytes()
    source_fingerprint = hashlib.sha256(source_bytes).hexdigest()
    scenario_id = state.scenario_id
    scenario_json = json.dumps(state.scenarios[scenario_id], sort_keys=True)
    with tempfile.TemporaryDirectory(prefix="yigdesk-revision-") as directory:
        immutable_path = Path(directory) / "captured.xlsx"
        immutable_path.write_bytes(source_bytes)
        packet = _consequence_packet_for_path(immutable_path, scenario_id)
        workbook = workbook_snapshot(immutable_path)
    if {
        packet["source_fingerprint"],
        workbook["fingerprint"],
    } != {source_fingerprint}:
        raise RuntimeError("captured revision produced inconsistent fingerprints")
    packet["revision_id"] = revision_id
    return AgentSnapshot(
        revision_id=revision_id,
        scenario_id=scenario_id,
        scenario_json=scenario_json,
        workbook_bytes=source_bytes,
        workbook_json=json.dumps(workbook, sort_keys=True),
        packet_json=json.dumps(packet, sort_keys=True),
    )


def _revision_fields(packet: dict[str, Any]) -> dict[str, str]:
    return {
        "revision_id": packet["revision_id"],
        "source_fingerprint": packet["source_fingerprint"],
        "packet_id": packet["packet_id"],
    }


def _requested_snapshot(store: AgentRunStore) -> AgentSnapshot | None:
    revision_id = request.headers.get("X-Yigdesk-Revision")
    if not revision_id:
        return None
    snapshot = store.get_snapshot(revision_id)
    if snapshot is None:
        from flask import abort

        abort(404, description="Unknown or expired agent revision.")
    return snapshot


def _is_loopback_host(host: str) -> bool:
    return host.strip().lower().strip("[]") in {"127.0.0.1", "localhost", "::1"}


def _state_payload(state: DemoState, agent: dict[str, Any] | None = None) -> dict[str, Any]:
    packet = _consequence_packet(state)
    return {
        "scenario_id": state.scenario_id,
        "scenario": state.scenarios[state.scenario_id],
        "workbook": workbook_snapshot(state.workbook_path),
        "capabilities": ["read_view", "inspect", "preview_consequence"],
        "revision": _revision_fields(packet),
        "agent": agent or {"available": False, "mode": "local-preview", "model": None},
    }


def main() -> None:
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "127.0.0.1")
    create_app().run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
