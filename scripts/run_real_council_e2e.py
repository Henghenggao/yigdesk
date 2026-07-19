"""Run the real project Codex council against the active browser-bound session."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator

from yigdesk.a2a import CouncilAuditError, verify_council_audit
from yigdesk.agent import _resolve_codex_command
from yigdesk.session import load_active_session


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_SCHEMA = ROOT / "yigdesk" / "schemas" / "council-e2e-result.schema.json"
CANDIDATE_VALIDATOR = Draft202012Validator(
    json.loads(CANDIDATE_SCHEMA.read_text(encoding="utf-8"))
)
COUNCIL_MODEL = "gpt-5.6-terra"


def build_codex_command(
    command_prefix: list[str],
    *,
    root: Path,
    runtime: Path,
    base_url: str,
    output_path: Path,
    preflight: dict[str, str],
) -> list[str]:
    """Build the outer Codex command used only by the real council E2E harness."""

    preflight_json = json.dumps(preflight, sort_keys=True)
    prompt = f"""Use $yigdesk-council on the current bound Yigdesk revision.
The read-only E2E harness already verified health, state, and analysis. Its exact
preflight is {preflight_json}. Do not run shell commands or repeat CLI preflight.
This is the real council performance E2E: use the project finance_analyst,
sales_advocate, risk_challenger, and decision_optimizer custom agents. For each
spawn set fork_turns="none" and send only the bounded revision identity, decision
inputs, exact role sequence, and required output fields. Each specialist's first
action must be its first Yigdesk MCP call. Keep the three specialists parallel and
start the optimizer only after their revision gate passes. Use standard tier; Fast
mode is disabled. Keep orchestration in this outer Codex task.
Pass the optimizer exactly the unique submitted, sales alternative, largest_safe_step_pct, and first_unsafe_pct values. Do not omit first_unsafe_pct.
Do not start a nested codex exec. The harness verifies the audit after Codex exits;
do not run the audit verifier inside Codex. Do not withhold the candidate decision
solely because that final audit verification is external. After the optimizer returns,
immediately emit only one compact JSON object and stop. Use exactly these fields:
{{"candidate_status":"READY_FOR_EXTERNAL_AUDIT","revision_id":"...",
"source_fingerprint":"...","packet_id":"...","submitted_discount_pct":"...",
"financially_feasible":true,"exact_max_discount_pct":"...",
"largest_safe_step_pct":"...","first_unsafe_pct":"...",
"recommended_discount_pct":null,
"commercial_optimality_proven":false,"decision":"short grounded conclusion"}}.
The harness validates this object against its local JSON Schema after exit."""
    return [
        *command_prefix,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-rules",
        "--strict-config",
        "--model",
        COUNCIL_MODEL,
        "--disable",
        "fast_mode",
        "--sandbox",
        "read-only",
        "-C",
        str(root.resolve()),
        "-c",
        'model_reasoning_effort="none"',
        "-c",
        'model_reasoning_summary="none"',
        "-c",
        'model_verbosity="low"',
        "--output-last-message",
        str(output_path.resolve()),
        prompt,
    ]


def read_candidate_result(
    output_path: Path,
    *,
    expected_revision: dict[str, str],
    expected_values: dict[str, object] | None = None,
) -> dict[str, object]:
    """Read the schema-bound candidate and enforce the immutable revision identity."""

    candidate = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(candidate, dict):
        raise ValueError("Council candidate must be a JSON object.")
    errors = sorted(
        CANDIDATE_VALIDATOR.iter_errors(candidate),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        raise ValueError(f"Council candidate schema validation failed: {errors[0].message}")
    if candidate.get("candidate_status") != "READY_FOR_EXTERNAL_AUDIT":
        raise ValueError("Council candidate is not ready for external audit.")
    for field in ("revision_id", "source_fingerprint", "packet_id"):
        if candidate.get(field) != expected_revision.get(field):
            raise ValueError(f"Council candidate identity mismatch for {field}.")
    for field, expected_value in (expected_values or {}).items():
        if candidate.get(field) != expected_value:
            raise ValueError(f"Council candidate grounded value mismatch for {field}.")
    if candidate.get("commercial_optimality_proven") is not False:
        raise ValueError("Council candidate overstates commercial optimality.")
    return candidate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path("runtime"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument(
        "--timeout-seconds",
        type=_bounded_timeout,
        default=120,
        help="Hard end-to-end budget; values above 120 are rejected.",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--codex", help="Optional Codex executable override.")
    parser.add_argument(
        "--acknowledge-data-sharing",
        action="store_true",
        required=True,
        help="Acknowledge that the active generated synthetic case is sent to Codex.",
    )
    return parser.parse_args(argv)


def _bounded_timeout(value: str) -> int:
    timeout = int(value)
    if not 0 < timeout <= 120:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 120 seconds")
    return timeout


def run_council_process(
    command: list[str],
    *,
    audit_path: Path,
    candidate_output_path: Path | None = None,
    timeout_seconds: float = 120,
    diagnostic_path: Path | None = None,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    """Run one real outer council and measure its first audited MCP activity."""

    if timeout_seconds <= 0 or timeout_seconds > 120:
        raise ValueError("The real council E2E budget must be between 0 and 120 seconds.")
    if audit_path.exists() and audit_path.stat().st_size:
        raise ValueError("The real council E2E requires a fresh session audit.")

    popen_options: dict[str, object] = {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        **popen_options,
    )
    output_counts = {"stdout": 0, "stderr": 0}
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    candidate_event: dict[str, object] = {"text": None, "ready_ms": None}

    def drain(stream, name: str) -> None:
        if stream is None:
            return
        try:
            for _line in stream:
                output_counts[name] += 1
                if name == "stdout":
                    stdout_lines.append(_line)
                    candidate_text = _candidate_from_codex_event(_line)
                    if candidate_text is not None:
                        candidate_event["text"] = candidate_text
                        candidate_event["ready_ms"] = round(
                            (time.perf_counter() - started) * 1000
                        )
                else:
                    stderr_lines.append(_line)
        finally:
            stream.close()

    stdout_thread = threading.Thread(
        target=drain, args=(process.stdout, "stdout"), daemon=True
    )
    stderr_thread = threading.Thread(
        target=drain, args=(process.stderr, "stderr"), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    deadline = started + timeout_seconds
    observed_events = 0
    first_mcp_call_ms: int | None = None
    role_first_call_ms: dict[str, int] = {}
    timed_out = False
    completed_on_candidate = False
    while process.poll() is None:
        observed_events, first_mcp_call_ms = _observe_audit_progress(
            audit_path,
            started=started,
            observed_events=observed_events,
            first_mcp_call_ms=first_mcp_call_ms,
            role_first_call_ms=role_first_call_ms,
        )
        decision_ready_ms = candidate_event["ready_ms"]
        if (
            isinstance(decision_ready_ms, int)
            and decision_ready_ms <= round(timeout_seconds * 1000)
            and observed_events >= 15
        ):
            completed_on_candidate = True
            _terminate_process_tree(process)
            break
        if time.perf_counter() >= deadline:
            timed_out = True
            _terminate_process_tree(process)
            break
        time.sleep(0.01)

    process.wait()
    stdout_thread.join()
    stderr_thread.join()
    candidate_text = candidate_event["text"]
    if candidate_output_path is not None and isinstance(candidate_text, str):
        candidate_output_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_output_path.write_text(candidate_text + "\n", encoding="utf-8")
    if diagnostic_path is not None and stderr_lines:
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_path.write_text("".join(stderr_lines), encoding="utf-8")
    if diagnostic_path is not None and stdout_lines:
        event_name = (
            diagnostic_path.name[: -len(".stderr.log")] + ".events.jsonl"
            if diagnostic_path.name.endswith(".stderr.log")
            else diagnostic_path.stem + ".events.jsonl"
        )
        (diagnostic_path.parent / event_name).write_text(
            "".join(stdout_lines), encoding="utf-8"
        )
    observed_events, first_mcp_call_ms = _observe_audit_progress(
        audit_path,
        started=started,
        observed_events=observed_events,
        first_mcp_call_ms=first_mcp_call_ms,
        role_first_call_ms=role_first_call_ms,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    budget_ms = round(timeout_seconds * 1000)
    report: dict[str, object] = {
        "status": "failed",
        "verified": False,
        "budget_ms": budget_ms,
        "elapsed_ms": elapsed_ms,
        "first_mcp_call_ms": first_mcp_call_ms,
        "role_first_call_ms": role_first_call_ms,
        "observed_call_count": observed_events,
        "codex_event_count": output_counts["stdout"],
    }
    if isinstance(candidate_event["ready_ms"], int):
        report["decision_ready_ms"] = candidate_event["ready_ms"]
        report["completion"] = (
            "candidate_event" if completed_on_candidate else "process_exit"
        )
    if timed_out:
        report["error"] = {"code": "COUNCIL_TIMEOUT"}
        return report
    if process.returncode != 0 and not completed_on_candidate:
        report["error"] = {"code": "COUNCIL_PROCESS_FAILED"}
        return report
    try:
        events = _read_complete_audit_events(audit_path)
        verified = verify_council_audit(events)
    except (CouncilAuditError, FileNotFoundError, json.JSONDecodeError, TypeError):
        report["error"] = {"code": "COUNCIL_AUDIT_INVALID"}
        return report
    report.update(
        {
            "status": "passed",
            "verified": True,
            "accepted_call_count": verified["accepted_call_count"],
            "observed_call_count": verified["observed_call_count"],
            "revision": verified["revision"],
        }
    )
    return report


def _candidate_from_codex_event(line: str) -> str | None:
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "item.completed":
        return None
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "agent_message":
        return None
    text_value = item.get("text")
    if not isinstance(text_value, str):
        return None
    try:
        candidate = json.loads(text_value)
    except json.JSONDecodeError:
        return None
    if not isinstance(candidate, dict):
        return None
    if candidate.get("candidate_status") != "READY_FOR_EXTERNAL_AUDIT":
        return None
    if not CANDIDATE_VALIDATOR.is_valid(candidate):
        return None
    return text_value


def _observe_audit_progress(
    audit_path: Path,
    *,
    started: float,
    observed_events: int,
    first_mcp_call_ms: int | None,
    role_first_call_ms: dict[str, int],
) -> tuple[int, int | None]:
    try:
        events = _read_complete_audit_events(audit_path)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return observed_events, first_mcp_call_ms
    now_ms = round((time.perf_counter() - started) * 1000)
    for event in events[observed_events:]:
        actor = event.get("actor")
        if first_mcp_call_ms is None:
            first_mcp_call_ms = now_ms
        if isinstance(actor, str):
            role_first_call_ms.setdefault(actor, now_ms)
    return len(events), first_mcp_call_ms


def _read_complete_audit_events(audit_path: Path) -> list[dict[str, object]]:
    raw = audit_path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if raw and not raw.endswith("\n"):
        lines = lines[:-1]
    return [json.loads(line) for line in lines if line.strip()]


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runtime = args.runtime.resolve()
    bound = load_active_session(runtime)
    if bound.audit_path.exists() and bound.audit_path.stat().st_size:
        raise SystemExit(
            "The active session already has council calls; upload or bind a fresh revision."
        )
    try:
        with urlopen(f"{args.base_url.rstrip('/')}/api/health", timeout=5) as response:
            if response.status != 200:
                raise SystemExit("Yigdesk health check did not return HTTP 200.")
    except URLError as error:
        raise SystemExit("Yigdesk is not reachable at the requested base URL.") from error

    report_path = (
        args.report.resolve()
        if args.report
        else runtime / "real-council-e2e-report.json"
    )
    output_path = runtime / "real-council-e2e-last-message.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command_prefix = [args.codex] if args.codex else _resolve_codex_command(root=ROOT)
    preflight = _load_preflight(args.base_url, bound)
    candidate_expectations = _load_candidate_expectations(args.base_url, preflight)
    command = build_codex_command(
        command_prefix,
        root=ROOT,
        runtime=runtime,
        base_url=args.base_url,
        output_path=output_path,
        preflight=preflight,
    )
    report = run_council_process(
        command,
        audit_path=bound.audit_path,
        candidate_output_path=output_path,
        timeout_seconds=args.timeout_seconds,
        diagnostic_path=runtime / "real-council-e2e.stderr.log",
        environment={
            **os.environ,
            "YIGDESK_URL": args.base_url,
            "YIGDESK_RUNTIME": str(runtime),
        },
    )
    revision = report.get("revision")
    if report.get("status") == "passed" and (
        not isinstance(revision, dict)
        or revision.get("revision_id") != bound.manifest["revision_id"]
        or revision.get("source_fingerprint") != bound.manifest["source"]["sha256"]
    ):
        report.update(
            {
                "status": "failed",
                "verified": False,
                "error": {"code": "COUNCIL_REVISION_MISMATCH"},
            }
        )
    if report.get("status") == "passed":
        try:
            report["candidate"] = read_candidate_result(
                output_path,
                expected_revision={
                    "revision_id": preflight["revision_id"],
                    "source_fingerprint": preflight["source_fingerprint"],
                    "packet_id": preflight["packet_id"],
                },
                expected_values=candidate_expectations,
            )
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            report.update(
                {
                    "status": "failed",
                    "verified": False,
                    "error": {"code": "COUNCIL_DECISION_INVALID"},
                }
            )
    report.update(
        {
            "mode": "codex-work-project-council",
            "model": COUNCIL_MODEL,
            "fast_mode": False,
            "reasoning_effort": "none",
            "session_id": bound.session_id,
        }
    )
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


def _load_preflight(base_url: str, bound) -> dict[str, str]:
    request = Request(
        f"{base_url.rstrip('/')}/api/analyze",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        packet = json.load(response)["packet"]
    scenario = bound.manifest["scenario"]
    preflight = {
        "revision_id": str(packet["revision_id"]),
        "source_fingerprint": str(packet["source_fingerprint"]),
        "packet_id": str(packet["packet_id"]),
        "requested_discount_pct": str(scenario["requested_discount_pct"]),
        "margin_floor_pct": str(scenario["margin_floor_pct"]),
        "current_discount_pct": str(scenario["current_discount_pct"]),
    }
    if (
        preflight["revision_id"] != bound.manifest["revision_id"]
        or preflight["source_fingerprint"] != bound.manifest["source"]["sha256"]
    ):
        raise SystemExit("Yigdesk preflight does not match the active bound session.")
    return preflight


def _load_candidate_expectations(
    base_url: str, preflight: dict[str, str]
) -> dict[str, object]:
    evaluation_request = Request(
        f"{base_url.rstrip('/')}/api/proposals/evaluate",
        data=json.dumps(
            {"requested_discount_pct": preflight["requested_discount_pct"]}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(evaluation_request, timeout=5) as response:
        evaluation_payload = json.load(response)
    request = Request(
        f"{base_url.rstrip('/')}/api/proposals/boundary?step_pct=0.01",
        method="GET",
    )
    with urlopen(request, timeout=5) as response:
        payload = json.load(response)
    revisions = (evaluation_payload.get("revision"), payload.get("revision"))
    if any(
        not isinstance(revision, dict)
        or any(
            revision.get(field) != preflight[field]
            for field in ("revision_id", "source_fingerprint", "packet_id")
        )
        for revision in revisions
    ):
        raise SystemExit("Yigdesk boundary evidence does not match the preflight revision.")
    proposal = evaluation_payload.get("proposal")
    if not isinstance(proposal, dict) or not isinstance(
        proposal.get("constraint_pass"), bool
    ):
        raise SystemExit("Yigdesk submitted-proposal evidence is unavailable.")
    boundary = payload.get("boundary")
    if not isinstance(boundary, dict):
        raise SystemExit("Yigdesk boundary evidence is unavailable.")
    first_unsafe = boundary.get("first_unsafe_proposal")
    if not isinstance(first_unsafe, dict):
        raise SystemExit("Yigdesk first-unsafe evidence is unavailable.")
    expected = {
        "submitted_discount_pct": preflight["requested_discount_pct"],
        "financially_feasible": proposal["constraint_pass"],
        "exact_max_discount_pct": boundary.get("exact_max_discount_pct"),
        "largest_safe_step_pct": boundary.get("largest_safe_step_pct"),
        "first_unsafe_pct": first_unsafe.get("requested_discount_pct"),
    }
    if not all(
        isinstance(expected[field], str) and expected[field]
        for field in (
            "submitted_discount_pct",
            "exact_max_discount_pct",
            "largest_safe_step_pct",
            "first_unsafe_pct",
        )
    ):
        raise SystemExit("Yigdesk boundary evidence is incomplete.")
    return expected


if __name__ == "__main__":
    sys.exit(main())
