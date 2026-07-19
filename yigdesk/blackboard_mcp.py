from __future__ import annotations
import json, os
from dataclasses import asdict
from pathlib import Path
from typing import Any
from mcp.server.fastmcp import FastMCP
from yigdesk.core.blackboard import Blackboard
from yigdesk.core.gate import Pending
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource

INSTRUCTIONS = """Yigdesk is a deterministic decision blackboard. Propose candidates and post
grounded claims; never invent figures - the engine prices every candidate and rejects ungrounded
claims. Treat HOLD as terminal for a candidate. Decisions close only through request_resolve; this
server never writes back to the source model."""

mcp = FastMCP("Yigdesk", instructions=INSTRUCTIONS, json_response=True)

def _bb() -> Blackboard:
    scn = Path(os.environ["YIGDESK_SCENARIO"])
    model = json.loads((scn / "model.json").read_text(encoding="utf-8"))
    src = ModelSource(scn / model["workbook"], model["input_refs"])
    return Blackboard(os.environ.get("YIGDESK_LEDGER", "runtime/board.jsonl"),
                      ExpressionEvaluator(model), src)

def _decision_dict(d):
    return {"id": d.id, "question": d.question, "status": d.status,
            "candidates": {cid: {"id": c.id, "author": c.author, "action": c.action,
                                 "status": c.status,
                                 "consequence": (asdict(c.consequence) if c.consequence is not None else None)}
                           for cid, c in d.candidates.items()},
            "claims": {cid: cl.__dict__ for cid, cl in d.claims.items()},
            "approvals": [a.__dict__ for a in d.approvals],
            "resolution": asdict(d.resolution) if d.resolution else None}

@mcp.tool()
def open_decision(decision_id: str, question: str, decision_type: str = "", policy: dict | None = None) -> dict[str, Any]:
    """Open a decision on the board."""
    _bb().open_decision(decision_id, question, decision_type, policy or {}, actor="agent:mcp", role="owner")
    return {"opened": decision_id}

@mcp.tool()
def propose_candidate(decision_id: str, candidate_id: str, overrides: dict) -> dict[str, Any]:
    """Propose a candidate action (input overrides); the engine prices it deterministically."""
    op = _bb().propose_candidate(decision_id, candidate_id, {"overrides": overrides}, actor="agent:mcp", role="proposer")
    return {"candidate_id": candidate_id, "consequence": op.payload["consequence"]}

@mcp.tool()
def post_claim(decision_id: str, claim_id: str, type: str, target: str, body: str, refs: list[str]) -> dict[str, Any]:
    """Post a claim; rejected fail-closed unless every ref grounds to real evidence."""
    c = _bb().post_claim(decision_id, claim_id, type, target, body, refs, actor="agent:mcp", role="critic")
    return {"claim_id": claim_id, "status": c.status}

@mcp.tool()
def cast_approval(decision_id: str, verdict: str, scope: str, role: str = "reviewer") -> dict[str, Any]:
    """Record an approval verdict (approve|hold|reject) scoped to a candidate or the decision."""
    _bb().cast_approval(decision_id, verdict, scope, actor="agent:mcp", role=role)
    return {"recorded": verdict}

@mcp.tool()
def request_resolve(decision_id: str) -> dict[str, Any]:
    """Run the deterministic gate; returns pending(reason) or the committed decision record."""
    r = _bb().request_resolve(decision_id, actor="agent:mcp", role="resolver")
    return {"pending": r.reason} if isinstance(r, Pending) else {"record": asdict(r)}

@mcp.tool()
def read_board() -> dict[str, Any]:
    """Return the deterministic board projection."""
    b = _bb().project()
    return {"decisions": {did: _decision_dict(d) for did, d in b.decisions.items()}}

def main() -> None:
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
