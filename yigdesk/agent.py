"""Codex orchestration against the deterministic blackboard MCP surface.

The Yigdesk-assisted Codex arm drives the blackboard server (``yigdesk.blackboard_mcp``)
over stdio. Determinism is the guarantee: a figure only becomes real when Codex calls
``propose_candidate`` and the engine prices it, so there is no post-hoc field-drift
verifier here -- Codex copies the engine-returned consequence verbatim. The sandbox
isolation and secret-hygiene guarantees around the Codex process are preserved.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from yigdesk.core.blackboard import Blackboard
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "agent-answer.schema.json"
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORT = "low"
SUPPORTED_REASONING_EFFORTS = frozenset(("minimal", "low", "medium", "high", "xhigh"))
# A number only becomes real by being priced through propose_candidate; that is the one
# op the assisted arm must be observed calling.
REQUIRED_TOOLS = ("propose_candidate",)
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
    "YIGDESK_SCENARIO",
    "YIGDESK_LEDGER",
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
        "AGENT_TRACE_THREAD_MISSING",
        "AGENT_TRACE_USAGE_INVALID",
        "AGENT_TRACE_ACTIVITY_MISSING",
        "AGENT_TRACE_TOOL_MISMATCH",
        # Retained for parity with the API/benchmark failure-summary allowlist.
        "AGENT_EVIDENCE_MISMATCH",
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
    """Codex output did not satisfy the deterministic trace guarantees."""

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
        scenario_dir: Path,
        *,
        proposal: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        # Resolve to absolute: the in-process pre-open reads scenario_dir against this
        # cwd, but the same value is exported as YIGDESK_SCENARIO for Codex's MCP
        # subprocess, which resolves it against its own cwd (codex runs with -C <workspace>).
        scenario_dir = Path(scenario_dir).resolve()
        with tempfile.TemporaryDirectory(prefix="yigdesk-agent-", dir=self.temp_root) as directory:
            run_dir = Path(directory)
            _prepare_isolated_workspace(run_dir)
            answer_path = run_dir / "answer.json"
            ledger_path = run_dir / "board.jsonl"
            try:
                # Pre-open the decision on the run's private ledger so Codex's required ops
                # reduce to propose_candidate + read_board. This is also load-bearing:
                # projection.fold drops a PROPOSE_CANDIDATE whose decision is not already open.
                _open_decision_in_process(scenario_dir, ledger_path, proposal)
            except (OSError, json.JSONDecodeError, KeyError) as error:
                # A missing/unbuilt scenario (no model.json or workbook) must surface as a
                # typed start failure -- callers (app.py, benchmark._safe_error_summary)
                # only handle AgentExecutionError/AgentVerificationError.
                raise AgentExecutionError(
                    "Scenario is missing or not built "
                    "(model.json or workbook is unavailable).",
                    code="AGENT_START_FAILED",
                    phase="starting_codex",
                ) from error
            command = self._command(answer_path, scenario_dir, ledger_path, run_dir, proposal)
            environment = _codex_environment(
                {
                    "YIGDESK_SCENARIO": str(scenario_dir),
                    "YIGDESK_LEDGER": str(ledger_path),
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
                    "Codex timed out before producing a result.",
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
                    "Codex did not produce a result.",
                    code="AGENT_PROCESS_FAILED",
                    phase=last_phase or "starting_codex",
                    elapsed_ms=latency_ms,
                )
            trace = _read_codex_trace(completed.stdout)
            tool_calls = [
                activity["tool"]
                for activity in trace["tool_activity"]
                if activity.get("type") == "mcp_tool_call"
                and activity.get("server") == "yigdesk"
            ]
            if any(tool in REQUIRED_TOOLS for tool in tool_calls):
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
            # Assisted path: this only checks thread_id + usage. The allowlist /
            # AGENT_TRACE_TOOL_MISMATCH branch of _verify_trace is intentionally reserved for
            # Task 5b's bare arm (expected_mcp_tools=()); the assisted trace legitimately holds
            # multiple ops, so op-presence is enforced separately by the gate just below.
            _verify_trace(trace)
            if not all(tool in tool_calls for tool in REQUIRED_TOOLS):
                raise AgentVerificationError(
                    "Codex did not price the candidate through the engine "
                    "(propose_candidate is missing from the trace).",
                    code="AGENT_TRACE_ACTIVITY_MISSING",
                )
            return {
                "verified": True,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "thread_id": trace["thread_id"],
                "latency_ms": latency_ms,
                "usage": trace["usage"],
                "tool_calls": tool_calls,
                "answer": answer,
            }

    def _command(
        self,
        answer_path: Path,
        scenario_dir: Path,
        ledger_path: Path,
        workspace: Path,
        proposal: Mapping[str, Any],
    ) -> list[str]:
        python_command = json.dumps(sys.executable)
        mcp_environment = (
            "mcp_servers.yigdesk.env={"
            f"YIGDESK_SCENARIO={json.dumps(str(scenario_dir))},"
            f"YIGDESK_LEDGER={json.dumps(str(ledger_path))},"
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
            'mcp_servers.yigdesk.args=["-m","yigdesk.blackboard_mcp"]',
            "-c",
            "mcp_servers.yigdesk.required=true",
            "-c",
            mcp_environment,
            "-c",
            "mcp_servers.yigdesk.startup_timeout_sec=15",
            "-c",
            "mcp_servers.yigdesk.tool_timeout_sec=30",
            _prompt(proposal),
        ]


def _prompt(proposal: Mapping[str, Any]) -> str:
    decision_id = str(proposal["decision_id"])
    candidate_id = str(proposal["candidate_id"])
    question = str(proposal.get("question", ""))
    overrides = json.dumps(proposal.get("overrides") or {}, sort_keys=True)
    return f"""Drive one decision on the Yigdesk deterministic blackboard (MCP server "yigdesk").
Decision {json.dumps(decision_id)} is already open on the board. Question: {json.dumps(question)}.
Do exactly the following and nothing else:
1. Call propose_candidate with decision_id={json.dumps(decision_id)}, candidate_id={json.dumps(candidate_id)},
   and overrides={overrides}. The engine prices the candidate and returns a consequence.
2. Call read_board to confirm the candidate and its priced consequence on the board.
The engine is the sole authority. Copy the consequence verdict verbatim into "verdict", and copy
every priced metric value verbatim into "metrics", keyed by the engine's metric id, exactly as the
engine returned it. Never compute, round, infer, repair, or invent any figure; if a metric value is
absent, copy it as returned. Treat a HOLD verdict as terminal.
Do not invoke shell commands, file tools, web search, or any non-yigdesk tool.
Return only the JSON object required by the supplied output schema. Nothing is sent."""


def _open_decision_in_process(
    scenario_dir: Path,
    ledger_path: Path,
    proposal: Mapping[str, Any],
) -> None:
    """Open the decision on the run's private ledger before Codex is spawned.

    Constructs a Blackboard over the scenario's evaluator/source (mirroring
    ``board.build_blackboard``) pointed at the isolated run ledger, so Codex only needs to
    call propose_candidate + read_board.
    """
    model = json.loads((scenario_dir / "model.json").read_text(encoding="utf-8"))
    source = ModelSource(scenario_dir / model["workbook"], model["input_refs"])
    board = Blackboard(str(ledger_path), ExpressionEvaluator(model), source)
    board.open_decision(
        str(proposal["decision_id"]),
        str(proposal.get("question", "")),
        str(proposal.get("decision_type", "")),
        proposal.get("policy") or {},
        actor="agent:runner",
        role="owner",
    )


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
                "Codex trace contains tool activity outside the expected Yigdesk MCP allowlist.",
                code="AGENT_TRACE_TOOL_MISMATCH",
            )
