from __future__ import annotations
from .model import Board, Decision, Candidate, Claim, Approval, DecisionRecord, Consequence, Metric
from .ops import OPEN_DECISION, PROPOSE_CANDIDATE, POST_CLAIM, CAST_APPROVAL, RESOLVED

def _rehydrate_consequence(payload):
    if payload is None or not isinstance(payload, dict):
        return payload
    return Consequence(payload["verdict"], [Metric(**m) for m in payload["metrics"]],
                       payload["evidence_refs"], payload["fingerprint"])

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
                consequence=_rehydrate_consequence(p.get("consequence")), status=p.get("status", "priced"))
        elif op.kind == POST_CLAIM and d and p.get("status") != "rejected":
            d.claims[p["claim_id"]] = Claim(
                id=p["claim_id"], author=op.actor, type=p["type"], target=p["target"],
                body=p.get("body",""), grounded_refs=p.get("grounded_refs", []),
                status=p.get("status", "grounded"))
        elif op.kind == CAST_APPROVAL and d:
            d.approvals.append(Approval(op.actor, op.role, p["verdict"], p["scope"]))
        elif op.kind == RESOLVED and d:
            d.resolution = DecisionRecord(**p["record"]); d.status = "resolved"
    return board
