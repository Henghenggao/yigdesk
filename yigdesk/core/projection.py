from __future__ import annotations
from .model import Board, Decision, Candidate, Claim, Approval, DecisionRecord
from .ops import OPEN_DECISION, PROPOSE_CANDIDATE, POST_CLAIM, CAST_APPROVAL, RESOLVED

def fold(ops) -> Board:
    board = Board()
    for op in ops:
        p = op.payload; d = board.decisions.get(p.get("decision_id"))
        if op.kind == OPEN_DECISION:
            board.decisions[p["decision_id"]] = Decision(
                id=p["decision_id"], question=p["question"],
                decision_type=p["decision_type"], policy=p.get("policy", {}))
        elif op.kind == PROPOSE_CANDIDATE and d:
            d.candidates[p["candidate_id"]] = Candidate(
                id=p["candidate_id"], author=op.actor, action=p["action"],
                consequence=p.get("consequence"), status=p.get("status", "priced"))
        elif op.kind == POST_CLAIM and d:
            d.claims[p["claim_id"]] = Claim(
                id=p["claim_id"], author=op.actor, type=p["type"], target=p["target"],
                body=p.get("body",""), grounded_refs=p.get("grounded_refs", []),
                status=p.get("status", "grounded"))
        elif op.kind == CAST_APPROVAL and d:
            d.approvals.append(Approval(op.actor, op.role, p["verdict"], p["scope"]))
        elif op.kind == RESOLVED and d:
            d.resolution = DecisionRecord(**p["record"]); d.status = "resolved"
    return board
