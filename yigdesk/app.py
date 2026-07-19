from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import tempfile
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from .a2a import CouncilAuditError, council_audit_status
from .agent import AgentExecutionError, AgentVerificationError, CodexRunner
from .engine import (
    DealInputs,
    compare_proposals,
    evaluate,
    evaluate_proposal,
    find_feasible_boundary,
    stress_test_cogs,
)
from .importer import (
    MAX_UPLOAD_BYTES,
    WorkbookImportError,
    parse_decimal_field,
)
from .session import (
    BoundSession,
    bind_synthetic_session,
    live_revision_id,
    load_active_session,
)
from .workbook import create_workbook, fingerprint, inspect_cell, read_inputs, workbook_snapshot


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = ROOT / "runtime"
AGENT_PHASES = ("starting_codex", "calling_yigdesk_tools", "verifying_result")
MAX_AGENT_PROGRESS_EVENTS = 12


def _load_scenarios() -> dict[str, dict[str, Any]]:
    return json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))


@dataclass
class DemoState:
    """Synthetic fixture state; the public API never mutates an analyzed model."""

    runtime_dir: Path
    scenarios: dict[str, dict[str, Any]] = field(default_factory=_load_scenarios)
    scenario_id: str = "ready"
    lock: threading.RLock = field(default_factory=threading.RLock)
    current_scenario: dict[str, Any] = field(init=False)
    active_source_path: Path = field(init=False)
    active_workbook_path: Path = field(init=False)
    bound_session_id: str | None = field(init=False, default=None)
    observed_session_id: str | None = field(init=False, default=None)
    source: dict[str, Any] = field(init=False)

    @property
    def workbook_path(self) -> Path:
        return self.active_workbook_path

    def load_bound_session(self, bound: BoundSession) -> None:
        self.scenario_id = bound.manifest["scenario_id"]
        self.current_scenario = deepcopy(bound.manifest["scenario"])
        self.active_source_path = bound.source_path
        self.active_workbook_path = bound.projection_path
        self.source = deepcopy(bound.manifest["source"])
        self.bound_session_id = bound.session_id
        self.observed_session_id = bound.session_id

    def reset_fixture(self, scenario_id: str = "ready") -> None:
        if scenario_id not in self.scenarios:
            raise ValueError("unknown scenario")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.active_workbook_path = self.runtime_dir / "yigdesk-demo.xlsx"
        create_workbook(self.active_workbook_path, self.scenarios[scenario_id])
        self.scenario_id = scenario_id
        self.bound_session_id = None
        self.current_scenario = deepcopy(self.scenarios[scenario_id])
        self.active_source_path = self.workbook_path
        self.source = {
            "kind": "generated-synthetic-fixture",
            "filename": "Northstar-renewal.xlsx",
            "size_bytes": self.workbook_path.stat().st_size,
            "sha256": fingerprint(self.workbook_path),
            "parser_version": "synthetic-five-formula-adapter/v1",
            "synthetic_marker_verified": True,
            "analysis_bytes_unchanged": True,
            "sheet_names": ["Deal Inputs", "Deal Model"],
            "extraction": {
                "sheet": "Deal Inputs",
                "revenue_cells": ["Deal Inputs!B2"],
                "cogs_cells": ["Deal Inputs!B4"],
                "revenue_k": self.current_scenario["list_arr_k"],
                "cogs_k": self.current_scenario["cogs_k"],
            },
        }

    def load_upload(
        self,
        *,
        source_bytes: bytes,
        original_filename: str,
        requested_discount_pct,
        margin_floor_pct,
        current_discount_pct,
    ) -> None:
        """Bind one upload as the immutable session shared with Codex Work."""

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        candidate = self.runtime_dir / ("upload-candidate-" + secrets.token_hex(6) + ".xlsx")
        candidate.write_bytes(source_bytes)
        try:
            bind_synthetic_session(
                candidate,
                runtime_root=self.runtime_dir,
                original_filename=original_filename,
                requested_discount_pct=requested_discount_pct,
                margin_floor_pct=margin_floor_pct,
                current_discount_pct=current_discount_pct,
            )
            bound = load_active_session(self.runtime_dir)
            self.load_bound_session(bound)
        finally:
            candidate.unlink(missing_ok=True)


@dataclass(frozen=True)
class AgentSnapshot:
    """Immutable inputs and derived read model for one real-agent revision."""

    revision_id: str
    scenario_id: str
    scenario_json: str
    source_bytes: bytes
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
                "stage": "starting_codex",
                "progress": [{"phase": "starting_codex", "elapsed_ms": 0}],
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

    def record_progress(self, run_id: str, event: Any) -> None:
        """Persist only ordered, bounded phase/timing evidence from the runner."""

        if not isinstance(event, dict):
            return
        phase = event.get("phase")
        elapsed_ms = event.get("elapsed_ms")
        if (
            phase not in AGENT_PHASES
            or isinstance(elapsed_ms, bool)
            or not isinstance(elapsed_ms, int)
            or elapsed_ms < 0
        ):
            return
        with self.lock:
            run = self.runs[run_id]
            progress = run["progress"]
            previous = progress[-1]
            if AGENT_PHASES.index(phase) < AGENT_PHASES.index(previous["phase"]):
                return
            if elapsed_ms < previous["elapsed_ms"]:
                return
            progress.append({"phase": phase, "elapsed_ms": elapsed_ms})
            if len(progress) > MAX_AGENT_PROGRESS_EVENTS:
                run["progress"] = [
                    progress[0],
                    *progress[-(MAX_AGENT_PROGRESS_EVENTS - 1) :],
                ]
            run["status"] = "running"
            run["stage"] = phase
            run["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

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
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + (64 * 1024)
    state = DemoState(
        Path(runtime_dir or os.environ.get("YIGDESK_RUNTIME", DEFAULT_RUNTIME)),
        scenarios=scenarios or _load_scenarios(),
    )
    try:
        state.load_bound_session(load_active_session(state.runtime_dir))
    except FileNotFoundError:
        state.reset_fixture("ready")
    requested_agent = (
        agent_enabled
        if agent_enabled is not None
        else os.environ.get("YIGDESK_NESTED_CODEX_ENABLED", "0") == "1"
    )
    configured = bool(requested_agent and _is_loopback_host(os.environ.get("HOST", "127.0.0.1")))
    runner = agent_runner or (CodexRunner() if configured else None)
    agent = {
        "available": bool(configured and runner is not None),
        "mode": "codex-mcp" if configured and runner is not None else "local-preview",
        "model": getattr(runner, "model", None) if configured else None,
        "reasoning_effort": (
            getattr(runner, "reasoning_effort", None) if configured else None
        ),
    }
    if agent["available"]:
        agent["request_token"] = secrets.token_urlsafe(32)
    agent_runs = AgentRunStore(
        cooldown_seconds=float(os.environ.get("YIGDESK_AGENT_COOLDOWN_SEC", "10"))
    )
    app.config["YIGDESK_STATE"] = state
    app.config["YIGDESK_AGENT"] = agent
    app.config["YIGDESK_AGENT_RUNS"] = agent_runs

    @app.before_request
    def refresh_bound_session():
        try:
            bound = load_active_session(state.runtime_dir)
        except FileNotFoundError:
            return None
        if bound.session_id != state.observed_session_id:
            with state.lock:
                state.load_bound_session(bound)
        return None

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

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        return (
            jsonify(
                {
                    "code": "UPLOAD_TOO_LARGE",
                    "error": f"Synthetic workbook uploads are limited to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                }
            ),
            413,
        )

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "mode": "public-preview"})

    @app.get("/api/state")
    def get_state():
        snapshot = _requested_snapshot(agent_runs, state)
        if snapshot is not None:
            packet = snapshot.packet()
            return jsonify(
                {
                    "scenario_id": snapshot.scenario_id,
                    "scenario": snapshot.scenario(),
                    "source": deepcopy(packet.get("source", {})),
                    "workbook": snapshot.workbook(),
                    "capabilities": ["read_view", "inspect", "preview_consequence"],
                    "decision_capabilities": [
                        "evaluate_proposal",
                        "compare_proposals",
                        "find_feasible_boundary",
                        "stress_test_assumption",
                        "list_missing_evidence",
                    ],
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

    @app.post("/api/upload")
    def upload_workbook():
        uploaded = request.files.get("workbook")
        if uploaded is None or not uploaded.filename:
            return (
                jsonify(
                    {
                        "code": "WORKBOOK_REQUIRED",
                        "error": "Choose a generated synthetic .xlsx workbook.",
                    }
                ),
                400,
            )
        source_bytes = uploaded.stream.read(MAX_UPLOAD_BYTES + 1)
        if len(source_bytes) > MAX_UPLOAD_BYTES:
            return (
                jsonify(
                    {
                        "code": "UPLOAD_TOO_LARGE",
                        "error": f"Synthetic workbook uploads are limited to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    }
                ),
                413,
            )
        try:
            requested_discount_pct = parse_decimal_field(
                "requested_discount_pct",
                request.form.get("requested_discount_pct", "2"),
            )
            margin_floor_pct = parse_decimal_field(
                "margin_floor_pct", request.form.get("margin_floor_pct", "30")
            )
            current_discount_pct = parse_decimal_field(
                "current_discount_pct",
                request.form.get("current_discount_pct", "0"),
            )
            with state.lock:
                state.load_upload(
                    source_bytes=source_bytes,
                    original_filename=uploaded.filename,
                    requested_discount_pct=requested_discount_pct,
                    margin_floor_pct=margin_floor_pct,
                    current_discount_pct=current_discount_pct,
                )
                return jsonify(_state_payload(state, agent)), 201
        except WorkbookImportError as error:
            return jsonify({"code": error.code, "error": str(error)}), 400

    @app.get("/api/inspect")
    def inspect():
        address = str(request.args.get("address", ""))
        try:
            snapshot = _requested_snapshot(agent_runs, state)
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
        snapshot = _requested_snapshot(agent_runs, state)
        if snapshot is not None:
            return jsonify({"packet": snapshot.packet()})
        with state.lock:
            return jsonify({"packet": _consequence_packet(state)})

    @app.get("/api/council-status")
    def get_council_status():
        try:
            bound = load_active_session(state.runtime_dir)
        except FileNotFoundError:
            status = council_audit_status([])
            return jsonify(
                {
                    **status,
                    "mode": "codex-work",
                    "status": "unbound",
                }
            )
        with state.lock:
            expected_revision = _revision_fields(_consequence_packet(state))
        try:
            raw = bound.audit_path.read_text(encoding="utf-8")
            complete_lines = raw.splitlines()
            if raw and not raw.endswith("\n"):
                complete_lines = complete_lines[:-1]
            events = [json.loads(line) for line in complete_lines if line.strip()]
            status = council_audit_status(
                events,
                expected_revision=expected_revision,
            )
            return jsonify({"mode": "codex-work", **status})
        except FileNotFoundError:
            status = council_audit_status([], expected_revision=expected_revision)
            return jsonify({"mode": "codex-work", **status})
        except (CouncilAuditError, json.JSONDecodeError, OSError, TypeError, ValueError):
            return jsonify(
                {
                    "mode": "codex-work",
                    "status": "rejected",
                    "verified": False,
                    "revision": expected_revision,
                    "error": {"code": "COUNCIL_AUDIT_INVALID"},
                }
            )

    @app.post("/api/proposals/evaluate")
    def evaluate_one_proposal():
        try:
            body = _json_object()
            discount = parse_decimal_field(
                "requested_discount_pct", body.get("requested_discount_pct")
            )
            inputs, revision = _decision_inputs(state, agent_runs)
            return jsonify(
                {
                    "proposal": evaluate_proposal(inputs, discount),
                    "revision": revision,
                }
            )
        except WorkbookImportError as error:
            return jsonify({"code": error.code, "error": str(error)}), 400
        except ValueError as error:
            return jsonify({"code": "INVALID_REQUEST", "error": str(error)}), 400

    @app.post("/api/proposals/compare")
    def compare_candidate_proposals():
        try:
            body = _json_object()
            raw_discounts = body.get("discounts_pct")
            if not isinstance(raw_discounts, list) or not 2 <= len(raw_discounts) <= 12:
                raise ValueError("discounts_pct must contain between 2 and 12 proposals")
            discounts = [
                parse_decimal_field("discounts_pct", raw) for raw in raw_discounts
            ]
            if len(set(discounts)) != len(discounts):
                raise ValueError("discounts_pct must not contain duplicates")
            inputs, revision = _decision_inputs(state, agent_runs)
            return jsonify(
                {
                    "comparison": compare_proposals(inputs, discounts),
                    "revision": revision,
                }
            )
        except WorkbookImportError as error:
            return jsonify({"code": error.code, "error": str(error)}), 400
        except ValueError as error:
            return jsonify({"code": "INVALID_REQUEST", "error": str(error)}), 400

    @app.get("/api/proposals/boundary")
    def get_proposal_boundary():
        try:
            step = parse_decimal_field("step_pct", request.args.get("step_pct", "0.01"))
            inputs, revision = _decision_inputs(state, agent_runs)
            return jsonify(
                {
                    "boundary": find_feasible_boundary(inputs, step),
                    "revision": revision,
                }
            )
        except (WorkbookImportError, ValueError) as error:
            code = getattr(error, "code", "INVALID_REQUEST")
            return jsonify({"code": code, "error": str(error)}), 400

    @app.post("/api/proposals/stress-test")
    def stress_test_proposal():
        try:
            body = _json_object()
            discount = parse_decimal_field(
                "requested_discount_pct", body.get("requested_discount_pct")
            )
            raw_change = body.get("cogs_change_pct")
            try:
                cogs_change = Decimal(str(raw_change).strip())
            except (InvalidOperation, AttributeError, ValueError) as error:
                raise ValueError("cogs_change_pct must be a decimal percentage") from error
            inputs, revision = _decision_inputs(state, agent_runs)
            return jsonify(
                {
                    "stress_test": stress_test_cogs(inputs, discount, cogs_change),
                    "revision": revision,
                }
            )
        except WorkbookImportError as error:
            return jsonify({"code": error.code, "error": str(error)}), 400
        except ValueError as error:
            return jsonify({"code": "INVALID_REQUEST", "error": str(error)}), 400

    @app.get("/api/evidence/missing")
    def list_missing_evidence():
        inputs, revision = _decision_inputs(state, agent_runs)
        missing = []
        if inputs.cogs_k is None:
            missing.append(
                {
                    "field": "cogs_k",
                    "evidence_address": "Deal Inputs!B4",
                    "impact": "Gross margin, headroom, boundary, and reviewability cannot be proven.",
                }
            )
        return jsonify(
            {
                "status": "HOLD" if missing else "COMPLETE",
                "missing": missing,
                "revision": revision,
            }
        )

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
        if not _agent_request_authorized(agent):
            return (
                jsonify(
                    {
                        "code": "AGENT_REQUEST_FORBIDDEN",
                        "error": "Codex runs require an authorized same-origin loopback request.",
                    }
                ),
                403,
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
            agent_runs.update(run["run_id"], status="running", stage="starting_codex")
            try:
                agent_result = runner.run(
                    packet,
                    base_url=internal_url,
                    revision_id=snapshot.revision_id,
                    progress_callback=lambda event: agent_runs.record_progress(
                        run["run_id"], event
                    ),
                )
            except (AgentExecutionError, AgentVerificationError) as error:
                message = (
                    "Codex output did not pass Yigdesk verification."
                    if isinstance(error, AgentVerificationError)
                    else "Codex did not produce a verified result."
                )
                failure_phase, elapsed_ms = _agent_failure_evidence(
                    agent_runs.get(run["run_id"]),
                    error,
                    verification_failed=isinstance(error, AgentVerificationError),
                )
                agent_runs.update(
                    run["run_id"],
                    status="failed",
                    stage="rejected",
                    failure_phase=failure_phase,
                    elapsed_ms=elapsed_ms,
                    error={"code": error.code, "message": message},
                )
                return
            except Exception as error:
                failure_phase, elapsed_ms = _agent_failure_evidence(
                    agent_runs.get(run["run_id"]), error
                )
                agent_runs.update(
                    run["run_id"],
                    status="failed",
                    stage="rejected",
                    failure_phase=failure_phase,
                    elapsed_ms=elapsed_ms,
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
    return _consequence_packet_for_paths(
        state.workbook_path,
        state.active_source_path,
        state.scenario_id,
        state.source,
    )


def _consequence_packet_for_paths(
    workbook_path: Path,
    source_path: Path,
    scenario_id: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    before = fingerprint(source_path)
    projection_before = fingerprint(workbook_path)
    consequence = evaluate(read_inputs(workbook_path))
    projection_after = fingerprint(workbook_path)
    after = fingerprint(source_path)
    packet_core = {
        "protocol_version": "demo-consequence-packet/v1",
        "scenario_id": scenario_id,
        "source_fingerprint": before,
        "projection_fingerprint": projection_before,
        "consequence": consequence,
        "implementation_scope": "synthetic-five-formula-adapter",
        "source": {
            key: deepcopy(value)
            for key, value in source.items()
            if key not in {"analysis_bytes_unchanged"}
        },
    }
    packet_id = "cpkt-" + hashlib.sha256(
        json.dumps(packet_core, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return {
        "packet_id": packet_id,
        **packet_core,
        "revision_id": live_revision_id(before, projection_before),
        "analysis_bytes_unchanged": (
            before == after and projection_before == projection_after
        ),
        "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capabilities": ["read_view", "inspect", "preview_consequence"],
        "decision_capabilities": [
            "evaluate_proposal",
            "compare_proposals",
            "find_feasible_boundary",
            "stress_test_assumption",
            "list_missing_evidence",
        ],
    }


def _capture_agent_snapshot(state: DemoState) -> AgentSnapshot:
    revision_id = "rev-" + secrets.token_hex(12)
    source_bytes = state.active_source_path.read_bytes()
    workbook_bytes = state.workbook_path.read_bytes()
    source_fingerprint = hashlib.sha256(source_bytes).hexdigest()
    scenario_id = state.scenario_id
    scenario_json = json.dumps(state.current_scenario, sort_keys=True)
    with tempfile.TemporaryDirectory(prefix="yigdesk-revision-") as directory:
        immutable_source = Path(directory) / "source.xlsx"
        immutable_workbook = Path(directory) / "projection.xlsx"
        immutable_source.write_bytes(source_bytes)
        immutable_workbook.write_bytes(workbook_bytes)
        packet = _consequence_packet_for_paths(
            immutable_workbook,
            immutable_source,
            scenario_id,
            state.source,
        )
        workbook = workbook_snapshot(immutable_workbook)
    if packet["source_fingerprint"] != source_fingerprint:
        raise RuntimeError("captured revision produced inconsistent fingerprints")
    if packet["projection_fingerprint"] != workbook["fingerprint"]:
        raise RuntimeError("captured projection produced inconsistent fingerprints")
    packet["revision_id"] = revision_id
    return AgentSnapshot(
        revision_id=revision_id,
        scenario_id=scenario_id,
        scenario_json=scenario_json,
        source_bytes=source_bytes,
        workbook_bytes=workbook_bytes,
        workbook_json=json.dumps(workbook, sort_keys=True),
        packet_json=json.dumps(packet, sort_keys=True),
    )


def _revision_fields(packet: dict[str, Any]) -> dict[str, str]:
    return {
        "revision_id": packet["revision_id"],
        "source_fingerprint": packet["source_fingerprint"],
        "packet_id": packet["packet_id"],
    }


def _requested_snapshot(
    store: AgentRunStore, state: DemoState | None = None
) -> AgentSnapshot | None:
    revision_id = request.headers.get("X-Yigdesk-Revision")
    if not revision_id:
        return None
    snapshot = store.get_snapshot(revision_id)
    if snapshot is not None:
        return snapshot
    if state is not None:
        with state.lock:
            if _consequence_packet(state)["revision_id"] == revision_id:
                return None
    from flask import abort

    abort(404, description="Unknown, expired, or inactive agent revision.")


def _json_object() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    return body


def _deal_inputs_from_scenario(scenario: dict[str, Any]) -> DealInputs:
    cogs = scenario.get("cogs_k")
    return DealInputs(
        list_arr_k=Decimal(str(scenario["list_arr_k"])),
        current_discount_pct=Decimal(str(scenario["current_discount_pct"])),
        requested_discount_pct=Decimal(str(scenario["requested_discount_pct"])),
        cogs_k=None if cogs is None else Decimal(str(cogs)),
        margin_floor_pct=Decimal(str(scenario["margin_floor_pct"])),
    )


def _decision_inputs(
    state: DemoState, store: AgentRunStore
) -> tuple[DealInputs, dict[str, str]]:
    snapshot = _requested_snapshot(store, state)
    if snapshot is not None:
        packet = snapshot.packet()
        return _deal_inputs_from_scenario(snapshot.scenario()), _revision_fields(packet)
    with state.lock:
        packet = _consequence_packet(state)
        return _deal_inputs_from_scenario(state.current_scenario), _revision_fields(packet)


def _is_loopback_host(host: str) -> bool:
    return host.strip().lower().strip("[]") in {"127.0.0.1", "localhost", "::1"}


def _agent_failure_evidence(
    run: dict[str, Any] | None,
    error: Exception,
    *,
    verification_failed: bool = False,
) -> tuple[str, int]:
    """Return only the last allowlisted phase and monotonic elapsed time."""

    progress = (run or {}).get("progress") or [
        {"phase": "starting_codex", "elapsed_ms": 0}
    ]
    last = progress[-1]
    candidate_phase = getattr(error, "phase", None)
    if verification_failed:
        phase = "verifying_result"
    elif (
        candidate_phase in AGENT_PHASES
        and AGENT_PHASES.index(candidate_phase) >= AGENT_PHASES.index(last["phase"])
    ):
        phase = candidate_phase
    else:
        phase = last["phase"]
    candidate_elapsed = getattr(error, "elapsed_ms", None)
    elapsed_ms = (
        candidate_elapsed
        if isinstance(candidate_elapsed, int)
        and not isinstance(candidate_elapsed, bool)
        and candidate_elapsed >= last["elapsed_ms"]
        else last["elapsed_ms"]
    )
    return phase, elapsed_ms


def _is_loopback_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if parsed.is_loopback:
        return True
    return bool(getattr(parsed, "ipv4_mapped", None) and parsed.ipv4_mapped.is_loopback)


def _agent_request_authorized(agent: dict[str, Any]) -> bool:
    hostname = urlsplit(f"//{request.host}").hostname or ""
    supplied = request.headers.get("X-Yigdesk-Agent-Token", "")
    expected = agent.get("request_token", "")
    return bool(
        _is_loopback_address(request.remote_addr or "")
        and _is_loopback_host(hostname)
        and supplied
        and expected
        and secrets.compare_digest(supplied, expected)
    )


def _state_payload(state: DemoState, agent: dict[str, Any] | None = None) -> dict[str, Any]:
    packet = _consequence_packet(state)
    return {
        "scenario_id": state.scenario_id,
        "scenario": state.current_scenario,
        "source": deepcopy(state.source),
        "workbook": workbook_snapshot(state.workbook_path),
        "capabilities": ["read_view", "inspect", "preview_consequence"],
        "decision_capabilities": [
            "evaluate_proposal",
            "compare_proposals",
            "find_feasible_boundary",
            "stress_test_assumption",
            "list_missing_evidence",
        ],
        "revision": _revision_fields(packet),
        "session": (
            None
            if state.bound_session_id is None
            else {
                "protocol_version": "yigdesk-session/v1",
                "session_id": state.bound_session_id,
                "revision_id": packet["revision_id"],
            }
        ),
        "agent": agent
        or {
            "available": False,
            "mode": "local-preview",
            "model": None,
            "reasoning_effort": None,
        },
    }


def main() -> None:
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "127.0.0.1")
    create_app().run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
