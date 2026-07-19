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

# The council speaks the six blackboard ops and nothing else. Each role gets
# exactly the ops it needs; open_decision / cast_approval / request_resolve stay
# with the orchestrator so only the deterministic gate can close a decision.
ROLE_TOOLS = {
    "finance_analyst": {"read_board", "propose_candidate"},
    "sales_advocate": {"read_board", "propose_candidate"},
    "risk_challenger": {"read_board", "propose_candidate", "post_claim"},
    "decision_optimizer": {"read_board", "post_claim"},
}

# The retired read-only 8-tool surface must not survive anywhere in a persona.
RETIRED_TOOLS = (
    "get_deal_context",
    "find_feasible_boundary",
    "evaluate_proposal",
    "inspect_evidence",
    "list_missing_evidence",
    "stress_test_assumption",
    "compare_proposals",
)


def _agent(name):
    return tomllib.loads(
        (ROOT / ".codex" / "agents" / f"{name}.toml").read_text(encoding="utf-8")
    )


def _skill():
    return (
        ROOT / ".agents" / "skills" / "yigdesk-council" / "SKILL.md"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_council_agents_pin_low_latency_reasoning_on_the_real_work_path(agent_name):
    config = _agent(agent_name)

    assert config["model_reasoning_effort"] == "none"
    assert config["model"] == "gpt-5.6-terra"
    assert config["model_reasoning_summary"] == "none"
    assert config["model_verbosity"] == "low"
    assert config["sandbox_mode"] == "read-only"
    assert "service_tier" not in config

    instructions = config["developer_instructions"]
    # Latency / bounded-context discipline is orthogonal to the surface and stays.
    assert "Make read_board your first action" in instructions
    assert "Do not emit a preamble or plan" in instructions
    assert "Do not inspect files or call shell tools before it" in instructions
    assert (
        "After the final required MCP call, return immediately with one compact JSON object"
        in instructions
    )
    # The new tool signatures set actor internally; no persona passes it.
    assert "actor=" not in instructions
    for retired in RETIRED_TOOLS:
        assert retired not in instructions

    mcp = config["mcp_servers"]["yigdesk"]
    assert mcp["command"] == "python"
    assert mcp["args"] == ["-m", "yigdesk.blackboard_mcp"]
    assert mcp["required"] is True
    assert set(mcp["enabled_tools"]) == ROLE_TOOLS[agent_name]
    assert set(mcp["env_vars"]) == {"YIGDESK_SCENARIO", "YIGDESK_LEDGER"}


def test_project_council_explicitly_disables_fast_mode():
    config = tomllib.loads(
        (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
    )

    assert config["features"]["fast_mode"] is False
    assert "service_tier" not in config
    mcp = config["mcp_servers"]["yigdesk"]
    assert mcp["args"] == ["-m", "yigdesk.blackboard_mcp"]
    assert set(mcp["env_vars"]) == {"YIGDESK_SCENARIO", "YIGDESK_LEDGER"}
    assert "env" not in mcp


def test_roles_split_the_six_ops_so_only_the_gate_closes_a_decision():
    tools = {
        name: set(_agent(name)["mcp_servers"]["yigdesk"]["enabled_tools"])
        for name in AGENT_NAMES
    }

    # Finance and sales price candidates.
    assert "propose_candidate" in tools["finance_analyst"]
    assert "propose_candidate" in tools["sales_advocate"]
    # Risk proposes a boundary candidate AND grounds a claim.
    assert "propose_candidate" in tools["risk_challenger"]
    assert "post_claim" in tools["risk_challenger"]
    # The optimizer only advises: read the board and post a claim. It never
    # proposes and it never resolves.
    assert tools["decision_optimizer"] == {"read_board", "post_claim"}
    # No role may open, approve, or resolve — those stay with the orchestrator.
    for name in AGENT_NAMES:
        assert "open_decision" not in tools[name]
        assert "cast_approval" not in tools[name]
        assert "request_resolve" not in tools[name]


def test_council_skill_spawns_bounded_agents_over_the_six_op_flow():
    skill = _skill()

    # Council latency contract (preserved, surface-orthogonal).
    assert 'fork_turns="none"' in skill
    assert "Do not copy the parent conversation into a specialist" in skill
    assert "first action must be its first required Yigdesk MCP call" in skill
    assert "timeout_ms of at least 10000" in skill

    # The six-op orchestration is spelled out end to end.
    for op in (
        "open_decision",
        "propose_candidate",
        "post_claim",
        "cast_approval",
        "request_resolve",
        "read_board",
    ):
        assert op in skill

    # Blackboard-native audit story replaces the a2a audit verifier.
    assert "append-only" in skill
    assert "ledger" in skill
    assert "DecisionRecord" in skill
    assert "report the committed DecisionRecord" in skill

    # The retired a2a-audit vocabulary is gone.
    for retired in (
        "verify_a2a_audit",
        "READY_FOR_EXTERNAL_AUDIT",
        "harness verifies the audit after Codex exits",
        "15 accepted calls",
        "packet id",
        "demo-consequence-packet",
    ):
        assert retired not in skill


def test_risk_persona_grounds_a_risk_claim_in_real_evidence():
    instructions = _agent("risk_challenger")["developer_instructions"]

    assert "propose_candidate" in instructions
    assert "post_claim" in instructions
    assert 'type="risk"' in instructions
    assert "Deal Inputs!B4" in instructions  # the COGS cell the risk claim grounds in
    assert "boundary_discount_pct" in instructions


def test_optimizer_advises_but_never_closes_the_decision():
    config = _agent("decision_optimizer")
    instructions = config["developer_instructions"]
    tools = set(config["mcp_servers"]["yigdesk"]["enabled_tools"])

    assert "read_board" in tools
    assert "post_claim" in tools
    assert "propose_candidate" not in tools
    assert "request_resolve" not in tools
    # The persona must say, in words, that it advises and never resolves.
    assert "non-binding" in instructions
    assert "request_resolve" in instructions  # named as the op it must NOT call
    assert "recommended_candidate_id" in instructions


def test_role_outputs_speak_the_new_op_vocabulary():
    instructions = {
        name: _agent(name)["developer_instructions"] for name in AGENT_NAMES
    }

    assert "submitted_discount_pct" in instructions["finance_analyst"]
    assert "propose_candidate" in instructions["finance_analyst"]
    assert "alternative_discount_pct" in instructions["sales_advocate"]
    assert "boundary_discount_pct" in instructions["risk_challenger"]
    assert "recommended_candidate_id" in instructions["decision_optimizer"]
