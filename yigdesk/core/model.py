from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class Metric:
    id: str; label: str; value: str | None; before: str | None; after: str | None; unit: str = ""

@dataclass(frozen=True)
class Consequence:
    verdict: str                      # "ok" | "hold"
    metrics: list[Metric]
    evidence_refs: list[str]
    fingerprint: str

@dataclass
class Candidate:
    id: str; author: str; action: dict[str, Any]
    consequence: Consequence | None = None
    status: str = "priced"            # "priced" | "hold"
    agent_identity: dict[str, str] | None = None
    seq: int = 0

@dataclass
class Claim:
    id: str; author: str; type: str; target: str; body: str
    grounded_refs: list[str] = field(default_factory=list)
    status: str = "grounded"          # "grounded" | "rejected"
    agent_identity: dict[str, str] | None = None

@dataclass
class Approval:
    actor: str; role: str; verdict: str; scope: str   # verdict: approve|hold|reject
    seq: int = 0
    action_id: str | None = None
    correlation_id: str | None = None
    action_type: str | None = None
    candidate_id: str | None = None
    note: str | None = None

@dataclass
class DecisionRecord:
    decision_id: str; chosen_candidate_id: str; closed_by: str; rationale: str
    evidence_refs: list[str]; approvals: list[dict]; evaluator_revision: str
    source_fingerprint: str; seq: int
    cutoff_seq: int | None = None
    agent_identities: list[dict[str, str]] = field(default_factory=list)

@dataclass
class LateWrite:
    seq: int; kind: str; actor: str; role: str; target_id: str
    cutoff_seq: int; reason: str
    agent_identity: dict[str, str] | None = None

@dataclass
class Decision:
    id: str; question: str; decision_type: str; policy: dict[str, Any]
    status: str = "open"              # "open" | "resolved" | "held"
    candidates: dict[str, Candidate] = field(default_factory=dict)
    claims: dict[str, Claim] = field(default_factory=dict)
    approvals: list[Approval] = field(default_factory=list)
    resolution: DecisionRecord | None = None
    owner_identity: dict[str, str] | None = None
    cutoff_seq: int | None = None
    cutoff_actor: str | None = None
    cutoff_reason: str | None = None
    cutoff_identity: dict[str, str] | None = None
    late_writes: list[LateWrite] = field(default_factory=list)

@dataclass
class Board:
    decisions: dict[str, Decision] = field(default_factory=dict)
