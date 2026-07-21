from __future__ import annotations
from .model import Board, Decision, Candidate, Claim, Approval, DecisionRecord, Consequence, Metric, LateWrite
from .ops import OPEN_DECISION, PROPOSE_CANDIDATE, POST_CLAIM, CAST_APPROVAL, REQUEST_RESOLVE, RESOLVED

def _rehydrate_consequence(payload):
    if payload is None or not isinstance(payload, dict):
        return payload
    return Consequence(payload["verdict"], [Metric(**m) for m in payload["metrics"]],
                       payload["evidence_refs"], payload["fingerprint"])

def _apply_cutoff(d, op, reason):
    if d.cutoff_seq is None:
        d.cutoff_seq = op.payload.get("cutoff_seq", op.seq)
        d.cutoff_actor = op.actor
        d.cutoff_reason = reason
        d.cutoff_identity = op.payload.get("agent_identity")

def _late_write(d, op):
    p = op.payload
    target_id = p.get("candidate_id") or p.get("claim_id") or ""
    d.late_writes.append(LateWrite(
        seq=op.seq, kind=op.kind, actor=op.actor, role=op.role,
        target_id=target_id, cutoff_seq=d.cutoff_seq or p.get("cutoff_seq", 0),
        reason=p.get("rejection_reason", "operation arrived after input cutoff"),
        agent_identity=p.get("agent_identity"),
    ))

def fold(ops) -> Board:
    board = Board()
    for op in ops:
        p = op.payload; d = board.decisions.get(p.get("decision_id"))
        if op.kind == OPEN_DECISION and p["decision_id"] not in board.decisions:
            board.decisions[p["decision_id"]] = Decision(
                id=p["decision_id"], question=p["question"],
                decision_type=p["decision_type"], policy=p.get("policy", {}),
                owner_identity=p.get("agent_identity"))
        elif op.kind == PROPOSE_CANDIDATE and d and d.resolution is None:
            if d.cutoff_seq is not None or p.get("status") == "late_rejected":
                _late_write(d, op)
            else:
                d.candidates[p["candidate_id"]] = Candidate(
                    id=p["candidate_id"], author=op.actor, action=p["action"],
                    consequence=_rehydrate_consequence(p.get("consequence")),
                    status=p.get("status", "priced"),
                    agent_identity=p.get("agent_identity"), seq=op.seq)
        elif op.kind == POST_CLAIM and d and d.resolution is None:
            if d.cutoff_seq is not None or p.get("status") == "late_rejected":
                _late_write(d, op)
            elif p.get("status") != "rejected":
                d.claims[p["claim_id"]] = Claim(
                    id=p["claim_id"], author=op.actor, type=p["type"], target=p["target"],
                    body=p.get("body",""), grounded_refs=p.get("grounded_refs", []),
                    status=p.get("status", "grounded"),
                    agent_identity=p.get("agent_identity"))
        elif op.kind == CAST_APPROVAL and d and d.resolution is None:
            if p.get("cutoff"):
                _apply_cutoff(d, op, p.get("cutoff_reason", "human_action"))
            d.approvals.append(Approval(
                op.actor, op.role, p["verdict"], p["scope"], seq=op.seq,
                action_id=p.get("action_id"),
                correlation_id=p.get("correlation_id"),
                action_type=p.get("action_type"),
                candidate_id=p.get("candidate_id"), note=p.get("note"),
            ))
        elif op.kind == REQUEST_RESOLVE and d and d.resolution is None and p.get("cutoff"):
            _apply_cutoff(d, op, p.get("cutoff_reason", "resolution_requested"))
        elif op.kind == RESOLVED and d and d.resolution is None:
            d.resolution = DecisionRecord(**p["record"]); d.status = "resolved"
    return board
