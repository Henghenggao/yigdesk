"""Codex orchestration with engine-grounded verification."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "agent-result.schema.json"
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORT = "low"
SUPPORTED_REASONING_EFFORTS = frozenset(("minimal", "low", "medium", "high", "xhigh"))
REQUIRED_TOOLS = ("get_deal_context", "preview_consequence", "inspect_evidence")
PROGRESS_PHASES = frozenset(
    ("starting_codex", "calling_yigdesk_tools", "verifying_result")
)
PERMISSION_PROFILE = "yigdesk_agent"
CODEX_PROCESS_ENV_KEYS = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "CODEX_HOME",
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORGANIZATION",
    "OPENAI_PROJECT",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
)
SHELL_ENV_INCLUDE_ONLY = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "NO_COLOR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "YIGDESK_URL",
    "YIGDESK_AUDIT_FILE",
)
PASSIVE_TRACE_ITEMS = frozenset(("agent_message", "reasoning", "todo_list", "error"))
EXECUTION_ERROR_CODES = frozenset(
    (
        "AGENT_EXECUTION_FAILED",
        "AGENT_TIMEOUT",
        "AGENT_START_FAILED",
        "AGENT_PROCESS_FAILED",
        "AGENT_OUTPUT_INVALID",
        "BARE_AGENT_TIMEOUT",
        "BARE_AGENT_PROCESS_FAILED",
        "BARE_AGENT_OUTPUT_INVALID",
    )
)
VERIFICATION_ERROR_CODES = frozenset(
    (
        "AGENT_VERIFICATION_FAILED",
        "AGENT_REVISION_REQUIRED",
        "AGENT_AUDIT_INVALID",
        "AGENT_AUDIT_TOOL_FAILED",
        "AGENT_TRACE_THREAD_MISSING",
        "AGENT_TRACE_USAGE_INVALID",
        "AGENT_TRACE_ACTIVITY_MISSING",
        "AGENT_TRACE_TOOL_MISMATCH",
        "AGENT_AUDIT_TOOL_MISMATCH",
        "AGENT_PACKET_REVISION_MISSING",
        "AGENT_AUDIT_REVISION_MISMATCH",
        "AGENT_PACKET_ID_MISMATCH",
        "AGENT_VERDICT_MISMATCH",
        "AGENT_METRIC_MISMATCH",
        "AGENT_EVIDENCE_MISMATCH",
        "AGENT_DRAFT_STATUS_MISMATCH",
        "AGENT_SUMMARY_FIGURE_MISMATCH",
        "AGENT_READ_ONLY_PROOF_MISSING",
    )
)


class AgentExecutionError(RuntimeError):
    """Codex could not produce a usable result."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "AGENT_EXECUTION_FAILED",
        phase: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code if code in EXECUTION_ERROR_CODES else "AGENT_EXECUTION_FAILED"
        self.phase = phase
        self.elapsed_ms = elapsed_ms


class AgentVerificationError(RuntimeError):
    """Codex output did not match the deterministic consequence packet."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "AGENT_VERIFICATION_FAILED",
    ) -> None:
        super().__init__(message)
        self.code = (
            code if code in VERIFICATION_ERROR_CODES else "AGENT_VERIFICATION_FAILED"
        )
        self.phase = "verifying_result"


Executor = Callable[..., subprocess.CompletedProcess[str]]
ProgressCallback = Callable[[dict[str, Any]], None]


class CodexRunner:
    def __init__(
        self,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
        executable: str | None = None,
        timeout: float = 120,
        executor: Executor | None = None,
        progress_callback: ProgressCallback | None = None,
        temp_root: Path | None = None,
    ) -> None:
        self.model = model or os.environ.get("YIGDESK_CODEX_MODEL", DEFAULT_MODEL)
        self.reasoning_effort = reasoning_effort or os.environ.get(
            "YIGDESK_CODEX_REASONING_EFFORT", DEFAULT_REASONING_EFFORT
        )
        if self.reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
            raise ValueError(
                "Codex reasoning effort must be one of: "
                + ", ".join(sorted(SUPPORTED_REASONING_EFFORTS))
            )
        self.command_prefix = [executable] if executable else _resolve_codex_command()
        self.timeout = timeout
        self.executor = executor or _execute
        self.progress_callback = progress_callback
        self.temp_root = temp_root

    def run(
        self,
        expected_packet: dict[str, Any],
        *,
        base_url: str,
        revision_id: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        revision_id = revision_id or expected_packet.get("revision_id")
        if not revision_id:
            raise AgentVerificationError(
                "An immutable agent revision is required.",
                code="AGENT_REVISION_REQUIRED",
            )
        with tempfile.TemporaryDirectory(prefix="yigdesk-agent-", dir=self.temp_root) as directory:
            run_dir = Path(directory)
            _prepare_isolated_workspace(run_dir)
            answer_path = run_dir / "answer.json"
            audit_path = run_dir / "mcp-audit.jsonl"
            command = self._command(
                answer_path,
                audit_path,
                base_url,
                run_dir,
                revision_id,
                expected_packet,
            )
            environment = _codex_environment(
                {
                    "YIGDESK_URL": base_url,
                    "YIGDESK_AUDIT_FILE": str(audit_path),
                    "NO_COLOR": "1",
                }
            )
            started = time.perf_counter()
            callback = (
                progress_callback
                if progress_callback is not None
                else self.progress_callback
            )
            last_phase: str | None = None
            last_elapsed_ms = 0

            def report_progress(phase: str) -> None:
                nonlocal last_phase, last_elapsed_ms
                if phase not in PROGRESS_PHASES or phase == last_phase:
                    return
                elapsed_ms = max(
                    last_elapsed_ms,
                    round((time.perf_counter() - started) * 1000),
                )
                last_phase = phase
                last_elapsed_ms = elapsed_ms
                if callback is not None:
                    try:
                        callback({"phase": phase, "elapsed_ms": elapsed_ms})
                    except Exception:
                        pass

            report_progress("starting_codex")
            try:
                completed = self.executor(
                    command,
                    env=environment,
                    timeout=self.timeout,
                    cwd=run_dir,
                    trace_callback=report_progress,
                )
            except subprocess.TimeoutExpired as error:
                if _contains_yigdesk_mcp_activity(error.output):
                    report_progress("calling_yigdesk_tools")
                raise AgentExecutionError(
                    "Codex timed out before producing a verified result.",
                    code="AGENT_TIMEOUT",
                    phase=last_phase or "starting_codex",
                    elapsed_ms=max(
                        last_elapsed_ms,
                        round((time.perf_counter() - started) * 1000),
                    ),
                ) from None
            except OSError:
                raise AgentExecutionError(
                    "Codex could not start.",
                    code="AGENT_START_FAILED",
                    phase=last_phase or "starting_codex",
                    elapsed_ms=max(
                        last_elapsed_ms,
                        round((time.perf_counter() - started) * 1000),
                    ),
                ) from None
            latency_ms = round((time.perf_counter() - started) * 1000)
            if completed.returncode != 0:
                raise AgentExecutionError(
                    "Codex did not produce a verified result.",
                    code="AGENT_PROCESS_FAILED",
                    phase=last_phase or "starting_codex",
                    elapsed_ms=latency_ms,
                )
            trace = _read_codex_trace(completed.stdout)
            if any(
                activity.get("type") == "mcp_tool_call"
                and activity.get("server") == "yigdesk"
                and activity.get("tool") in REQUIRED_TOOLS
                for activity in trace["tool_activity"]
            ):
                report_progress("calling_yigdesk_tools")
            report_progress("verifying_result")
            try:
                answer = json.loads(answer_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as error:
                raise AgentExecutionError(
                    "Codex did not return schema-valid JSON output.",
                    code="AGENT_OUTPUT_INVALID",
                    phase="verifying_result",
                    elapsed_ms=latency_ms,
                ) from error
            _verify_trace(trace, expected_mcp_tools=REQUIRED_TOOLS)
            audit = _read_audit(audit_path)
            _verify(answer, audit, expected_packet)
            return {
                "verified": True,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "thread_id": trace["thread_id"],
                "latency_ms": latency_ms,
                "usage": trace["usage"],
                "tool_calls": [event["tool"] for event in audit],
                "audit": audit,
                "answer": answer,
            }

    def _command(
        self,
        answer_path: Path,
        audit_path: Path,
        base_url: str,
        workspace: Path,
        revision_id: str,
        expected_packet: dict[str, Any],
    ) -> list[str]:
        python_command = json.dumps(sys.executable)
        mcp_environment = (
            "mcp_servers.yigdesk.env={"
            f"YIGDESK_URL={json.dumps(base_url)},"
            f"YIGDESK_AUDIT_FILE={json.dumps(str(audit_path))},"
            f"YIGDESK_REVISION_ID={json.dumps(revision_id)},"
            f"YIGDESK_EXPECTED_PACKET_ID={json.dumps(expected_packet['packet_id'])},"
            "YIGDESK_EXPECTED_SOURCE_FINGERPRINT="
            f"{json.dumps(expected_packet['source_fingerprint'])},"
            f"PYTHONPATH={json.dumps(str(ROOT))}"
            "}"
        )
        return [
            *self.command_prefix,
            "exec",
            "--json",
            "--ephemeral",
            *_codex_isolation_args(workspace),
            "--model",
            self.model,
            "-c",
            f"model_reasoning_effort={json.dumps(self.reasoning_effort)}",
            "--output-schema",
            str(SCHEMA_PATH),
            "--output-last-message",
            str(answer_path),
            "-C",
            str(workspace),
            "-c",
            f"mcp_servers.yigdesk.command={python_command}",
            "-c",
            'mcp_servers.yigdesk.args=["-m","yigdesk.mcp_server"]',
            "-c",
            "mcp_servers.yigdesk.required=true",
            "-c",
            mcp_environment,
            "-c",
            "mcp_servers.yigdesk.startup_timeout_sec=15",
            "-c",
            "mcp_servers.yigdesk.tool_timeout_sec=30",
            _prompt(),
        ]


def _prompt() -> str:
    return """Analyze the current synthetic deal request with the Yigdesk MCP server.
You MUST call get_deal_context first, preview_consequence second, and inspect_evidence
for at least one address from the returned packet. The packet is the only authority:
copy its verdict and display metrics exactly and never calculate, infer, round, repair,
or introduce a figure. Set inspected_evidence to the exact canonical address string
that you pass to inspect_evidence, character for character, with no label or explanation.
Keep summary qualitative with no digits. Treat HOLD as terminal.
Do not invoke shell commands, file tools, web search, or any non-Yigdesk tool.
Return only the JSON object required by the supplied output schema. Nothing is sent."""


def _toml_map(values: Mapping[str, str]) -> str:
    return "{" + ",".join(f"{key}={json.dumps(value)}" for key, value in values.items()) + "}"


def _prepare_isolated_workspace(workspace: Path) -> None:
    (workspace / "home").mkdir(exist_ok=True)
    (workspace / "tmp").mkdir(exist_ok=True)


def _codex_isolation_args(workspace: Path) -> list[str]:
    """Keep model-visible tools, files, and environment inside one empty run root."""

    return [
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "-c",
        f"default_permissions={json.dumps(PERMISSION_PROFILE)}",
        "-c",
        (
            f"permissions.{PERMISSION_PROFILE}.description="
            + json.dumps("Read only the isolated Yigdesk run workspace.")
        ),
        "-c",
        (
            f"permissions.{PERMISSION_PROFILE}.filesystem="
            '{":minimal"="read",":workspace_roots"={"."="read"}}'
        ),
        "-c",
        f"permissions.{PERMISSION_PROFILE}.network.enabled=false",
        "-c",
        "features.shell_tool=false",
        "-c",
        "features.shell_snapshot=false",
        "-c",
        "allow_login_shell=false",
        "-c",
        'web_search="disabled"',
        "-c",
        'shell_environment_policy.inherit="all"',
        "-c",
        "shell_environment_policy.ignore_default_excludes=false",
        "-c",
        "shell_environment_policy.include_only="
        + json.dumps(SHELL_ENV_INCLUDE_ONLY),
        "-c",
        "shell_environment_policy.set="
        + _toml_map(
            {
                "HOME": str(workspace / "home"),
                "USERPROFILE": str(workspace / "home"),
                "HOMEDRIVE": "",
                "HOMEPATH": str(workspace / "home"),
                "APPDATA": str(workspace / "home" / "appdata"),
                "LOCALAPPDATA": str(workspace / "home" / "localappdata"),
                "TEMP": str(workspace / "tmp"),
                "TMP": str(workspace / "tmp"),
                "TMPDIR": str(workspace / "tmp"),
            }
        ),
    ]


def _execute(command, *, env, timeout, cwd, input=None, trace_callback=None):
    process = subprocess.Popen(
        command,
        env=env,
        cwd=cwd,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def drain(stream, sink: list[str], *, observe_trace: bool) -> None:
        if stream is None:
            return
        try:
            for line in stream:
                sink.append(line)
                if (
                    observe_trace
                    and trace_callback is not None
                    and _contains_yigdesk_mcp_activity(line)
                ):
                    trace_callback("calling_yigdesk_tools")
        finally:
            stream.close()

    stdout_thread = threading.Thread(
        target=drain,
        args=(process.stdout, stdout_lines),
        kwargs={"observe_trace": True},
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=drain,
        args=(process.stderr, stderr_lines),
        kwargs={"observe_trace": False},
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    if input is not None and process.stdin is not None:
        try:
            process.stdin.write(input)
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        stdout_thread.join()
        stderr_thread.join()
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output="".join(stdout_lines),
            stderr="".join(stderr_lines),
        ) from None
    stdout_thread.join()
    stderr_thread.join()
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
    )


def _codex_environment(
    extra: Mapping[str, str] | None = None,
    *,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the minimal process environment Codex needs without unrelated secrets."""

    available = os.environ if source is None else source
    environment = {
        key: value
        for key in CODEX_PROCESS_ENV_KEYS
        if (value := available.get(key)) is not None
    }
    if extra:
        environment.update(extra)
    return environment


def _resolve_codex_command(
    *,
    platform: str | None = None,
    which=None,
    root: Path | None = None,
) -> list[str]:
    platform = platform or os.name
    which = which or shutil.which
    root = root or ROOT
    node = which("node.exe") if platform == "nt" else which("node")
    local_script = root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    if node and local_script.is_file():
        return [node, str(local_script)]
    if platform == "nt":
        shim = which("codex.cmd")
        node = node or which("node")
        if shim and node:
            script = Path(shim).parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            if script.is_file():
                return [node, str(script)]
        native = which("codex.exe")
        if native:
            return [native]
    return [which("codex") or "codex"]


def _read_audit(path: Path) -> list[dict[str, Any]]:
    try:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise AgentVerificationError(
            "MCP tool audit is missing or invalid.",
            code="AGENT_AUDIT_INVALID",
        ) from error
    if any(not event.get("ok") for event in events):
        raise AgentVerificationError(
            "At least one audited MCP tool call failed.",
            code="AGENT_AUDIT_TOOL_FAILED",
        )
    return events


def _read_codex_trace(stdout: str) -> dict[str, Any]:
    thread_id = None
    usage: dict[str, int] = {}
    item_trace_available = False
    tool_activity: list[dict[str, str]] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        if event.get("type") in ("item.started", "item.updated", "item.completed"):
            item_trace_available = True
        if event.get("type") == "item.completed" and isinstance(event.get("item"), dict):
            item = event["item"]
            item_type = item.get("type")
            if isinstance(item_type, str) and item_type not in PASSIVE_TRACE_ITEMS:
                activity = {"type": item_type}
                if item_type == "mcp_tool_call":
                    activity.update(
                        {
                            "server": str(item.get("server", "")),
                            "tool": str(item.get("tool", "")),
                        }
                    )
                tool_activity.append(activity)
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
    return {
        "thread_id": thread_id,
        "usage": usage,
        "item_trace_available": item_trace_available,
        "tool_activity": tool_activity,
    }


def _contains_yigdesk_mcp_activity(output: str | bytes | None) -> bool:
    if isinstance(output, bytes):
        stdout = output.decode("utf-8", errors="replace")
    elif isinstance(output, str):
        stdout = output
    else:
        return False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") not in ("item.started", "item.updated", "item.completed"):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if (
            item.get("type") == "mcp_tool_call"
            and item.get("server") == "yigdesk"
            and item.get("tool") in REQUIRED_TOOLS
        ):
            return True
    return False


def _verify_trace(
    trace: dict[str, Any],
    *,
    expected_mcp_tools: tuple[str, ...] | None = None,
) -> None:
    if not isinstance(trace.get("thread_id"), str) or not trace["thread_id"]:
        raise AgentVerificationError(
            "Codex trace is missing a thread identifier.",
            code="AGENT_TRACE_THREAD_MISSING",
        )
    usage = trace.get("usage")
    if not isinstance(usage, dict) or not all(
        isinstance(usage.get(field), int) and usage[field] >= 0
        for field in ("input_tokens", "output_tokens")
    ):
        raise AgentVerificationError(
            "Codex trace is missing valid token usage.",
            code="AGENT_TRACE_USAGE_INVALID",
        )
    if expected_mcp_tools is not None and not trace.get("item_trace_available"):
        raise AgentVerificationError(
            "Codex trace is missing item-level tool activity.",
            code="AGENT_TRACE_ACTIVITY_MISSING",
        )
    if expected_mcp_tools is not None:
        expected_activity = [
            {"type": "mcp_tool_call", "server": "yigdesk", "tool": tool}
            for tool in expected_mcp_tools
        ]
        if trace.get("tool_activity") != expected_activity:
            raise AgentVerificationError(
                "Codex trace contains tool activity outside the three required Yigdesk MCP calls.",
                code="AGENT_TRACE_TOOL_MISMATCH",
            )


def _verify(answer: dict[str, Any], audit: list[dict[str, Any]], packet: dict[str, Any]) -> None:
    actual_tools = [event.get("tool") for event in audit]
    if actual_tools != list(REQUIRED_TOOLS):
        raise AgentVerificationError(
            "Required MCP tool audit order is get_deal_context, preview_consequence, inspect_evidence.",
            code="AGENT_AUDIT_TOOL_MISMATCH",
        )
    revision = {
        "revision_id": packet.get("revision_id"),
        "source_fingerprint": packet.get("source_fingerprint"),
        "packet_id": packet.get("packet_id"),
    }
    if any(not value for value in revision.values()):
        raise AgentVerificationError(
            "Engine packet is missing immutable revision proof.",
            code="AGENT_PACKET_REVISION_MISSING",
        )
    for event in audit:
        for field, value in revision.items():
            if event.get(field) != value:
                raise AgentVerificationError(
                    f"MCP audit {field} does not match the agent revision.",
                    code="AGENT_AUDIT_REVISION_MISMATCH",
                )
    consequence = packet["consequence"]
    expected = {
        "packet_id": packet["packet_id"],
        "verdict": consequence["verdict"],
        "metrics": {
            "net_arr": consequence["display"]["net_arr"],
            "arr_impact": consequence["display"]["arr_impact"],
            "gross_margin": consequence["display"]["gross_margin"],
            "headroom": consequence["display"]["headroom"],
        },
    }
    for field in ("packet_id", "verdict"):
        if answer.get(field) != expected[field]:
            code = (
                "AGENT_PACKET_ID_MISMATCH"
                if field == "packet_id"
                else "AGENT_VERDICT_MISMATCH"
            )
            raise AgentVerificationError(
                f"Codex {field} does not match the engine packet.",
                code=code,
            )
    metrics = answer.get("metrics") or {}
    for field, value in expected["metrics"].items():
        if metrics.get(field) != value:
            raise AgentVerificationError(
                f"Codex metric {field} does not match the engine packet.",
                code="AGENT_METRIC_MISMATCH",
            )
    evidence = {cell["address"] for cell in consequence["evidence_cells"]}
    inspected = answer.get("inspected_evidence")
    audited_inspections = {
        event.get("address") for event in audit if event.get("tool") == "inspect_evidence"
    }
    if inspected not in evidence or inspected not in audited_inspections:
        raise AgentVerificationError(
            "Inspected evidence is not proven by the packet and MCP audit.",
            code="AGENT_EVIDENCE_MISMATCH",
        )
    if answer.get("draft_status") != "NOT_SENT":
        raise AgentVerificationError(
            "Codex must declare the draft as NOT_SENT.",
            code="AGENT_DRAFT_STATUS_MISMATCH",
        )
    if re.search(r"\d", str(answer.get("summary", ""))):
        raise AgentVerificationError(
            "Codex summary introduced a figure outside structured packet metrics.",
            code="AGENT_SUMMARY_FIGURE_MISMATCH",
        )
    if packet.get("analysis_bytes_unchanged") is not True:
        raise AgentVerificationError(
            "The engine did not prove read-only workbook bytes.",
            code="AGENT_READ_ONLY_PROOF_MISSING",
        )
