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

from yigdesk.board import build_blackboard

POLICY = {
    "decision_type": "council_discount",
    "required_approvals": [{"role": "cfo", "verdict": "approve"}],
    "required_claims": [{"type": "risk"}],
    "candidate_selector": "max:headroom",
}
HUMAN_POLICY = {**POLICY, "candidate_selector": "human_selected"}


def _seed_decision(
    bb, decision_id: str, question: str, *, policy=POLICY, include_hold: bool = False
) -> None:
    """Open one fully-populated council_discount decision on the shared ledger.

    discount=2 keeps headroom above the floor, so the candidate prices to an
    `ok` verdict and the `max:headroom` selector can commit deterministically.
    """
    bb.open_decision(
        decision_id, question, "council_discount", policy, actor="agent:mcp", role="owner"
    )
    bb.propose_candidate(
        decision_id, "c1", {"overrides": {"discount": 2}}, actor="finance", role="proposer"
    )
    bb.post_claim(
        decision_id,
        "k1",
        "risk",
        "c1",
        "cogs may rise",
        ["Deal Inputs!B4"],
        actor="risk",
        role="critic",
    )
    if include_hold:
        bb.propose_candidate(
            decision_id,
            "hold",
            {"overrides": {"discount": "invalid"}},
            actor="finance",
            role="proposer",
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
