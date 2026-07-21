"""Ledger-backed continuation for a live Codex decision turn.

This module does not add a blackboard operation. It observes durable human
actions written through ``cast_approval`` and, for an approval, invokes the
existing deterministic ``request_resolve`` boundary.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .board import build_blackboard
from .core.gate import Pending
from .core.ops import CAST_APPROVAL


def _human_action(bb, decision_id: str, after_seq: int):
    for op in bb.ledger.read():
        if (
            op.seq > after_seq
            and op.kind == CAST_APPROVAL
            and op.actor == "human:web"
            and op.payload.get("decision_id") == decision_id
            and op.payload.get("action_id")
        ):
            return op
    return None


def continue_decision(
    bb,
    decision_id: str,
    *,
    after_seq: int,
    timeout_seconds: float,
    poll_seconds: float = 0.1,
) -> dict[str, Any]:
    """Wait for one durable browser action and continue the deterministic flow."""
    deadline = time.monotonic() + max(timeout_seconds, 0)
    while True:
        op = _human_action(bb, decision_id, after_seq)
        if op is not None:
            action_type = op.payload.get("action_type")
            common = {
                "decision_id": decision_id,
                "action_id": op.payload["action_id"],
                "action_type": action_type,
                "action_seq": op.seq,
            }
            if action_type == "approve_candidate":
                result, replayed = bb.request_resolve_with_status(
                    decision_id, actor="orchestrator:codex", role="owner"
                )
                if isinstance(result, Pending):
                    return {**common, "status": "pending", "reason": result.reason}
                return {
                    **common,
                    "status": "resolved",
                    "record": asdict(result),
                    "resolve_replayed": replayed,
                }
            if action_type == "hold":
                return {**common, "status": "held", "reason": op.payload.get("note")}
            return {
                **common,
                "status": "revision_requested",
                "reason": op.payload.get("note"),
            }
        if time.monotonic() >= deadline:
            return {
                "status": "pending",
                "decision_id": decision_id,
                "reason": "human action deadline missed",
            }
        time.sleep(max(poll_seconds, 0.001))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Continue one Yigdesk decision after a durable browser action."
    )
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--after-seq", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    result = continue_decision(
        build_blackboard(args.scenario, args.ledger),
        args.decision_id,
        after_seq=args.after_seq,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(2 if result["status"] == "pending" else 0)


if __name__ == "__main__":
    main()
