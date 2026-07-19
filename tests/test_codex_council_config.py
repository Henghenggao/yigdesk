from __future__ import annotations

import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AGENT_NAMES = (
    "finance_analyst",
    "sales_advocate",
    "risk_challenger",
    "decision_optimizer",
)
ROLE_TOOLS = {
    "finance_analyst": {
        "get_deal_context",
        "find_feasible_boundary",
        "evaluate_proposal",
        "inspect_evidence",
    },
    "sales_advocate": {"get_deal_context", "evaluate_proposal"},
    "risk_challenger": {
        "get_deal_context",
        "list_missing_evidence",
        "find_feasible_boundary",
        "stress_test_assumption",
        "inspect_evidence",
    },
    "decision_optimizer": {
        "get_deal_context",
        "compare_proposals",
        "inspect_evidence",
    },
}


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_council_agents_pin_low_latency_reasoning_on_the_real_work_path(agent_name):
    config = tomllib.loads(
        (ROOT / ".codex" / "agents" / f"{agent_name}.toml").read_text(
            encoding="utf-8"
        )
    )

    assert config["model_reasoning_effort"] == "none"
    assert config["model"] == "gpt-5.6-terra"
    assert config["model_reasoning_summary"] == "none"
    assert config["model_verbosity"] == "low"
    assert "service_tier" not in config
    instructions = config["developer_instructions"]
    assert "Make get_deal_context your first action" in instructions
    assert "Do not emit a preamble or plan" in instructions
    assert "Do not inspect files or call shell tools before it" in instructions
    assert (
        "After the final required MCP call, return immediately with one compact JSON object"
        in instructions
    )
    mcp = config["mcp_servers"]["yigdesk"]
    assert mcp["required"] is True
    assert set(mcp["enabled_tools"]) == ROLE_TOOLS[agent_name]
    assert set(mcp["env_vars"]) == {"YIGDESK_URL", "YIGDESK_RUNTIME"}


def test_project_council_explicitly_disables_fast_mode():
    config = tomllib.loads(
        (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
    )

    assert config["features"]["fast_mode"] is False
    assert "service_tier" not in config
    mcp = config["mcp_servers"]["yigdesk"]
    assert set(mcp["env_vars"]) == {"YIGDESK_URL", "YIGDESK_RUNTIME"}
    assert "env" not in mcp


def test_council_skill_spawns_bounded_agents_without_parent_conversation_history():
    skill = (
        ROOT / ".agents" / "skills" / "yigdesk-council" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert 'fork_turns="none"' in skill
    assert "Do not copy the parent conversation into a specialist" in skill
    assert "first action must be its first required Yigdesk MCP call" in skill
    assert "revision id, source fingerprint, packet id" in skill
    assert "real council E2E harness supplies a verified preflight" in skill
    assert "do not repeat shell or CLI preflight" in skill
    assert "timeout_ms of at least 10000" in skill
    assert "harness verifies the audit after Codex exits" in skill


def test_risk_agent_pins_named_stress_arguments_and_schema_retry():
    config = tomllib.loads(
        (ROOT / ".codex" / "agents" / "risk_challenger.toml").read_text(
            encoding="utf-8"
        )
    )
    instructions = config["developer_instructions"]

    assert (
        '{"requested_discount_pct": 2, "cogs_change_pct": 5, '
        '"actor": "risk_challenger"}'
    ) in instructions
    assert "retry it once with those exact named arguments" in instructions


def test_role_handoffs_pin_every_optimizer_proposal_field():
    instructions = {
        name: tomllib.loads(
            (ROOT / ".codex" / "agents" / f"{name}.toml").read_text(
                encoding="utf-8"
            )
        )["developer_instructions"]
        for name in AGENT_NAMES
    }

    assert "largest_safe_step_pct" in instructions["finance_analyst"]
    assert "first_unsafe_pct" in instructions["finance_analyst"]
    assert "alternative_discount_pct" in instructions["sales_advocate"]
    assert "largest_safe_step_pct" in instructions["risk_challenger"]
    assert "first_unsafe_pct" in instructions["risk_challenger"]
    assert "submitted, sales alternative, largest_safe_step_pct, and first_unsafe_pct" in instructions["decision_optimizer"]

    skill = (
        ROOT / ".agents" / "skills" / "yigdesk-council" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "exactly the submitted, sales alternative, largest safe step, and first unsafe" in skill
