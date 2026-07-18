from __future__ import annotations

import json
import subprocess

import pytest

from yigdesk.benchmark import (
    BareCodexRunner,
    load_case,
    observability_profile,
    run_comparison,
    score_answer,
    scenario_from_case,
)


def private_case():
    return {
        "case_id": "private-deal-001",
        "request": {
            "requester": "A. Example",
            "requester_role": "Account Executive",
            "recipient": "B. Reviewer",
            "sent_at": "2026-07-18T09:00:00Z",
            "subject": "Renewal terms review",
            "message": "Please assess the requested terms.",
        },
        "inputs": {
            "list_arr_k": "1000",
            "current_discount_pct": "10",
            "requested_discount_pct": "12",
            "cogs_k": "480",
            "margin_floor_pct": "40",
        },
        "gold": {
            "verdict": "READY FOR CFO",
            "metrics": {
                "net_arr": "$880k",
                "arr_impact": "-$20k",
                "gross_margin": "45.5%",
                "headroom": "5.5%",
            },
        },
    }


def test_private_case_loader_keeps_source_data_local_and_builds_demo_scenario(tmp_path):
    path = tmp_path / "case.json"
    path.write_text(json.dumps(private_case()), encoding="utf-8")

    case = load_case(path)
    scenario = scenario_from_case(case)

    assert case["case_id"] == "private-deal-001"
    assert scenario["subject"] == "Renewal terms review"
    assert scenario["requested_discount_pct"] == "12"
    assert scenario["cogs_k"] == "480"


def test_benchmark_scores_each_decision_field_against_independent_gold():
    answer = {
        "verdict": "READY FOR CFO",
        "metrics": {
            "net_arr": "$880k",
            "arr_impact": "-$20k",
            "gross_margin": "46.0%",
            "headroom": "5.5%",
        },
    }

    score = score_answer(answer, private_case()["gold"])

    assert score["correct_fields"] == 4
    assert score["total_fields"] == 5
    assert score["accuracy"] == 0.8
    assert score["exact_match"] is False
    assert score["field_matches"]["gross_margin"] is False


def test_benchmark_normalizes_decimal_values_and_units_without_hiding_format_drift():
    answer = {
        "verdict": "READY FOR CFO",
        "metrics": {
            "net_arr": "$880.0k",
            "arr_impact": "USD -20.00 thousand",
            "gross_margin": "45.50 percent",
            "headroom": "5.50 percentage points",
        },
    }

    score = score_answer(answer, private_case()["gold"])

    assert score["accuracy"] == 1.0
    assert score["exact_match"] is True
    assert score["format_consistency"] == 0.0
    assert not any(score["format_matches"].values())


def test_benchmark_accepts_mathematically_correct_high_precision_at_engine_display_resolution():
    answer = {
        "verdict": "READY FOR CFO",
        "metrics": {
            "net_arr": "$879.996k",
            "arr_impact": "-$19.996k",
            "gross_margin": "45.454545%",
            "headroom": "5.454545 percentage points",
        },
    }

    score = score_answer(answer, private_case()["gold"])

    assert score["accuracy"] == 1.0
    assert score["exact_match"] is True
    assert score["format_consistency"] == 0.0


def test_benchmark_rejects_value_outside_engine_display_resolution():
    answer = {
        "verdict": "READY FOR CFO",
        "metrics": {
            **private_case()["gold"]["metrics"],
            "gross_margin": "45.44%",
        },
    }

    score = score_answer(answer, private_case()["gold"])

    assert score["field_matches"]["gross_margin"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda gold: gold.update(verdict="APPROVE"), "verdict"),
        (lambda gold: gold["metrics"].update(gross_margin=45.5), "gross_margin.*string"),
        (lambda gold: gold["metrics"].update(net_arr="880"), "net_arr.*unit"),
    ],
)
def test_case_loader_validates_gold_types_verdict_and_units(tmp_path, mutation, message):
    case = private_case()
    mutation(case["gold"])
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_case(path)


def test_case_loader_rejects_missing_independent_gold(tmp_path):
    case = private_case()
    case.pop("gold")
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")

    with pytest.raises(ValueError, match="gold"):
        load_case(path)


def test_bare_runner_uses_same_model_in_isolated_workspace_without_yigdesk_mcp(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("UNRELATED_BENCHMARK_SECRET", "must-not-cross-boundary")
    case = private_case()
    case["request"]["message"] = "PRIVATE-REQUEST-SENTINEL"
    case["inputs"]["list_arr_k"] = "991.234"

    def execute(command, *, env, timeout, cwd, input):
        assert "--ignore-user-config" in command
        assert "--ignore-rules" in command
        assert "--strict-config" in command
        assert "--skip-git-repo-check" in command
        assert "--sandbox" not in command
        assert "features.shell_tool=false" in command
        assert "features.shell_snapshot=false" in command
        assert 'default_permissions="yigdesk_agent"' in command
        assert "gpt-5.6-sol" in command
        assert not any("mcp_servers.yigdesk" in item for item in command)
        assert "PRIVATE-REQUEST-SENTINEL" not in " ".join(command)
        assert "991.234" not in " ".join(command)
        assert "PRIVATE-REQUEST-SENTINEL" in input
        assert "991.234" in input
        assert "ROUND_HALF_UP" in input
        assert "0.01k" in input
        assert "0.1 percentage point" in input
        assert "ROUND_HALF_EVEN" in input
        assert "rounded gross margin" in input
        assert "UNRELATED_BENCHMARK_SECRET" not in env
        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "verdict": "READY FOR CFO",
                    "summary": "The request is ready for review.",
                    "metrics": private_case()["gold"]["metrics"],
                    "evidence_refs": ["list_arr_k", "requested_discount_pct", "cogs_k"],
                },
                handle,
            )
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "bare-thread"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "done"},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 40},
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    result = BareCodexRunner(
        model="gpt-5.6-sol",
        executor=execute,
        temp_root=tmp_path,
    ).run(case)

    assert result["thread_id"] == "bare-thread"
    assert result["answer"]["metrics"]["gross_margin"] == "45.5%"
    assert result["usage"]["input_tokens"] == 100


def test_observability_profile_distinguishes_model_trace_from_engine_audit():
    bare = observability_profile(
        {"thread_id": "bare", "model": "gpt-5.6-sol", "usage": {"input_tokens": 1}}
    )
    assisted = observability_profile(
        {
            "thread_id": "assisted",
            "model": "gpt-5.6-sol",
            "usage": {"input_tokens": 1},
            "tool_calls": ["get_deal_context", "preview_consequence", "inspect_evidence"],
            "verified": True,
        },
        packet={"packet_id": "cpkt-1", "source_fingerprint": "sha", "analysis_bytes_unchanged": True},
    )

    assert bare["score"] == 2
    assert assisted["score"] == 6
    assert assisted["signals"]["deterministic_packet"] is True


def test_comparison_report_omits_private_source_content(tmp_path):
    case = private_case()
    case["request"]["message"] = "SECRET-CUSTOMER-CONTENT"

    class Bare:
        model = "gpt-5.6-sol"

        def run(self, supplied_case):
            return {
                "thread_id": "bare",
                "model": "gpt-5.6-sol",
                "latency_ms": 20,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "answer": {
                    "verdict": case["gold"]["verdict"],
                    "metrics": case["gold"]["metrics"],
                },
            }

    class Assisted:
        model = "gpt-5.6-sol"

        def run(self, packet, *, base_url, revision_id=None):
            assert revision_id == packet["revision_id"]
            consequence = packet["consequence"]
            return {
                "thread_id": "assisted",
                "model": self.model,
                "latency_ms": 10,
                "usage": {"input_tokens": 8, "output_tokens": 3},
                "tool_calls": ["get_deal_context", "preview_consequence", "inspect_evidence"],
                "verified": True,
                "answer": {
                    "verdict": consequence["verdict"],
                    "metrics": {
                        key: consequence["display"][key]
                        for key in ("net_arr", "arr_impact", "gross_margin", "headroom")
                    },
                },
            }

    report = run_comparison(
        case,
        runs=1,
        output_dir=tmp_path / "results",
        bare_runner=Bare(),
        assisted_runner=Assisted(),
    )

    serialized = json.dumps(report)
    assert "SECRET-CUSTOMER-CONTENT" not in serialized
    assert report["aggregate"]["bare"]["accuracy_mean"] == 1.0
    assert report["aggregate"]["bare"]["failure_rate"] == 0.0
    assert report["aggregate"]["assisted"]["observability_score"] == 6
    assert report["engine_vs_gold"]["accuracy"] == 1.0
    assert report["scoring_contract"] == {
        "money_resolution_k": "1",
        "money_rounding": "ROUND_HALF_EVEN",
        "percentage_point_resolution": "0.1",
        "percentage_rounding": "ROUND_HALF_UP",
        "format_consistency": "literal string equality, reported separately",
    }
    assert (tmp_path / "results" / "comparison.json").is_file()
    assert (tmp_path / "results" / "comparison.md").is_file()


def test_comparison_records_one_trial_failure_and_continues(tmp_path):
    case = private_case()

    class Bare:
        model = "gpt-5.6-sol"
        calls = 0

        def run(self, supplied_case):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("private source must not enter report")
            return {
                "thread_id": "bare-ok",
                "model": self.model,
                "latency_ms": 20,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "answer": {"verdict": case["gold"]["verdict"], "metrics": case["gold"]["metrics"]},
            }

    class Assisted(Bare):
        def run(self, packet, *, base_url, revision_id=None):
            assert revision_id == packet["revision_id"]
            return {
                "thread_id": "assisted-ok",
                "model": self.model,
                "latency_ms": 10,
                "usage": {"input_tokens": 8, "output_tokens": 3},
                "tool_calls": ["get_deal_context", "preview_consequence", "inspect_evidence"],
                "answer": {"verdict": case["gold"]["verdict"], "metrics": case["gold"]["metrics"]},
            }

    report = run_comparison(
        case,
        runs=2,
        output_dir=tmp_path / "results",
        bare_runner=Bare(),
        assisted_runner=Assisted(),
    )

    assert [record["status"] for record in report["records"]["bare"]] == ["failure", "success"]
    assert report["aggregate"]["bare"]["failure_rate"] == 0.5
    assert report["aggregate"]["bare"]["successful_runs"] == 1
    assert report["aggregate"]["assisted"]["failure_rate"] == 0.0
    assert "private source must not enter report" not in json.dumps(report)
