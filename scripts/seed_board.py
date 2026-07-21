"""Seed a Yigdesk decision-board ledger deterministically.

The CLI upload path can only bind a workbook session; it cannot open a decision,
propose a candidate, or post a claim. The MCP / agent side normally does that.
For the E2E harness we need the same shared ledger populated from the outside,
so this script writes the OPEN_DECISION / PROPOSE_CANDIDATE / POST_CLAIM ops that
the board web surface then renders and gates over.

It mirrors the fixture in tests/test_board_api.py: council_discount scenario,
`cfo` approval, a grounded `risk` claim, and a `max:headroom` policy selector.

Modes:
  (default)  one decision (d1)            -> single-decision / happy-path spec
  --multi    two decisions (d1, d2)       -> chooser-selection spec
  --empty    no decisions (empty ledger)  -> empty-board spec

The target ledger is truncated first so re-seeding between specs is deterministic.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from yigdesk.agent_identity import identity_from_env
from yigdesk.board import build_blackboard

POLICY = {
    "decision_type": "council_discount",
    "required_approvals": [{"role": "cfo", "verdict": "approve"}],
    "required_claims": [{"type": "risk"}],
    "candidate_selector": "max:headroom",
}
HUMAN_POLICY = {**POLICY, "candidate_selector": "human_selected"}


def _identity(agent_id: str):
    return identity_from_env({
        "YIGDESK_AGENT_ID": agent_id,
        "YIGDESK_AGENT_RUN_ID": "synthetic-preview-run",
        "YIGDESK_AGENT_INSTANCE_ID": f"{agent_id}-preview-instance",
        "YIGDESK_AGENT_PROFILE": agent_id,
        "YIGDESK_AGENT_MODEL": "gpt-5.6-terra",
        "YIGDESK_PROMPT_REVISION": f"{agent_id}-preview-v1",
        "YIGDESK_SKILLS_REVISION": "yigdesk-council-v1",
        "YIGDESK_MEMORY_REVISION": "isolated-none",
    })


def _seed_decision(
    bb, decision_id: str, question: str, *, policy=POLICY, include_hold: bool = False
) -> None:
    """Open one fully-populated council decision on the shared ledger."""
    orchestrator = _identity("orchestrator")
    bb.open_decision(
        decision_id, question, "council_discount", policy,
        actor=orchestrator.actor, role="owner", agent_identity=orchestrator,
    )
    # Match the real Codex council contract. Sales independently prices the
    # submitted request as well as its alternative; the focused manifest folds
    # the duplicate action into one comparison row while the ledger retains both.
    for candidate_id, discount, agent_id in (
        ("submitted_request", 12, "finance_analyst"),
        ("sales_submitted_assessment", 12, "sales_advocate"),
        ("sales_alternative", 15, "sales_advocate"),
        ("risk_boundary", 20, "risk_challenger"),
    ):
        identity = _identity(agent_id)
        bb.propose_candidate(
            decision_id, candidate_id, {"overrides": {"discount": discount}},
            actor=identity.actor, role="proposer", agent_identity=identity,
        )
    risk = _identity("risk_challenger")
    bb.post_claim(
        decision_id,
        "k1",
        "risk",
        "risk_boundary",
        "20% is the boundary candidate; rising COGS removes its margin cushion",
        ["Deal Inputs!B4"],
        actor=risk.actor,
        role="critic",
        agent_identity=risk,
    )
    optimizer = _identity("decision_optimizer")
    bb.post_claim(
        decision_id,
        "optimizer-advisory",
        "advisory",
        "submitted_request",
        "The submitted request has the greatest deterministic headroom; this is a non-binding financial recommendation.",
        ["Deal Inputs!B5"],
        actor=optimizer.actor,
        role="advisor",
        agent_identity=optimizer,
    )
    if include_hold:
        finance = _identity("finance_analyst")
        bb.propose_candidate(
            decision_id,
            "hold",
            {"overrides": {"discount": "invalid"}},
            actor=finance.actor,
            role="proposer",
            agent_identity=finance,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", type=Path, required=True, help="Built scenario directory.")
    ap.add_argument("--ledger", type=Path, required=True, help="Shared ledger (JSONL) to seed.")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--multi", action="store_true", help="Seed two decisions (chooser state).")
    group.add_argument("--empty", action="store_true", help="Seed an empty board (no decisions).")
    group.add_argument(
        "--human-selected", action="store_true", help="Seed a human-selected decision with a HOLD candidate."
    )
    a = ap.parse_args()

    # Start from a clean ledger so each E2E spec observes exactly what it seeded.
    a.ledger.unlink(missing_ok=True)
    a.ledger.with_name(a.ledger.name + ".lock").unlink(missing_ok=True)

    bb = build_blackboard(a.scenario, a.ledger)  # touches the (now empty) ledger file
    if a.empty:
        print("seeded", a.ledger, "(empty)")
        return

    _seed_decision(
        bb,
        "d1",
        "Approve the discount?",
        policy=HUMAN_POLICY if a.human_selected else POLICY,
        include_hold=a.human_selected,
    )
    if a.multi:
        _seed_decision(bb, "d2", "Approve the pilot expansion?")
    mode = "multi" if a.multi else "human-selected" if a.human_selected else "single"
    print("seeded", a.ledger, f"({mode})")


if __name__ == "__main__":
    main()
