from __future__ import annotations

import json
import os
import secrets
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from .importer import (
    MAX_UPLOAD_BYTES,
    WorkbookImportError,
    parse_decimal_field,
)
from .session import (
    BoundSession,
    bind_synthetic_session,
    load_active_session,
)
from .workbook import create_workbook, fingerprint


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = ROOT / "runtime"


def _load_scenarios() -> dict[str, dict[str, Any]]:
    return json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))


@dataclass
class DemoState:
    """Synthetic fixture / bound-session state for the engine-free upload shell.

    The decision surface now lives in the deterministic blackboard; this shell
    only owns upload/reset/session binding and never evaluates a model.
    """

    runtime_dir: Path
    scenarios: dict[str, dict[str, Any]] = field(default_factory=_load_scenarios)
    scenario_id: str = "ready"
    lock: threading.RLock = field(default_factory=threading.RLock)
    current_scenario: dict[str, Any] = field(init=False)
    active_source_path: Path = field(init=False)
    active_workbook_path: Path | None = field(init=False, default=None)
    bound_session_id: str | None = field(init=False, default=None)
    bound_revision_id: str | None = field(init=False, default=None)
    observed_session_id: str | None = field(init=False, default=None)
    source: dict[str, Any] = field(init=False)

    @property
    def workbook_path(self) -> Path:
        return self.active_workbook_path

    def load_bound_session(self, bound: BoundSession) -> None:
        self.scenario_id = bound.manifest["scenario_id"]
        self.current_scenario = deepcopy(bound.manifest["scenario"])
        self.active_source_path = bound.source_path
        self.source = deepcopy(bound.manifest["source"])
        self.bound_session_id = bound.session_id
        self.bound_revision_id = bound.manifest["revision_id"]
        self.observed_session_id = bound.session_id

    def reset_fixture(self, scenario_id: str = "ready") -> None:
        if scenario_id not in self.scenarios:
            raise ValueError("unknown scenario")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.active_workbook_path = self.runtime_dir / "yigdesk-demo.xlsx"
        create_workbook(self.active_workbook_path, self.scenarios[scenario_id])
        self.scenario_id = scenario_id
        self.bound_session_id = None
        self.bound_revision_id = None
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
        """Bind one upload as the immutable active session."""

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


def create_app(
    *,
    runtime_dir: Path | None = None,
    scenarios: dict[str, dict[str, Any]] | None = None,
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
    app.config["YIGDESK_STATE"] = state

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
        with state.lock:
            return jsonify(_state_payload(state))

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
                return jsonify(_state_payload(state))
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
                return jsonify(_state_payload(state)), 201
        except WorkbookImportError as error:
            return jsonify({"code": error.code, "error": str(error)}), 400

    return app


def _state_payload(state: DemoState) -> dict[str, Any]:
    """Engine-free session/scenario view: no consequence packet, no workbook
    projection, no revision math — just the bound session's identity."""

    return {
        "scenario_id": state.scenario_id,
        "scenario": state.current_scenario,
        "source": deepcopy(state.source),
        "session": (
            None
            if state.bound_session_id is None
            else {
                "protocol_version": "yigdesk-session/v1",
                "session_id": state.bound_session_id,
                "revision_id": state.bound_revision_id,
            }
        ),
    }


def main() -> None:
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "127.0.0.1")
    create_app().run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
