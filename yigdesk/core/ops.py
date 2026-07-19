from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any

OPEN_DECISION="open_decision"; PROPOSE_CANDIDATE="propose_candidate"; POST_CLAIM="post_claim"
CAST_APPROVAL="cast_approval"; REQUEST_RESOLVE="request_resolve"; RESOLVED="resolved"
KINDS={OPEN_DECISION,PROPOSE_CANDIDATE,POST_CLAIM,CAST_APPROVAL,REQUEST_RESOLVE,RESOLVED}

@dataclass(frozen=True)
class Op:
    seq: int
    kind: str
    actor: str
    role: str
    payload: dict[str, Any]
    base_seq: int = 0

def op_to_json(op: Op) -> str:
    return json.dumps({"seq":op.seq,"kind":op.kind,"actor":op.actor,"role":op.role,
                       "base_seq":op.base_seq,"payload":op.payload},
                      sort_keys=True, separators=(",", ":"))

def op_from_json(line: str) -> Op:
    d=json.loads(line)
    return Op(d["seq"], d["kind"], d["actor"], d["role"], d["payload"], d.get("base_seq",0))
