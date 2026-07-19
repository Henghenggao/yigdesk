from __future__ import annotations
from dataclasses import asdict
from typing import Any
from mcp.server.fastmcp import FastMCP
from yigdesk.board import build_blackboard_from_env, board_dict
from yigdesk.core.gate import Pending

INSTRUCTIONS = """Yigdesk is a deterministic decision blackboard. Propose candidates and post
grounded claims; never invent figures - the engine prices every candidate and rejects ungrounded
claims. Treat HOLD as terminal for a candidate. Decisions close only through request_resolve; this
server never writes back to the source model."""

mcp = FastMCP("Yigdesk", instructions=INSTRUCTIONS, json_response=True)

@mcp.tool()
def open_decision(decision_id: str, question: str, decision_type: str = "", policy: dict | None = None) -> dict[str, Any]:
    """Open a decision on the board."""
    build_blackboard_from_env().open_decision(decision_id, question, decision_type, policy or {}, actor="agent:mcp", role="owner")
    return {"opened": decision_id}

@mcp.tool()
def propose_candidate(decision_id: str, candidate_id: str, overrides: dict) -> dict[str, Any]:
    """Propose a candidate action (input overrides); the engine prices it deterministically."""
    op = build_blackboard_from_env().propose_candidate(decision_id, candidate_id, {"overrides": overrides}, actor="agent:mcp", role="proposer")
    return {"candidate_id": candidate_id, "consequence": op.payload["consequence"]}

@mcp.tool()
def post_claim(decision_id: str, claim_id: str, type: str, target: str, body: str, refs: list[str]) -> dict[str, Any]:
    """Post a claim; rejected fail-closed unless every ref grounds to real evidence."""
    c = build_blackboard_from_env().post_claim(decision_id, claim_id, type, target, body, refs, actor="agent:mcp", role="critic")
    return {"claim_id": claim_id, "status": c.status}

@mcp.tool()
def cast_approval(decision_id: str, verdict: str, scope: str, role: str = "reviewer") -> dict[str, Any]:
    """Record an approval verdict (approve|hold|reject) scoped to a candidate or the decision."""
    build_blackboard_from_env().cast_approval(decision_id, verdict, scope, actor="agent:mcp", role=role)
    return {"recorded": verdict}

@mcp.tool()
def request_resolve(decision_id: str) -> dict[str, Any]:
    """Run the deterministic gate; returns pending(reason) or the committed decision record."""
    r = build_blackboard_from_env().request_resolve(decision_id, actor="agent:mcp", role="resolver")
    return {"pending": r.reason} if isinstance(r, Pending) else {"record": asdict(r)}

@mcp.tool()
def read_board() -> dict[str, Any]:
    """Return the deterministic board projection."""
    return board_dict(build_blackboard_from_env().project())

def main() -> None:
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
