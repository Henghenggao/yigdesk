from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
import json
from .model import Decision, DecisionRecord

@dataclass(frozen=True)
class Pending:
    reason: str

def _approvals_met(d: Decision) -> bool:
    for req in d.policy.get("required_approvals", []):
        if not any(a.role == req["role"] and a.verdict == req["verdict"] for a in d.approvals):
            return False
    return True

def _eligible(d: Decision):
    return [c for c in d.candidates.values()
            if c.consequence is not None and c.consequence.verdict == "ok"]

def _agent_identities(d: Decision) -> list[dict[str, str]]:
    values = [d.owner_identity, d.cutoff_identity]
    values.extend(candidate.agent_identity for candidate in d.candidates.values())
    values.extend(claim.agent_identity for claim in d.claims.values())
    unique = {
        json.dumps(value, sort_keys=True, separators=(",", ":")): value
        for value in values if value is not None
    }
    return [unique[key] for key in sorted(unique)]

def resolve(d: Decision, evaluator_revision: str, seq: int = 0):
    """Deterministic close: no LLM, pure function of (candidates, approvals, policy, revision)."""
    if not _approvals_met(d):
        return Pending("required approval missing")
    for req in d.policy.get("required_claims", []):
        if not any(c.status == "grounded" and c.type == req["type"] for c in d.claims.values()):
            return Pending(f"required claim of type {req['type']} missing")
    eligible = _eligible(d)
    if not eligible:
        return Pending("no candidate passes constraints (all HOLD/failed)")
    selector = d.policy.get("candidate_selector", "human_selected")
    if selector == "human_selected":
        eligible_ids = {c.id for c in eligible}
        picked = [a.scope for a in d.approvals if a.verdict == "approve" and a.scope in eligible_ids]
        if not picked:
            return Pending("human selection required (no approved eligible candidate)")
        chosen_id, closed_by = sorted(picked)[0], "human"
    elif selector.startswith("max:"):
        metric = selector.split(":", 1)[1]
        def key(c):
            m = {x.id: x.after for x in c.consequence.metrics}
            return (Decimal(m[metric]), c.id)
        chosen_id, closed_by = max(eligible, key=key).id, "policy"
    else:
        return Pending(f"unknown selector {selector}")
    chosen = d.candidates[chosen_id]
    return DecisionRecord(
        decision_id=d.id, chosen_candidate_id=chosen_id, closed_by=closed_by,
        rationale=f"selector={selector}", evidence_refs=chosen.consequence.evidence_refs,
        approvals=[a.__dict__ for a in d.approvals], evaluator_revision=evaluator_revision,
        source_fingerprint=chosen.consequence.fingerprint, seq=seq,
        cutoff_seq=d.cutoff_seq, agent_identities=_agent_identities(d))
