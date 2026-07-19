from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from openpyxl import Workbook

from yigdesk.agent import (
    AgentExecutionError,
    AgentVerificationError,
    CodexRunner,
    _codex_environment,
    _execute,
    _read_codex_trace,
    _resolve_codex_command,
    _verify_trace,
)


# --- hermetic scenario + proposal helpers ------------------------------------


def build_scenario(root):
    """Build a tmp council-style scenario (workbook + model.json), mirroring
    scripts/build_scenarios.py + data/scenarios/council_discount/model.json."""
    scenario = root / "scenario"
    scenario.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Deal Inputs"
    for addr, value in {"B2": 1000, "B3": 0, "B4": 480, "B5": 40}.items():
        sheet[addr] = value
    workbook.save(scenario / "council_deal.xlsx")
    model = {
        "workbook": "council_deal.xlsx",
        "input_refs": {
            "list_arr": "Deal Inputs!B2",
            "discount": "Deal Inputs!B3",
            "cogs": "Deal Inputs!B4",
            "floor": "Deal Inputs!B5",
        },
        "metrics": [
            {"id": "net_arr", "label": "Net ARR", "formula": "list_arr * (1 - discount/100)", "unit": "$k"},
            {"id": "gross_profit", "label": "Gross profit", "formula": "net_arr - cogs", "requires": ["cogs"], "unit": "$k"},
            {"id": "gross_margin", "label": "Gross margin", "formula": "gross_profit / net_arr * 100", "requires": ["cogs"], "unit": "%"},
            {"id": "headroom", "label": "Headroom", "formula": "gross_margin - floor", "requires": ["cogs"], "unit": "pt"},
        ],
        "constraints": [{"metric": "headroom", "op": ">=", "value": 0}],
    }
    (scenario / "model.json").write_text(json.dumps(model), encoding="utf-8")
    return scenario


def proposal(**overrides):
    base = {
        "decision_id": "d1",
        "candidate_id": "c1",
        "question": "Approve a 10% council discount?",
        "overrides": {"discount": 10},
    }
    base.update(overrides)
    return base


def engine_answer(**overrides):
    """A schema-valid answer that copies the engine-priced consequence verbatim."""
    answer = {
        "verdict": "ok",
        "metrics": {
            "net_arr": "900.00",
            "gross_profit": "420.00",
            "gross_margin": "46.67",
            "headroom": "6.67",
        },
    }
    answer.update(overrides)
    return answer


def _default_trace():
    return [
        {"type": "thread.started", "thread_id": "thread-123"},
        *[
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "yigdesk",
                    "tool": tool,
                    "status": "completed",
                },
            }
            for tool in ("propose_candidate", "read_board")
        ],
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 420, "cached_input_tokens": 20, "output_tokens": 80},
        },
    ]


def executor_with(answer, *, trace=None, scenario_dir=None):
    def execute(command, *, env, timeout, cwd, trace_callback=None):
        # Sandbox isolation guarantees (preserved).
        assert "--ignore-user-config" in command
        assert "--ignore-rules" in command
        assert "--strict-config" in command
        assert "--skip-git-repo-check" in command
        assert "--sandbox" not in command
        assert 'default_permissions="yigdesk_agent"' in command
        permissions = next(
            item for item in command if item.startswith("permissions.yigdesk_agent.filesystem=")
        )
        assert '":minimal"="read"' in permissions
        assert '":workspace_roots"={"."="read"}' in permissions
        assert "permissions.yigdesk_agent.network.enabled=false" in command
        assert "features.shell_tool=false" in command
        assert "features.shell_snapshot=false" in command
        assert "allow_login_shell=false" in command
        assert 'web_search="disabled"' in command
        assert 'shell_environment_policy.inherit="all"' in command
        assert "shell_environment_policy.ignore_default_excludes=false" in command
        shell_include_only = next(
            item for item in command if item.startswith("shell_environment_policy.include_only=")
        )
        assert "PATH" in shell_include_only
        assert "SYSTEMROOT" in shell_include_only
        assert "CODEX_HOME" not in shell_include_only
        assert "OPENAI_API_KEY" not in shell_include_only
        shell_set = next(
            item for item in command if item.startswith("shell_environment_policy.set=")
        )
        assert f'HOME={json.dumps(str(cwd / "home"))}' in shell_set
        assert f'USERPROFILE={json.dumps(str(cwd / "home"))}' in shell_set
        assert f'TEMP={json.dumps(str(cwd / "tmp"))}' in shell_set
        assert "CODEX_HOME" not in shell_set
        # New surface: blackboard MCP over stdio, scenario + ledger env.
        assert 'mcp_servers.yigdesk.args=["-m","yigdesk.blackboard_mcp"]' in command
        assert 'mcp_servers.yigdesk.args=["-m","yigdesk.mcp_server"]' not in command
        mcp_env = next(
            item for item in command if item.startswith("mcp_servers.yigdesk.env=")
        )
        assert "YIGDESK_SCENARIO" in mcp_env
        assert "YIGDESK_LEDGER" in mcp_env
        assert "PYTHONPATH" in mcp_env
        assert str(cwd) == command[command.index("-C") + 1]
        # Injected process env carries the scenario (absolute) and only allowlisted keys.
        assert env.get("YIGDESK_SCENARIO") == (
            str(Path(scenario_dir).resolve()) if scenario_dir else env.get("YIGDESK_SCENARIO")
        )
        assert env.get("YIGDESK_LEDGER")
        assert "DATABASE_PASSWORD" not in env

        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(answer, handle)
        events = _default_trace() if trace is None else trace
        stdout = "\n".join(json.dumps(event) for event in events)
        if trace_callback is not None:
            trace_callback("calling_yigdesk_tools")
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    return execute


# --- structural: the new blackboard-driven run -------------------------------


def test_runner_accepts_engine_grounded_blackboard_output(tmp_path):
    scenario = build_scenario(tmp_path)
    runner = CodexRunner(
        model="gpt-5.6-sol",
        executor=executor_with(engine_answer(), scenario_dir=scenario),
        temp_root=tmp_path,
    )

    result = runner.run(scenario, proposal=proposal())

    assert result["verified"] is True
    assert result["thread_id"] == "thread-123"
    assert result["model"] == "gpt-5.6-sol"
    assert result["reasoning_effort"] == "low"
    assert "propose_candidate" in result["tool_calls"]
    assert result["usage"]["input_tokens"] == 420
    assert result["answer"]["verdict"] == "ok"
    assert result["answer"]["metrics"]["gross_margin"] == "46.67"


def test_runner_targets_blackboard_mcp_with_scenario_env(tmp_path):
    scenario = build_scenario(tmp_path)
    captured = {}
    delegate = executor_with(engine_answer(), scenario_dir=scenario)

    def execute(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return delegate(command, **kwargs)

    runner = CodexRunner(executor=execute, temp_root=tmp_path)
    runner.run(scenario, proposal=proposal())

    command = captured["command"]
    assert 'mcp_servers.yigdesk.args=["-m","yigdesk.blackboard_mcp"]' in command
    assert 'mcp_servers.yigdesk.args=["-m","yigdesk.mcp_server"]' not in command
    assert captured["env"]["YIGDESK_SCENARIO"] == str(scenario.resolve())

    schema_path = command[command.index("--output-schema") + 1]
    schema = json.loads(open(schema_path, encoding="utf-8").read())
    assert "verdict" in schema["properties"]
    assert "metrics" in schema["properties"]
    assert set(schema["required"]) >= {"verdict", "metrics"}


def test_runner_injects_the_proposed_candidate_into_the_prompt(tmp_path):
    scenario = build_scenario(tmp_path)
    captured = {}
    delegate = executor_with(engine_answer(), scenario_dir=scenario)

    def execute(command, **kwargs):
        captured["command"] = command
        return delegate(command, **kwargs)

    runner = CodexRunner(executor=execute, temp_root=tmp_path)
    runner.run(scenario, proposal=proposal())

    prompt = captured["command"][-1]
    assert "propose_candidate" in prompt
    assert "read_board" in prompt
    assert "d1" in prompt
    assert "c1" in prompt
    assert json.dumps({"discount": 10}) in prompt


def test_runner_pins_low_reasoning_effort_in_strict_codex_config(tmp_path):
    scenario = build_scenario(tmp_path)
    captured = {}
    delegate = executor_with(engine_answer(), scenario_dir=scenario)

    def execute(command, **kwargs):
        captured["command"] = command
        return delegate(command, **kwargs)

    runner = CodexRunner(executor=execute, temp_root=tmp_path)
    runner.run(scenario, proposal=proposal())

    assert 'model_reasoning_effort="low"' in captured["command"]


def test_runner_requires_yigdesk_mcp_server_to_initialize(tmp_path):
    scenario = build_scenario(tmp_path)
    captured = {}
    delegate = executor_with(engine_answer(), scenario_dir=scenario)

    def execute(command, **kwargs):
        captured["command"] = command
        return delegate(command, **kwargs)

    runner = CodexRunner(executor=execute, temp_root=tmp_path)
    runner.run(scenario, proposal=proposal())

    assert "mcp_servers.yigdesk.required=true" in captured["command"]


# --- progress phases (monotonic + sanitized) ---------------------------------


def test_runner_reports_only_sanitized_monotonic_progress_phases(tmp_path):
    scenario = build_scenario(tmp_path)
    events = []
    runner = CodexRunner(
        executor=executor_with(engine_answer(), scenario_dir=scenario),
        temp_root=tmp_path,
    )

    runner.run(scenario, proposal=proposal(), progress_callback=events.append)

    assert [event["phase"] for event in events] == [
        "starting_codex",
        "calling_yigdesk_tools",
        "verifying_result",
    ]
    assert all(set(event) == {"phase", "elapsed_ms"} for event in events)
    assert [event["elapsed_ms"] for event in events] == sorted(
        event["elapsed_ms"] for event in events
    )


def test_runner_timeout_exposes_only_sanitized_partial_trace_phase(tmp_path):
    scenario = build_scenario(tmp_path)
    secret = "customer-sensitive-prompt"
    partial_trace = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-secret"}),
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "yigdesk",
                        "tool": "propose_candidate",
                        "arguments": {"overrides": {"discount": secret}},
                    },
                }
            ),
        ]
    )

    def execute(command, *, timeout, **_kwargs):
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=partial_trace,
            stderr=f"stderr must not leak: {secret}",
        )

    events = []
    runner = CodexRunner(executor=execute, temp_root=tmp_path)

    with pytest.raises(AgentExecutionError) as caught:
        runner.run(scenario, proposal=proposal(), progress_callback=events.append)

    error = caught.value
    assert error.code == "AGENT_TIMEOUT"
    assert error.phase == "calling_yigdesk_tools"
    assert isinstance(error.elapsed_ms, int) and error.elapsed_ms >= 0
    assert [event["phase"] for event in events] == [
        "starting_codex",
        "calling_yigdesk_tools",
    ]
    assert secret not in str(error)
    assert secret not in repr(error)
    assert all(secret not in repr(event) for event in events)


def test_real_executor_streams_sanitized_mcp_phase_before_process_exit(tmp_path):
    event = {
        "type": "item.started",
        "item": {
            "type": "mcp_tool_call",
            "server": "yigdesk",
            "tool": "propose_candidate",
            "arguments": {"must_not_reach_callback": "sensitive"},
        },
    }
    script = (
        "import time; "
        f"print({json.dumps(json.dumps(event))}, flush=True); "
        "time.sleep(0.3)"
    )
    observations = []

    completed = _execute(
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        timeout=2,
        cwd=tmp_path,
        trace_callback=lambda phase: observations.append((phase, time.perf_counter())),
    )
    finished = time.perf_counter()

    assert completed.returncode == 0
    assert [phase for phase, _observed_at in observations] == ["calling_yigdesk_tools"]
    assert finished - observations[0][1] >= 0.1


# --- required-op + trace integrity -------------------------------------------


def test_runner_requires_propose_candidate_activity_in_trace(tmp_path):
    scenario = build_scenario(tmp_path)
    trace = [
        {"type": "thread.started", "thread_id": "thread-123"},
        {
            "type": "item.completed",
            "item": {"type": "mcp_tool_call", "server": "yigdesk", "tool": "read_board"},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
    ]
    runner = CodexRunner(
        executor=executor_with(engine_answer(), trace=trace, scenario_dir=scenario),
        temp_root=tmp_path,
    )

    with pytest.raises(AgentVerificationError) as caught:
        runner.run(scenario, proposal=proposal())

    assert caught.value.code == "AGENT_TRACE_ACTIVITY_MISSING"


@pytest.mark.parametrize(
    "trace",
    [
        [{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}],
        [{"type": "thread.started", "thread_id": "thread-123"}],
    ],
)
def test_runner_rejects_incomplete_codex_trace(tmp_path, trace):
    scenario = build_scenario(tmp_path)
    runner = CodexRunner(
        executor=executor_with(engine_answer(), trace=trace, scenario_dir=scenario),
        temp_root=tmp_path,
    )

    with pytest.raises(AgentVerificationError, match="trace"):
        runner.run(scenario, proposal=proposal())


def test_runner_rejects_invalid_answer_json(tmp_path):
    scenario = build_scenario(tmp_path)

    def execute(command, *, env, timeout, cwd, trace_callback=None):
        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("not json{")
        stdout = "\n".join(json.dumps(event) for event in _default_trace())
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    runner = CodexRunner(executor=execute, temp_root=tmp_path)

    with pytest.raises(AgentExecutionError) as caught:
        runner.run(scenario, proposal=proposal())

    assert caught.value.code == "AGENT_OUTPUT_INVALID"


# --- scenario path resolution + typed unbuilt-scenario failure ---------------


def test_runner_resolves_relative_scenario_dir_to_absolute_env(tmp_path, monkeypatch):
    # Codex's MCP subprocess resolves YIGDESK_SCENARIO against its own cwd (-C <workspace>),
    # so a relative input must be exported as an absolute path.
    build_scenario(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured = {}
    delegate = executor_with(engine_answer())

    def execute(command, **kwargs):
        captured["env"] = kwargs["env"]
        return delegate(command, **kwargs)

    runner = CodexRunner(executor=execute, temp_root=tmp_path)
    result = runner.run(Path("scenario"), proposal=proposal())

    assert result["verified"] is True
    injected = captured["env"]["YIGDESK_SCENARIO"]
    assert Path(injected).is_absolute()
    assert Path(injected) == Path("scenario").resolve()


def test_runner_raises_typed_error_for_missing_scenario(tmp_path):
    runner = CodexRunner(executor=executor_with(engine_answer()), temp_root=tmp_path)

    with pytest.raises(AgentExecutionError) as caught:
        runner.run(tmp_path / "does-not-exist", proposal=proposal())

    assert caught.value.code == "AGENT_START_FAILED"


def test_runner_raises_typed_error_for_unbuilt_workbook(tmp_path):
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    # model.json present, but the workbook is never built (built on demand elsewhere).
    (scenario / "model.json").write_text(
        json.dumps({"workbook": "council_deal.xlsx", "input_refs": {}, "metrics": []}),
        encoding="utf-8",
    )
    runner = CodexRunner(executor=executor_with(engine_answer()), temp_root=tmp_path)

    with pytest.raises(AgentExecutionError) as caught:
        runner.run(scenario, proposal=proposal())

    assert caught.value.code == "AGENT_START_FAILED"


# --- preserved helper guarantees ---------------------------------------------


def test_verify_trace_enforces_thread_usage_and_tool_allowlist():
    good = {
        "thread_id": "thread-1",
        "usage": {"input_tokens": 5, "output_tokens": 1},
        "item_trace_available": True,
        "tool_activity": [],
    }
    # Bare-arm contract (Task 5b): empty allowlist accepts an empty tool trace.
    _verify_trace(good, expected_mcp_tools=())

    with pytest.raises(AgentVerificationError) as thread_missing:
        _verify_trace({**good, "thread_id": None})
    assert thread_missing.value.code == "AGENT_TRACE_THREAD_MISSING"

    with pytest.raises(AgentVerificationError) as usage_missing:
        _verify_trace({**good, "usage": {}})
    assert usage_missing.value.code == "AGENT_TRACE_USAGE_INVALID"

    extra_tool = {
        **good,
        "tool_activity": [{"type": "mcp_tool_call", "server": "other", "tool": "read_file"}],
    }
    with pytest.raises(AgentVerificationError) as tool_mismatch:
        _verify_trace(extra_tool, expected_mcp_tools=())
    assert tool_mismatch.value.code == "AGENT_TRACE_TOOL_MISMATCH"


def test_read_codex_trace_captures_thread_usage_and_tool_activity():
    stdout = "\n".join(
        json.dumps(event)
        for event in [
            {"type": "thread.started", "thread_id": "thread-9"},
            {
                "type": "item.completed",
                "item": {"type": "mcp_tool_call", "server": "yigdesk", "tool": "propose_candidate"},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 4}},
        ]
    )
    trace = _read_codex_trace(stdout)
    assert trace["thread_id"] == "thread-9"
    assert trace["usage"] == {"input_tokens": 3, "output_tokens": 4}
    assert trace["item_trace_available"] is True
    assert trace["tool_activity"] == [
        {"type": "mcp_tool_call", "server": "yigdesk", "tool": "propose_candidate"}
    ]


def test_runner_rejects_unknown_reasoning_effort():
    with pytest.raises(ValueError, match="reasoning effort"):
        CodexRunner(reasoning_effort="fastest")


def test_windows_resolver_uses_accessible_node_cli_instead_of_store_executable(tmp_path):
    npm = tmp_path / "npm"
    script = npm / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    shim = npm / "codex.cmd"
    shim.write_text("", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")

    def which(name):
        return {"codex.cmd": str(shim), "node.exe": str(node)}.get(name)

    assert _resolve_codex_command(
        platform="nt",
        which=which,
        root=tmp_path / "repository-without-local-codex",
    ) == [str(node), str(script)]


def test_resolver_prefers_repository_codex_package_on_clean_machine(tmp_path):
    script = tmp_path / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    node = tmp_path / "node"
    node.write_text("", encoding="utf-8")

    def which(name):
        return str(node) if name in {"node", "node.exe"} else None

    assert _resolve_codex_command(platform="posix", which=which, root=tmp_path) == [
        str(node),
        str(script),
    ]


def test_codex_process_environment_excludes_unrelated_server_secrets():
    environment = _codex_environment(
        {"NO_COLOR": "1"},
        source={
            "PATH": "/usr/bin",
            "HOME": "/service",
            "CODEX_API_KEY": "codex-credential",
            "DATABASE_PASSWORD": "must-not-cross-boundary",
            "AWS_SECRET_ACCESS_KEY": "must-not-cross-boundary",
        },
    )

    assert environment == {
        "PATH": "/usr/bin",
        "HOME": "/service",
        "CODEX_API_KEY": "codex-credential",
        "NO_COLOR": "1",
    }
