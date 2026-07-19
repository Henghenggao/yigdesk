from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

import pytest

from yigdesk.agent import (
    AgentExecutionError,
    AgentVerificationError,
    CodexRunner,
    _codex_environment,
    _execute,
    _resolve_codex_command,
)


def expected_packet():
    return {
        "packet_id": "cpkt-abc123",
        "revision_id": "rev-test123",
        "source_fingerprint": "sha256-test123",
        "analysis_bytes_unchanged": True,
        "consequence": {
            "verdict": "READY FOR CFO",
            "display": {
                "net_arr": "$880k",
                "arr_impact": "-$20k",
                "gross_margin": "45.5%",
                "headroom": "5.5%",
            },
            "evidence_cells": [{"address": "Deal Model!B4"}],
        },
    }


def agent_answer(**overrides):
    answer = {
        "packet_id": "cpkt-abc123",
        "verdict": "READY FOR CFO",
        "summary": "Evidence is complete and the request is ready for CFO review.",
        "metrics": {
            "net_arr": "$880k",
            "arr_impact": "-$20k",
            "gross_margin": "45.5%",
            "headroom": "5.5%",
        },
        "inspected_evidence": "Deal Model!B4",
        "draft_status": "NOT_SENT",
    }
    answer.update(overrides)
    return answer


def audited_calls(**overrides):
    revision = {
        "revision_id": "rev-test123",
        "source_fingerprint": "sha256-test123",
        "packet_id": "cpkt-abc123",
    }
    events = [
        {"tool": "get_deal_context", "ok": True, **revision},
        {"tool": "preview_consequence", "ok": True, "verdict": "READY FOR CFO", **revision},
        {
            "tool": "inspect_evidence",
            "ok": True,
            "address": "Deal Model!B4",
            **revision,
        },
    ]
    for index, changes in overrides.items():
        events[int(index)].update(changes)
    return events


def executor_with(answer, *, audit=None, trace=None):
    def execute(command, *, env, timeout, cwd, trace_callback=None):
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
        assert "PYTHONPATH" in next(item for item in command if item.startswith("mcp_servers.yigdesk.env="))
        assert str(cwd) == command[command.index("-C") + 1]
        assert any(
            item.startswith("mcp_servers.yigdesk.env=")
            and "YIGDESK_URL" in item
            and "YIGDESK_AUDIT_FILE" in item
            and "YIGDESK_REVISION_ID" in item
            for item in command
        )
        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(answer, handle)
        recorded_audit = audit if audit is not None else audited_calls()
        with open(env["YIGDESK_AUDIT_FILE"], "w", encoding="utf-8") as handle:
            for event in recorded_audit:
                handle.write(json.dumps(event) + "\n")
        events = (
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-123"}),
                *[
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "mcp_tool_call",
                                "server": "yigdesk",
                                "tool": tool,
                                "status": "completed",
                            },
                        }
                    )
                    for tool in (
                        "get_deal_context",
                        "preview_consequence",
                        "inspect_evidence",
                    )
                ],
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 420, "cached_input_tokens": 20, "output_tokens": 80},
                    }
                ),
            ]
            if trace is None
            else [json.dumps(event) for event in trace]
        )
        if trace_callback is not None:
            trace_callback("calling_yigdesk_tools")
        stdout = "\n".join(events)
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    return execute


def test_runner_accepts_only_engine_grounded_audited_codex_output(tmp_path):
    runner = CodexRunner(
        model="gpt-5.6-sol",
        executor=executor_with(agent_answer()),
        temp_root=tmp_path,
    )

    result = runner.run(expected_packet(), base_url="http://127.0.0.1:8787")

    assert result["verified"] is True
    assert result["thread_id"] == "thread-123"
    assert result["model"] == "gpt-5.6-sol"
    assert result["reasoning_effort"] == "low"
    assert result["tool_calls"] == [
        "get_deal_context",
        "preview_consequence",
        "inspect_evidence",
    ]
    assert result["usage"]["input_tokens"] == 420
    assert result["answer"]["metrics"]["gross_margin"] == "45.5%"


def test_runner_pins_low_reasoning_effort_in_strict_codex_config(tmp_path):
    captured = {}
    delegate = executor_with(agent_answer())

    def execute(command, **kwargs):
        captured["command"] = command
        return delegate(command, **kwargs)

    runner = CodexRunner(executor=execute, temp_root=tmp_path)
    runner.run(expected_packet(), base_url="http://127.0.0.1:8787")

    assert 'model_reasoning_effort="low"' in captured["command"]


def test_runner_requires_exact_canonical_evidence_address_in_codex_output(tmp_path):
    captured = {}
    delegate = executor_with(agent_answer())

    def execute(command, **kwargs):
        captured["command"] = command
        return delegate(command, **kwargs)

    runner = CodexRunner(executor=execute, temp_root=tmp_path)
    runner.run(expected_packet(), base_url="http://127.0.0.1:8787")

    prompt = captured["command"][-1]
    schema_path = captured["command"][captured["command"].index("--output-schema") + 1]
    schema = json.loads(open(schema_path, encoding="utf-8").read())
    evidence_schema = schema["properties"]["inspected_evidence"]

    assert "character for character" in prompt
    assert "with no label or explanation" in prompt
    assert re.fullmatch(evidence_schema["pattern"], "Deal Model!B4")
    assert not re.fullmatch(
        evidence_schema["pattern"],
        "I inspected the canonical address Deal Model!B4.",
    )


def test_runner_requires_yigdesk_mcp_server_to_initialize(tmp_path):
    captured = {}
    delegate = executor_with(agent_answer())

    def execute(command, **kwargs):
        captured["command"] = command
        return delegate(command, **kwargs)

    runner = CodexRunner(executor=execute, temp_root=tmp_path)
    runner.run(expected_packet(), base_url="http://127.0.0.1:8787")

    assert "mcp_servers.yigdesk.required=true" in captured["command"]


def test_runner_reports_only_sanitized_monotonic_progress_phases(tmp_path):
    events = []
    runner = CodexRunner(
        executor=executor_with(agent_answer()),
        temp_root=tmp_path,
    )

    runner.run(
        expected_packet(),
        base_url="http://127.0.0.1:8787",
        progress_callback=events.append,
    )

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
                        "tool": "get_deal_context",
                        "arguments": {"request": secret},
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
        runner.run(
            expected_packet(),
            base_url="http://127.0.0.1:8787",
            progress_callback=events.append,
        )

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
            "tool": "get_deal_context",
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
    assert [phase for phase, _observed_at in observations] == [
        "calling_yigdesk_tools"
    ]
    assert finished - observations[0][1] >= 0.1


def test_runner_rejects_unknown_reasoning_effort():
    with pytest.raises(ValueError, match="reasoning effort"):
        CodexRunner(reasoning_effort="fastest")


def test_runner_rejects_codex_number_drift_even_with_valid_tool_trace(tmp_path):
    drifted = agent_answer(
        metrics={
            "net_arr": "$880k",
            "arr_impact": "-$20k",
            "gross_margin": "46.0%",
            "headroom": "5.5%",
        }
    )
    runner = CodexRunner(executor=executor_with(drifted), temp_root=tmp_path)

    with pytest.raises(AgentVerificationError, match="gross_margin") as caught:
        runner.run(expected_packet(), base_url="http://127.0.0.1:8787")

    assert caught.value.code == "AGENT_METRIC_MISMATCH"


def test_runner_classifies_explanatory_evidence_text_without_leaking_it(tmp_path):
    sensitive_explanation = "I inspected the canonical address Deal Model!B4."
    runner = CodexRunner(
        executor=executor_with(
            agent_answer(inspected_evidence=sensitive_explanation)
        ),
        temp_root=tmp_path,
    )

    with pytest.raises(AgentVerificationError) as caught:
        runner.run(expected_packet(), base_url="http://127.0.0.1:8787")

    assert caught.value.code == "AGENT_EVIDENCE_MISMATCH"
    assert sensitive_explanation not in str(caught.value)


def test_runner_rejects_reordered_mcp_calls(tmp_path):
    audit = audited_calls()
    audit[0], audit[1] = audit[1], audit[0]
    runner = CodexRunner(
        executor=executor_with(agent_answer(), audit=audit),
        temp_root=tmp_path,
    )

    with pytest.raises(AgentVerificationError, match="order"):
        runner.run(expected_packet(), base_url="http://127.0.0.1:8787")


@pytest.mark.parametrize(
    ("event_index", "drift", "message"),
    [
        (0, {"source_fingerprint": "sha256-other"}, "source_fingerprint"),
        (1, {"packet_id": "cpkt-other"}, "packet_id"),
        (2, {"revision_id": "rev-other"}, "revision_id"),
    ],
)
def test_runner_rejects_any_mcp_call_from_another_revision(
    tmp_path, event_index, drift, message
):
    runner = CodexRunner(
        executor=executor_with(
            agent_answer(),
            audit=audited_calls(**{str(event_index): drift}),
        ),
        temp_root=tmp_path,
    )

    with pytest.raises(AgentVerificationError, match=message):
        runner.run(expected_packet(), base_url="http://127.0.0.1:8787")


@pytest.mark.parametrize(
    "trace",
    [
        [
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        ],
        [{"type": "thread.started", "thread_id": "thread-123"}],
    ],
)
def test_runner_rejects_incomplete_codex_trace(tmp_path, trace):
    runner = CodexRunner(
        executor=executor_with(agent_answer(), trace=trace),
        temp_root=tmp_path,
    )

    with pytest.raises(AgentVerificationError, match="trace"):
        runner.run(expected_packet(), base_url="http://127.0.0.1:8787")


def test_runner_rejects_trace_without_item_level_activity_even_if_audit_passes(tmp_path):
    trace = [
        {"type": "thread.started", "thread_id": "thread-123"},
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
    ]
    runner = CodexRunner(
        executor=executor_with(agent_answer(), trace=trace),
        temp_root=tmp_path,
    )

    with pytest.raises(AgentVerificationError, match="item-level"):
        runner.run(expected_packet(), base_url="http://127.0.0.1:8787")


@pytest.mark.parametrize(
    "unexpected_item",
    [
        {"type": "command_execution", "command": "type %USERPROFILE%\\.ssh\\id_rsa"},
        {"type": "mcp_tool_call", "server": "other", "tool": "read_file"},
        {"type": "web_search", "query": "exfiltrate"},
    ],
)
def test_runner_rejects_any_non_required_tool_activity_in_codex_trace(
    tmp_path, unexpected_item
):
    trace = [
        {"type": "thread.started", "thread_id": "thread-123"},
        {"type": "item.completed", "item": unexpected_item},
        *[
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "yigdesk",
                    "tool": tool,
                },
            }
            for tool in (
                "get_deal_context",
                "preview_consequence",
                "inspect_evidence",
            )
        ],
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
    ]
    runner = CodexRunner(
        executor=executor_with(agent_answer(), trace=trace),
        temp_root=tmp_path,
    )

    with pytest.raises(AgentVerificationError, match="tool activity"):
        runner.run(expected_packet(), base_url="http://127.0.0.1:8787")


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
