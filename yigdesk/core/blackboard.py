from __future__ import annotations
from dataclasses import asdict
from .ledger import Ledger
from .projection import fold
from .gate import resolve, Pending
from . import ops as K
from .model import Claim

def _consequence_payload(c):
    return {"verdict": c.verdict, "metrics": [asdict(m) for m in c.metrics],
            "evidence_refs": c.evidence_refs, "fingerprint": c.fingerprint}

class Blackboard:
    def __init__(self, ledger_path, evaluator, source):
        self.ledger = Ledger(ledger_path); self.ev = evaluator; self.src = source

    def project(self):
        return fold(self.ledger.read())

    def open_decision(self, decision_id, question, decision_type, policy, *, actor, role):
        return self.ledger.append(K.OPEN_DECISION, actor, role,
            {"decision_id": decision_id, "question": question,
             "decision_type": decision_type, "policy": policy})

    def propose_candidate(self, decision_id, candidate_id, action, *, actor, role):
        c = self.ev.price(action, self.src)
        return self.ledger.append(K.PROPOSE_CANDIDATE, actor, role,
            {"decision_id": decision_id, "candidate_id": candidate_id, "action": action,
             "consequence": _consequence_payload(c), "status": "priced" if c.verdict == "ok" else "hold"})

    def post_claim(self, decision_id, claim_id, ctype, target, body, refs, *, actor, role):
        grounded = bool(refs) and all(self.ev.ground(r, self.src) for r in refs)
        status = "grounded" if grounded else "rejected"
        self.ledger.append(K.POST_CLAIM, actor, role,
            {"decision_id": decision_id, "claim_id": claim_id, "type": ctype, "target": target,
             "body": body, "grounded_refs": refs if grounded else [], "status": status})
        return Claim(claim_id, actor, ctype, target, body, refs if grounded else [], status)

    def cast_approval(self, decision_id, verdict, scope, *, actor, role):
        return self.ledger.append(K.CAST_APPROVAL, actor, role,
            {"decision_id": decision_id, "verdict": verdict, "scope": scope})

    def request_resolve(self, decision_id, *, actor, role):
        with self.ledger.transaction() as txn:
            board = fold(txn.read())
            d = board.decisions[decision_id]
            if d.resolution is not None:           # idempotent: already closed
                return d.resolution
            result = resolve(d, self.ev.revision, seq=txn.next_seq())
            if isinstance(result, Pending):
                return result
            txn.append(K.RESOLVED, actor, role,
                       {"decision_id": decision_id, "record": asdict(result)})
            return result
