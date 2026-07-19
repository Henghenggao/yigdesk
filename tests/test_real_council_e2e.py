from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from scripts.run_real_council_e2e import (
    build_codex_command,
    parse_args,
    read_candidate_result,
    run_council_process,
)
from test_a2a import complete_audit


def test_real_council_command_targets_the_project_agents_without_fast_or_nested_runner(
    tmp_path,
):
    root = tmp_path / "repo"
    runtime = root / "runtime" / "real-council"
    output = runtime / "last-message.txt"

    command = build_codex_command(
        ["codex"],
        root=root,
        runtime=runtime,
        base_url="http://127.0.0.1:8787",
        output_path=output,
        preflight={
            "revision_id": "revision-test",
            "source_fingerprint": "fingerprint-test",
            "packet_id": "packet-test",
            "requested_discount_pct": "2",
            "margin_floor_pct": "30",
            "current_discount_pct": "0",
        },
    )

    assert command[:2] == ["codex", "exec"]
    assert "--ignore-user-config" not in command
    assert "--ignore-rules" in command
    assert "--strict-config" in command
    assert command[command.index("--disable") + 1] == "fast_mode"
    assert 'model_reasoning_effort="none"' in command
    assert 'model_reasoning_summary="none"' in command
    assert 'model_verbosity="low"' in command
    assert command[command.index("--model") + 1] == "gpt-5.6-terra"
    assert "--output-schema" not in command
    assert not any("service_tier" in argument for argument in command)
    assert not any("mcp_servers.yigdesk" in argument for argument in command)
    assert not any(argument.startswith("projects.") for argument in command)
    prompt = command[-1]
    assert "$yigdesk-council" in prompt
    assert "current bound Yigdesk revision" in prompt
    assert 'fork_turns="none"' in prompt
    assert "do not start a nested codex exec" in prompt.lower()
    assert "Do not run shell commands or repeat CLI preflight" in prompt
    assert "revision-test" in prompt
    assert "fingerprint-test" in prompt
    assert "packet-test" in prompt
    assert "The harness verifies the audit after Codex exits" in prompt
    assert "Do not withhold the candidate decision" in prompt
    assert '"candidate_status":"READY_FOR_EXTERNAL_AUDIT"' in prompt
    assert "submitted, sales alternative, largest_safe_step_pct, and first_unsafe_pct" in prompt


def test_real_council_process_reports_observed_first_mcp_latency_and_verified_audit(
    tmp_path,
):
    audit_path = tmp_path / "codex-a2a-audit.jsonl"
    producer = tmp_path / "produce_audit.py"
    events_json = json.dumps(complete_audit())
    producer.write_text(
        "\n".join(
            (
                "import json, pathlib, sys, time",
                "audit = pathlib.Path(sys.argv[1])",
                f"events = json.loads({events_json!r})",
                "time.sleep(0.05)",
                "for event in events:",
                "    with audit.open('a', encoding='utf-8') as stream:",
                "        stream.write(json.dumps(event) + '\\n')",
                "    time.sleep(0.005)",
            )
        ),
        encoding="utf-8",
    )

    report = run_council_process(
        [sys.executable, str(producer), str(audit_path)],
        audit_path=audit_path,
        timeout_seconds=1,
    )

    assert report["status"] == "passed"
    assert report["verified"] is True
    assert report["accepted_call_count"] == 15
    assert 25 <= report["first_mcp_call_ms"] < 500
    assert set(report["role_first_call_ms"]) == {
        "finance_analyst",
        "sales_advocate",
        "risk_challenger",
        "decision_optimizer",
    }
    assert report["elapsed_ms"] < report["budget_ms"] == 1000


def test_real_council_cli_requires_data_sharing_ack_and_never_exceeds_120_seconds():
    with pytest.raises(SystemExit):
        parse_args([])
    with pytest.raises(SystemExit):
        parse_args(["--acknowledge-data-sharing", "--timeout-seconds", "121"])

    args = parse_args(
        ["--acknowledge-data-sharing", "--timeout-seconds", "120"]
    )

    assert args.acknowledge_data_sharing is True
    assert args.timeout_seconds == 120


def test_failed_council_keeps_raw_startup_diagnostics_out_of_the_json_report(
    tmp_path,
):
    diagnostic_path = tmp_path / "codex.stderr.log"
    event_path = tmp_path / "codex.events.jsonl"
    secret = "synthetic-sensitive-diagnostic"

    report = run_council_process(
        [
            sys.executable,
            "-c",
            (
                "import os, sys; "
                "sys.stdout.write('local-event-diagnostic\\n'); "
                f"sys.stderr.write({secret!r} + ':' + os.environ['YIGDESK_RUNTIME']); "
                "raise SystemExit(7)"
            ),
        ],
        audit_path=tmp_path / "missing-audit.jsonl",
        timeout_seconds=1,
        diagnostic_path=diagnostic_path,
        environment={**os.environ, "YIGDESK_RUNTIME": "runtime/from-parent"},
    )

    assert report["status"] == "failed"
    assert report["error"] == {"code": "COUNCIL_PROCESS_FAILED"}
    assert secret not in json.dumps(report)
    assert secret in diagnostic_path.read_text(encoding="utf-8")
    assert "runtime/from-parent" in diagnostic_path.read_text(encoding="utf-8")
    assert "local-event-diagnostic" in event_path.read_text(encoding="utf-8")


def test_candidate_result_is_a_compact_identity_bound_handoff(tmp_path):
    output = tmp_path / "candidate.json"
    expected = {
        "revision_id": "revision-1",
        "source_fingerprint": "fingerprint-1",
        "packet_id": "packet-1",
    }
    candidate = {
        **expected,
        "candidate_status": "READY_FOR_EXTERNAL_AUDIT",
        "submitted_discount_pct": "2.0",
        "financially_feasible": True,
        "exact_max_discount_pct": "2.239020",
        "largest_safe_step_pct": "2.23",
        "first_unsafe_pct": "2.24",
        "recommended_discount_pct": None,
        "commercial_optimality_proven": False,
        "decision": "Base case is feasible; +5% COGS remains a terminal risk.",
    }
    output.write_text(json.dumps(candidate), encoding="utf-8")

    expected_values = {
        "submitted_discount_pct": "2.0",
        "financially_feasible": True,
        "exact_max_discount_pct": "2.239020",
        "largest_safe_step_pct": "2.23",
        "first_unsafe_pct": "2.24",
    }
    assert read_candidate_result(
        output,
        expected_revision=expected,
        expected_values=expected_values,
    ) == candidate

    output.write_text(
        json.dumps({**candidate, "revision_id": "drifted"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="identity"):
        read_candidate_result(output, expected_revision=expected)

    output.write_text(json.dumps({key: value for key, value in candidate.items() if key != "decision"}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        read_candidate_result(output, expected_revision=expected)

    output.write_text(
        json.dumps({**candidate, "largest_safe_step_pct": "2.24"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="grounded value"):
        read_candidate_result(
            output,
            expected_revision=expected,
            expected_values=expected_values,
        )


def test_candidate_event_ends_the_run_without_waiting_for_cli_cleanup(tmp_path):
    audit_path = tmp_path / "codex-a2a-audit.jsonl"
    candidate_path = tmp_path / "candidate.json"
    producer = tmp_path / "produce_candidate_then_sleep.py"
    events_json = json.dumps(complete_audit())
    candidate = {
        "candidate_status": "READY_FOR_EXTERNAL_AUDIT",
        "revision_id": "revision-test",
        "source_fingerprint": "fingerprint-test",
        "packet_id": "packet-test",
        "submitted_discount_pct": "2",
        "financially_feasible": True,
        "exact_max_discount_pct": "2.239020",
        "largest_safe_step_pct": "2.23",
        "first_unsafe_pct": "2.24",
        "recommended_discount_pct": None,
        "commercial_optimality_proven": False,
        "decision": "Grounded candidate is ready for the external audit.",
    }
    codex_event = {
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": json.dumps(candidate),
        },
    }
    producer.write_text(
        "\n".join(
            (
                "import json, pathlib, sys, time",
                "audit = pathlib.Path(sys.argv[1])",
                f"events = json.loads({events_json!r})",
                "audit.write_text(''.join(json.dumps(event) + '\\n' for event in events), encoding='utf-8')",
                f"print(json.dumps({codex_event!r}), flush=True)",
                "time.sleep(2)",
            )
        ),
        encoding="utf-8",
    )

    report = run_council_process(
        [sys.executable, str(producer), str(audit_path)],
        audit_path=audit_path,
        candidate_output_path=candidate_path,
        timeout_seconds=1,
    )

    assert report["status"] == "passed"
    assert report["completion"] == "candidate_event"
    assert report["decision_ready_ms"] < report["budget_ms"] == 1000
    assert json.loads(candidate_path.read_text(encoding="utf-8")) == candidate
