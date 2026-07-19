"""Immutable, local session binding for natural-language Codex intake."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .importer import WorkbookImportError, import_synthetic_workbook
from .workbook import create_workbook, fingerprint


SESSION_PROTOCOL = "yigdesk-session/v1"
ACTIVE_SESSION_FILE = "active-session.json"
_SAFE_SESSION_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")


@dataclass(frozen=True)
class BoundSession:
    """Resolved immutable files and metadata for one active local session."""

    session_id: str
    directory: Path
    source_path: Path
    projection_path: Path
    audit_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


def live_revision_id(source_fingerprint: str, projection_fingerprint: str) -> str:
    """Bind revision identity to both evidence bytes and decision inputs."""

    identity = f"{source_fingerprint}:{projection_fingerprint}".encode("ascii")
    return "live-" + hashlib.sha256(identity).hexdigest()[:24]


def bind_synthetic_session(
    source_path: Path | str,
    *,
    runtime_root: Path | str,
    requested_discount_pct: Decimal,
    margin_floor_pct: Decimal,
    current_discount_pct: Decimal = Decimal("0"),
    session_id: str | None = None,
) -> dict[str, Any]:
    """Validate a synthetic workbook and atomically make a copied revision active."""

    source_path = Path(source_path).expanduser().resolve(strict=True)
    runtime_root = Path(runtime_root).expanduser().resolve()
    imported = import_synthetic_workbook(
        source_path,
        original_filename=source_path.name,
        requested_discount_pct=requested_discount_pct,
        margin_floor_pct=margin_floor_pct,
        current_discount_pct=current_discount_pct,
    )
    source_fingerprint = fingerprint(source_path)
    chosen_id = session_id or _new_session_id(source_fingerprint)
    if not _SAFE_SESSION_ID.fullmatch(chosen_id):
        raise WorkbookImportError(
            "SESSION_ID_INVALID",
            "Session id must contain only letters, numbers, dot, underscore, or hyphen.",
        )

    sessions_root = runtime_root / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    session_dir = (sessions_root / chosen_id).resolve()
    _require_child(session_dir, sessions_root.resolve())
    if session_dir.exists():
        raise WorkbookImportError(
            "SESSION_ALREADY_EXISTS",
            "Choose a new session id; immutable sessions cannot be overwritten.",
        )

    staging = sessions_root / f".{chosen_id}-{secrets.token_hex(4)}.tmp"
    staging.mkdir()
    try:
        copied_source = staging / "source.xlsx"
        projection = staging / "projection.xlsx"
        shutil.copyfile(source_path, copied_source)
        if fingerprint(copied_source) != source_fingerprint:
            raise WorkbookImportError(
                "SOURCE_COPY_DRIFT", "The session copy did not match the validated source."
            )
        create_workbook(projection, imported.scenario)
        projection_fingerprint = fingerprint(projection)
        revision_id = live_revision_id(source_fingerprint, projection_fingerprint)
        source_cell_count = len(imported.source["extraction"]["revenue_cells"]) + len(
            imported.source["extraction"]["cogs_cells"]
        )
        manifest = {
            "protocol_version": SESSION_PROTOCOL,
            "session_id": chosen_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "revision_id": revision_id,
            "scenario_id": "bound-" + chosen_id,
            "scenario": imported.scenario,
            "source": {
                **imported.source,
                "filename": source_path.name,
                "sha256": source_fingerprint,
                "session_file": "source.xlsx",
                "source_cell_count": source_cell_count,
            },
            "projection": {
                "session_file": "projection.xlsx",
                "sha256": projection_fingerprint,
            },
            "council": {"audit_file": "codex-a2a-audit.jsonl"},
        }
        _write_json(staging / "manifest.json", manifest)
        staging.replace(session_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    pointer = {
        "protocol_version": SESSION_PROTOCOL,
        "session_id": chosen_id,
        "manifest": f"sessions/{chosen_id}/manifest.json",
    }
    runtime_root.mkdir(parents=True, exist_ok=True)
    pointer_tmp = runtime_root / f".{ACTIVE_SESSION_FILE}-{secrets.token_hex(4)}.tmp"
    _write_json(pointer_tmp, pointer)
    pointer_tmp.replace(runtime_root / ACTIVE_SESSION_FILE)

    return {
        "status": "BOUND",
        "session_id": chosen_id,
        "revision_id": revision_id,
        "source_fingerprint": source_fingerprint,
        "projection_fingerprint": projection_fingerprint,
        "source": {
            "filename": source_path.name,
            "source_cell_count": source_cell_count,
            "analysis_bytes_unchanged": source_path.read_bytes()
            == (session_dir / "source.xlsx").read_bytes(),
        },
        "decision": {
            "current_discount_pct": str(current_discount_pct),
            "requested_discount_pct": str(requested_discount_pct),
            "margin_floor_pct": str(margin_floor_pct),
        },
        "next": {
            "state": "python -m yigdesk.cli state",
            "council_audit": str(session_dir / "codex-a2a-audit.jsonl"),
            "evidence_url": "http://127.0.0.1:8787",
        },
    }


def load_active_session(runtime_root: Path | str) -> BoundSession:
    """Resolve and verify the active immutable session without trusting paths in JSON."""

    runtime_root = Path(runtime_root).expanduser().resolve()
    pointer_path = runtime_root / ACTIVE_SESSION_FILE
    if not pointer_path.is_file():
        raise FileNotFoundError(pointer_path)
    pointer = _read_json(pointer_path)
    if pointer.get("protocol_version") != SESSION_PROTOCOL:
        raise ValueError("Unsupported active Yigdesk session protocol.")
    session_id = pointer.get("session_id")
    if not isinstance(session_id, str) or not _SAFE_SESSION_ID.fullmatch(session_id):
        raise ValueError("Active Yigdesk session id is invalid.")

    session_dir = (runtime_root / "sessions" / session_id).resolve()
    _require_child(session_dir, (runtime_root / "sessions").resolve())
    manifest_path = session_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    if (
        manifest.get("protocol_version") != SESSION_PROTOCOL
        or manifest.get("session_id") != session_id
    ):
        raise ValueError("Active Yigdesk session manifest does not match its pointer.")

    source_path = session_dir / "source.xlsx"
    projection_path = session_dir / "projection.xlsx"
    if fingerprint(source_path) != manifest["source"]["sha256"]:
        raise ValueError("Active Yigdesk source fingerprint does not match its manifest.")
    if fingerprint(projection_path) != manifest["projection"]["sha256"]:
        raise ValueError("Active Yigdesk projection fingerprint does not match its manifest.")
    expected_revision = live_revision_id(
        manifest["source"]["sha256"], manifest["projection"]["sha256"]
    )
    if manifest.get("revision_id") != expected_revision:
        raise ValueError("Active Yigdesk revision identity is invalid.")
    return BoundSession(
        session_id=session_id,
        directory=session_dir,
        source_path=source_path,
        projection_path=projection_path,
        audit_path=session_dir / "codex-a2a-audit.jsonl",
        manifest_path=manifest_path,
        manifest=manifest,
    )


def resolve_council_audit_path(runtime_root: Path | str) -> Path:
    """Use the active session audit, with a legacy ignored-runtime fallback."""

    runtime_root = Path(runtime_root).expanduser().resolve()
    try:
        return load_active_session(runtime_root).audit_path
    except FileNotFoundError:
        return runtime_root / "codex-a2a-audit.jsonl"


def _new_session_id(source_fingerprint: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{source_fingerprint[:10]}-{secrets.token_hex(3)}"


def _require_child(candidate: Path, parent: Path) -> None:
    if candidate == parent or parent not in candidate.parents:
        raise ValueError("Session path escaped the configured runtime directory.")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object at {path.name}.")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
