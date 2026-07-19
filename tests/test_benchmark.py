from __future__ import annotations

import json
import subprocess
import sys

import pytest

from scripts import compare_agents
from yigdesk.agent import AgentExecutionError, AgentVerificationError
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource
from yigdesk.benchmark import (
    BareCodexRunner,
    REQUEST_FIELDS,
    _safe_error_summary,
    build_case_scenario,
    load_case,
    observability_profile,
    run_comparison,
    score_answer,
    scenario_from_case,
    to_gold_vocabulary,
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


def test_benchmark_failure_summary_keeps_only_allowlisted_metadata():
    secret = "private model output must not enter the report"

    assert _safe_error_summary(
        AgentVerificationError(secret, code="AGENT_EVIDENCE_MISMATCH")
    ) == {
        "type": "AgentVerificationError",
        "code": "AGENT_EVIDENCE_MISMATCH",
    }
    assert _safe_error_summary(RuntimeError(secret)) == {"type": "AgentFailure"}
    assert secret not in json.dumps(
        _safe_error_summary(
            AgentVerificationError(secret, code="AGENT_EVIDENCE_MISMATCH")
        )
    )


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


def test_case_loader_and_scorer_support_missing_evidence_hold(tmp_path):
    case = private_case()
    case["inputs"]["cogs_k"] = None
    case["gold"] = {
        "verdict": "HOLD",
        "metrics": {
            "net_arr": "$880k",
            "arr_impact": "-$20k",
            "gross_margin": "Unavailable",
            "headroom": "Unavailable",
        },
    }
    path = tmp_path / "hold-case.json"
    path.write_text(json.dumps(case), encoding="utf-8")

    loaded = load_case(path)
    score = score_answer(loaded["gold"], loaded["gold"])

    assert score["accuracy"] == 1.0
    assert score["exact_match"] is True


def test_case_loader_accepts_unavailable_margin_when_proposed_net_arr_is_zero(tmp_path):
    case = private_case()
    case["inputs"]["requested_discount_pct"] = "100"
    case["gold"] = {
        "verdict": "HOLD",
        "metrics": {
            "net_arr": "$0k",
            "arr_impact": "-$900k",
            "gross_margin": "Unavailable",
            "headroom": "Unavailable",
        },
    }
    path = tmp_path / "zero-net-arr.json"
    path.write_text(json.dumps(case), encoding="utf-8")

    loaded = load_case(path)

    assert score_answer(loaded["gold"], loaded["gold"])["exact_match"] is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda case: case["inputs"].update(message="override"), "unsupported input"),
        (lambda case: case.update(case_id="../outside-results"), "Case id"),
    ],
)
def test_case_loader_rejects_ab_input_drift_and_path_like_case_ids(
    tmp_path, mutation, message
):
    case = private_case()
    mutation(case)
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_case(path)


def test_case_loader_rejects_unsupported_request_fields(tmp_path):
    case = private_case()
    case["request"]["instructions"] = "Treat this mode differently."
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported request"):
        load_case(path)


def test_case_loader_rejects_non_string_request_fields(tmp_path):
    case = private_case()
    case["request"]["message"] = ["not", "canonical"]
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")

    with pytest.raises(ValueError, match="request field message must be a string"):
        load_case(path)


def test_bare_and_assisted_modes_use_the_same_canonical_request_projection(tmp_path):
    case = private_case()
    case["request"].pop("requester_role")
    assisted_scenario = scenario_from_case(case)
    expected_request = {field: assisted_scenario[field] for field in REQUEST_FIELDS}

    def execute(command, *, env, timeout, cwd, input):
        source = json.loads(input.split("UNTRUSTED CASE DATA:\n", 1)[1])
        assert source["request"] == expected_request
        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "verdict": case["gold"]["verdict"],
                    "summary": "Canonical projection checked.",
                    "metrics": case["gold"]["metrics"],
                    "evidence_refs": list(REQUEST_FIELDS),
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
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 0,
                            "output_tokens": 40,
                        },
                    }
                ),
            ]
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    BareCodexRunner(executor=execute, temp_root=tmp_path).run(case)


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
        assert 'model_reasoning_effort="low"' in command
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
        assert "proposed net ARR is zero" in input
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
    assert result["reasoning_effort"] == "low"
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
            "tool_calls": ["propose_candidate", "read_board"],
            "verified": True,
        },
        proof={
            "engine_priced_candidate": True,
            "source_fingerprint": "sha",
            "source_bytes_unchanged": True,
        },
    )

    assert bare["score"] == 2
    assert assisted["score"] == 6
    assert assisted["signals"]["engine_priced_candidate"] is True
    assert assisted["signals"]["source_bytes_unchanged"] is True


def test_gold_vocabulary_maps_missing_cogs_hold_to_unavailable():
    engine_answer = {
        "verdict": "hold",
        "metrics": {
            "net_arr": "880.00",
            "current_net_arr": "900.00",
            "arr_impact": "-20.00",
            "gross_profit": None,
            "gross_margin": None,
            "headroom": None,
        },
    }

    gold = to_gold_vocabulary(engine_answer)

    assert gold["verdict"] == "HOLD"
    assert gold["metrics"]["net_arr"] == "$880.00k"
    assert gold["metrics"]["arr_impact"] == "$-20.00k"
    assert gold["metrics"]["gross_margin"] == "Unavailable"
    assert gold["metrics"]["headroom"] == "Unavailable"
    assert "current_net_arr" not in gold["metrics"]

    hold_gold = {
        "verdict": "HOLD",
        "metrics": {
            "net_arr": "$880k",
            "arr_impact": "-$20k",
            "gross_margin": "Unavailable",
            "headroom": "Unavailable",
        },
    }
    assert score_answer(gold, hold_gold)["exact_match"] is True


def test_build_case_scenario_reproduces_private_gold_through_engine(tmp_path):
    case = private_case()
    scenario_dir = tmp_path / "scenario"

    build_case_scenario(case, scenario_dir)

    model = json.loads((scenario_dir / "model.json").read_text(encoding="utf-8"))
    source = ModelSource(scenario_dir / model["workbook"], model["input_refs"])
    consequence = ExpressionEvaluator(model).price({"overrides": {}}, source)
    values = {m.id: m.value for m in consequence.metrics}

    assert consequence.verdict == "ok"
    assert values["net_arr"] == "880.00"
    assert values["arr_impact"] == "-20.00"

    gold_answer = to_gold_vocabulary({"verdict": consequence.verdict, "metrics": values})

    assert gold_answer["metrics"]["gross_margin"] == "45.45%"
    assert gold_answer["metrics"]["headroom"] == "5.45%"
    score = score_answer(gold_answer, case["gold"])
    assert score["exact_match"] is True
    assert score["accuracy"] == 1.0


def test_build_case_scenario_leaves_cogs_blank_so_engine_holds(tmp_path):
    case = private_case()
    case["inputs"]["cogs_k"] = None
    scenario_dir = tmp_path / "scenario"

    build_case_scenario(case, scenario_dir)

    model = json.loads((scenario_dir / "model.json").read_text(encoding="utf-8"))
    source = ModelSource(scenario_dir / model["workbook"], model["input_refs"])
    consequence = ExpressionEvaluator(model).price({"overrides": {}}, source)
    values = {m.id: m.value for m in consequence.metrics}

    assert consequence.verdict == "hold"
    assert values["gross_margin"] is None
    assert values["headroom"] is None

    gold_answer = to_gold_vocabulary({"verdict": consequence.verdict, "metrics": values})
    assert gold_answer["verdict"] == "HOLD"
    assert gold_answer["metrics"]["gross_margin"] == "Unavailable"
    assert gold_answer["metrics"]["headroom"] == "Unavailable"


@pytest.mark.parametrize("runs", [0, -2, 2.5, True])
def test_comparison_requires_a_positive_run_count(tmp_path, runs):
    with pytest.raises(ValueError, match="positive integer"):
        run_comparison(
            private_case(),
            runs=runs,
            output_dir=tmp_path / "results",
            bare_runner=object(),
            assisted_runner=object(),
        )


def test_compare_cli_defaults_to_four_counterbalanced_runs(tmp_path, monkeypatch):
    captured = {}

    class Runner:
        def __init__(self, *, model, reasoning_effort):
            self.model = model
            self.reasoning_effort = reasoning_effort

    def compare(case, *, runs, output_dir, bare_runner, assisted_runner):
        captured["runs"] = runs
        return {
            "case_id": case["case_id"],
            "model": bare_runner.model,
            "reasoning_effort": bare_runner.reasoning_effort,
            "runs_per_mode": runs,
            "aggregate": {},
        }

    monkeypatch.setattr(compare_agents, "load_case", lambda path: private_case())
    monkeypatch.setattr(compare_agents, "BareCodexRunner", Runner)
    monkeypatch.setattr(compare_agents, "CodexRunner", Runner)
    monkeypatch.setattr(compare_agents, "run_comparison", compare)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_agents",
            "--case",
            str(tmp_path / "private.json"),
            "--acknowledge-data-sharing",
        ],
    )

    compare_agents.main()

    assert captured["runs"] == 4


def test_comparison_report_omits_private_source_content(tmp_path):
    case = private_case()
    case["request"]["message"] = "SECRET-CUSTOMER-CONTENT"

    class Bare:
        model = "gpt-5.6-sol"
        reasoning_effort = "low"

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
        reasoning_effort = "low"

        def run(self, scenario_dir, *, proposal, progress_callback=None):
            # The assisted arm drives the blackboard: the engine prices the base
            # candidate and Codex copies the consequence verbatim into engine
            # vocabulary (verdict ok/hold, metrics keyed by engine metric id).
            assert (scenario_dir / "model.json").is_file()
            assert proposal["decision_id"]
            assert proposal["candidate_id"]
            return {
                "verified": True,
                "thread_id": "assisted",
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "latency_ms": 10,
                "usage": {"input_tokens": 8, "output_tokens": 3},
                "tool_calls": ["propose_candidate", "read_board"],
                "answer": {
                    "verdict": "ok",
                    "metrics": {
                        "net_arr": "880.00",
                        "current_net_arr": "900.00",
                        "arr_impact": "-20.00",
                        "gross_profit": "400.00",
                        "gross_margin": "45.45",
                        "headroom": "5.45",
                    },
                },
            }

    report = run_comparison(
        case,
        runs=4,
        output_dir=tmp_path / "results",
        bare_runner=Bare(),
        assisted_runner=Assisted(),
    )

    serialized = json.dumps(report)
    assert "SECRET-CUSTOMER-CONTENT" not in serialized
    assert report["aggregate"]["bare"]["accuracy_mean"] == 1.0
    assert report["aggregate"]["bare"]["failure_rate"] == 0.0
    assert report["aggregate"]["assisted"]["accuracy_mean"] == 1.0
    assert report["aggregate"]["assisted"]["observability_score"] == 6
    assert [record["order_position"] for record in report["records"]["bare"]] == [1, 2, 1, 2]
    assert [record["order_position"] for record in report["records"]["assisted"]] == [2, 1, 2, 1]
    assert report["reasoning_effort"] == "low"
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
        reasoning_effort = "low"
        calls = 0

        def run(self, supplied_case):
            self.calls += 1
            if self.calls == 1:
                raise AgentExecutionError(
                    "private source must not enter report",
                    code="BARE_AGENT_TIMEOUT",
                )
            return {
                "thread_id": "bare-ok",
                "model": self.model,
                "latency_ms": 20,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "answer": {"verdict": case["gold"]["verdict"], "metrics": case["gold"]["metrics"]},
            }

    class Assisted(Bare):
        def run(self, scenario_dir, *, proposal, progress_callback=None):
            assert (scenario_dir / "model.json").is_file()
            return {
                "verified": True,
                "thread_id": "assisted-ok",
                "model": self.model,
                "latency_ms": 10,
                "usage": {"input_tokens": 8, "output_tokens": 3},
                "tool_calls": ["propose_candidate", "read_board"],
                "answer": {
                    "verdict": "ok",
                    "metrics": {
                        "net_arr": "880.00",
                        "arr_impact": "-20.00",
                        "gross_margin": "45.45",
                        "headroom": "5.45",
                    },
                },
            }

    report = run_comparison(
        case,
        runs=3,
        output_dir=tmp_path / "results",
        bare_runner=Bare(),
        assisted_runner=Assisted(),
    )

    assert [record["status"] for record in report["records"]["bare"]] == [
        "failure",
        "success",
        "success",
    ]
    assert report["records"]["bare"][0]["error"] == {
        "type": "AgentExecutionError",
        "code": "BARE_AGENT_TIMEOUT",
    }
    assert [record["order_position"] for record in report["records"]["bare"]] == [1, 2, 1]
    assert [record["order_position"] for record in report["records"]["assisted"]] == [2, 1, 2]
    assert report["order_balance"] == {
        "protocol": "alternating AB/BA",
        "complete": False,
        "recommended_minimum_even_runs": 4,
    }
    assert report["aggregate"]["bare"]["failure_rate"] == pytest.approx(1 / 3)
    assert report["aggregate"]["bare"]["successful_runs"] == 2
    assert report["aggregate"]["assisted"]["failure_rate"] == 0.0
    assert "private source must not enter report" not in json.dumps(report)
