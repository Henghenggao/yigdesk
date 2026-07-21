from __future__ import annotations
from dataclasses import asdict
from .ledger import Ledger
from .projection import fold
from .gate import resolve, Pending
from . import ops as K
from .model import Claim
from yigdesk.agent_identity import validate_agent_identity


class LateWriteError(ValueError):
    def __init__(self, decision_id, cutoff_seq, operation):
        super().__init__(
            f"decision {decision_id!r} input closed at cutoff #{cutoff_seq}; "
            f"late {operation.kind} was rejected and audited at #{operation.seq}"
        )
        self.operation = operation

def _consequence_payload(c):
    return {"verdict": c.verdict, "metrics": [asdict(m) for m in c.metrics],
            "evidence_refs": c.evidence_refs, "fingerprint": c.fingerprint}

def _require_open_decision(txn, decision_id, ops=None):
    decision = fold(txn.read() if ops is None else ops).decisions.get(decision_id)
    if decision is None:
        raise KeyError(f"decision {decision_id!r} does not exist")
    if decision.resolution is not None:
        raise ValueError(f"decision {decision_id!r} is already resolved")
    return decision


def _head_seq(ops):
    return ops[-1].seq if ops else 0


def _identity_payload(agent_identity, actor=None):
    if agent_identity is None:
        return {}
    identity = validate_agent_identity(agent_identity)
    expected_actor = f"agent:{identity['agent_id']}"
    if actor is not None and actor != expected_actor:
        raise ValueError(
            f"actor {actor!r} does not match declared identity {expected_actor!r}"
        )
    return {"agent_identity": identity}


def _action_op(ops, action_id):
    return next((op for op in ops if op.payload.get("action_id") == action_id), None)


def _revision_has_new_candidate(ops, prior_action):
    return (
        prior_action.payload.get("action_type") == "request_revision"
        and any(
            op.seq > prior_action.seq
            and op.kind == K.PROPOSE_CANDIDATE
            and op.payload.get("decision_id")
            == prior_action.payload.get("decision_id")
            and op.payload.get("status") != "late_rejected"
            for op in ops
        )
    )

class Blackboard:
    def __init__(self, ledger_path, evaluator, source):
        self.ledger = Ledger(ledger_path); self.ev = evaluator; self.src = source

    def project(self):
        return fold(self.ledger.read())

    def open_decision(
        self, decision_id, question, decision_type, policy, *, actor, role,
        agent_identity=None,
    ):
        with self.ledger.transaction() as txn:
            operations = txn.read()
            if decision_id in fold(operations).decisions:
                raise ValueError(f"decision {decision_id!r} already exists")
            return txn.append(K.OPEN_DECISION, actor, role,
                {"decision_id": decision_id, "question": question,
                 "decision_type": decision_type, "policy": policy,
                 **_identity_payload(agent_identity, actor)},
                base_seq=_head_seq(operations))

    def propose_candidate(
        self, decision_id, candidate_id, action, *, actor, role,
        agent_identity=None,
    ):
        identity = _identity_payload(agent_identity, actor)
        c = self.ev.price(action, self.src)
        with self.ledger.transaction() as txn:
            operations = txn.read()
            decision = _require_open_decision(txn, decision_id, operations)
            if decision.cutoff_seq is not None:
                late = txn.append(K.PROPOSE_CANDIDATE, actor, role, {
                    "decision_id": decision_id, "candidate_id": candidate_id,
                    "action": action, "status": "late_rejected",
                    "cutoff_seq": decision.cutoff_seq,
                    "rejection_reason": "candidate arrived after input cutoff",
                    **identity,
                }, base_seq=decision.cutoff_seq)
                raise LateWriteError(decision_id, decision.cutoff_seq, late)
            return txn.append(K.PROPOSE_CANDIDATE, actor, role,
                {"decision_id": decision_id, "candidate_id": candidate_id, "action": action,
                 "consequence": _consequence_payload(c),
                 "status": "priced" if c.verdict == "ok" else "hold", **identity},
                base_seq=_head_seq(operations))

    def post_claim(
        self, decision_id, claim_id, ctype, target, body, refs, *, actor, role,
        agent_identity=None,
    ):
        identity = _identity_payload(agent_identity, actor)
        grounded = bool(refs) and all(self.ev.ground(r, self.src) for r in refs)
        status = "grounded" if grounded else "rejected"
        with self.ledger.transaction() as txn:
            operations = txn.read()
            decision = _require_open_decision(txn, decision_id, operations)
            if decision.cutoff_seq is not None:
                late = txn.append(K.POST_CLAIM, actor, role, {
                    "decision_id": decision_id, "claim_id": claim_id,
                    "type": ctype, "target": target, "body": body,
                    "grounded_refs": [], "status": "late_rejected",
                    "cutoff_seq": decision.cutoff_seq,
                    "rejection_reason": "claim arrived after input cutoff",
                    **identity,
                }, base_seq=decision.cutoff_seq)
                raise LateWriteError(decision_id, decision.cutoff_seq, late)
            txn.append(K.POST_CLAIM, actor, role,
                {"decision_id": decision_id, "claim_id": claim_id, "type": ctype, "target": target,
                 "body": body, "grounded_refs": refs if grounded else [], "status": status,
                 **identity}, base_seq=_head_seq(operations))
        return Claim(
            claim_id, actor, ctype, target, body, refs if grounded else [], status,
            identity.get("agent_identity"),
        )

    def cast_approval(
        self, decision_id, verdict, scope, *, actor, role, agent_identity=None,
    ):
        with self.ledger.transaction() as txn:
            operations = txn.read()
            decision = _require_open_decision(txn, decision_id, operations)
            cutoff = verdict in {"approve", "hold"} and decision.cutoff_seq is None
            cutoff_seq = txn.next_seq() if cutoff else decision.cutoff_seq
            return txn.append(K.CAST_APPROVAL, actor, role,
                {"decision_id": decision_id, "verdict": verdict, "scope": scope,
                 **({"cutoff": True, "cutoff_seq": cutoff_seq,
                     "cutoff_reason": "human_action"} if cutoff else {}),
                 **_identity_payload(agent_identity, actor)},
                base_seq=_head_seq(operations))

    def cast_action_approval(
        self, decision_id, verdict, scope, *, actor, role, action_id,
        correlation_id, action_type, candidate_id=None, note=None,
    ):
        """Append a browser approval once, with its durable idempotency receipt."""
        with self.ledger.transaction() as txn:
            ops = txn.read()
            existing = _action_op(ops, action_id)
            if existing is not None:
                expected = {
                    "decision_id": decision_id,
                    "verdict": verdict,
                    "scope": scope,
                    "correlation_id": correlation_id,
                    "action_type": action_type,
                    "candidate_id": candidate_id,
                    "note": note,
                }
                if (
                    existing.actor != actor
                    or existing.role != role
                    or any(existing.payload.get(key) != value for key, value in expected.items())
                ):
                    raise ValueError("action_id was already used with another request")
                return existing, True
            prior_human_action = next((
                op for op in reversed(ops)
                if op.kind == K.CAST_APPROVAL
                and op.actor == actor
                and op.payload.get("decision_id") == decision_id
                and op.payload.get("action_id")
            ), None)
            if (
                prior_human_action is not None
                and not _revision_has_new_candidate(ops, prior_human_action)
            ):
                raise ValueError("human action already recorded for decision")
            decision = _require_open_decision(txn, decision_id, ops)
            cutoff = (
                action_type in {"approve_candidate", "hold"}
                and decision.cutoff_seq is None
            )
            cutoff_seq = txn.next_seq() if cutoff else decision.cutoff_seq
            return txn.append(K.CAST_APPROVAL, actor, role, {
                "decision_id": decision_id,
                "verdict": verdict,
                "scope": scope,
                "action_id": action_id,
                "correlation_id": correlation_id,
                "action_type": action_type,
                "candidate_id": candidate_id,
                "note": note,
                **({"cutoff": True, "cutoff_seq": cutoff_seq,
                    "cutoff_reason": "human_action"} if cutoff else {}),
            }, base_seq=_head_seq(ops)), False

    def find_action(self, action_id):
        return _action_op(self.ledger.read(), action_id)

    def request_resolve(self, decision_id, *, actor, role, agent_identity=None):
        result, _replayed = self.request_resolve_with_status(
            decision_id, actor=actor, role=role, agent_identity=agent_identity,
        )
        return result

    def request_resolve_with_status(
        self, decision_id, *, actor, role, agent_identity=None,
        action_id=None, correlation_id=None, action_type=None,
        candidate_id=None, note=None,
    ):
        identity = _identity_payload(agent_identity, actor)
        with self.ledger.transaction() as txn:
            operations = txn.read()
            if action_id is not None:
                if not correlation_id or action_type != "resolve":
                    raise ValueError(
                        "resolve action_id, correlation_id, and action_type are required together"
                    )
                existing = _action_op(operations, action_id)
                if existing is not None:
                    expected = {
                        "decision_id": decision_id,
                        "correlation_id": correlation_id,
                        "action_type": action_type,
                        "candidate_id": candidate_id,
                        "note": note,
                    }
                    if (
                        existing.actor != actor
                        or existing.role != role
                        or any(
                            existing.payload.get(key) != value
                            for key, value in expected.items()
                        )
                    ):
                        raise ValueError(
                            "action_id was already used with another request"
                        )
                    decision = fold(operations).decisions.get(decision_id)
                    if decision is None:
                        raise KeyError(f"decision {decision_id!r} does not exist")
                    if decision.resolution is not None:
                        return decision.resolution, True
                    if existing.payload.get("action_outcome") == "pending":
                        return Pending(existing.payload["pending_reason"]), True
                    # Recover a committed outcome if the process stopped after
                    # the resolve receipt was fsynced but before RESOLVED landed.
                    recovered = resolve(
                        decision, self.ev.revision, seq=txn.next_seq()
                    )
                    if isinstance(recovered, Pending):
                        return recovered, True
                    txn.append(
                        K.RESOLVED, actor, role,
                        {
                            "decision_id": decision_id,
                            "record": asdict(recovered),
                            **identity,
                        },
                        base_seq=decision.cutoff_seq or 0,
                    )
                    return recovered, True
            d = fold(operations).decisions.get(decision_id)
            if d is None:
                raise KeyError(f"decision {decision_id!r} does not exist")
            if d.resolution is not None:           # idempotent: already closed
                if action_id is not None:
                    txn.append(K.REQUEST_RESOLVE, actor, role, {
                        "decision_id": decision_id,
                        "action_id": action_id,
                        "correlation_id": correlation_id,
                        "action_type": action_type,
                        "candidate_id": candidate_id,
                        "note": note,
                        "action_outcome": "resolved",
                        **identity,
                    }, base_seq=_head_seq(operations))
                    return d.resolution, False
                return d.resolution, True
            if action_id is not None:
                request_seq = txn.next_seq()
                establishes_cutoff = d.cutoff_seq is None
                if establishes_cutoff:
                    d.cutoff_seq = request_seq
                    d.cutoff_actor = actor
                    d.cutoff_reason = "resolution_requested"
                    d.cutoff_identity = identity.get("agent_identity")
                result = resolve(d, self.ev.revision, seq=request_seq + 1)
                payload = {
                    "decision_id": decision_id,
                    "action_id": action_id,
                    "correlation_id": correlation_id,
                    "action_type": action_type,
                    "candidate_id": candidate_id,
                    "note": note,
                    "action_outcome": (
                        "pending" if isinstance(result, Pending) else "resolved"
                    ),
                    **(
                        {"pending_reason": result.reason}
                        if isinstance(result, Pending) else {}
                    ),
                    **(
                        {
                            "cutoff": True,
                            "cutoff_seq": request_seq,
                            "cutoff_reason": "resolution_requested",
                        }
                        if establishes_cutoff else {}
                    ),
                    **identity,
                }
                txn.append(
                    K.REQUEST_RESOLVE, actor, role, payload,
                    base_seq=_head_seq(operations),
                )
                if isinstance(result, Pending):
                    return result, False
                txn.append(
                    K.RESOLVED, actor, role,
                    {"decision_id": decision_id, "record": asdict(result), **identity},
                    base_seq=d.cutoff_seq or 0,
                )
                return result, False
            if d.cutoff_seq is None:
                cutoff_seq = txn.next_seq()
                txn.append(K.REQUEST_RESOLVE, actor, role, {
                    "decision_id": decision_id, "cutoff": True,
                    "cutoff_seq": cutoff_seq,
                    "cutoff_reason": "resolution_requested", **identity,
                }, base_seq=_head_seq(operations))
                d = fold(txn.read()).decisions[decision_id]
            result = resolve(d, self.ev.revision, seq=txn.next_seq())
            if isinstance(result, Pending):
                return result, False
            txn.append(K.RESOLVED, actor, role,
                       {"decision_id": decision_id, "record": asdict(result), **identity},
                       base_seq=d.cutoff_seq or 0)
            return result, False
