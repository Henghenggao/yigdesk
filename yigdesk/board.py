"""Shared board construction + serialization for the MCP and web surfaces."""
from __future__ import annotations
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any
from yigdesk.core.blackboard import Blackboard
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource

DEFAULT_LEDGER = "runtime/board.jsonl"
DEFAULT_SCENARIO = "data/scenarios/council_discount"

def build_blackboard(scenario_dir, ledger_path=None) -> Blackboard:
    scn = Path(scenario_dir)
    model = json.loads((scn / "model.json").read_text(encoding="utf-8"))
    src = ModelSource(scn / model["workbook"], model["input_refs"])
    return Blackboard(str(ledger_path or DEFAULT_LEDGER), ExpressionEvaluator(model), src)

def build_blackboard_from_env(*, require_scenario: bool = False) -> Blackboard:
    scenario = os.environ.get("YIGDESK_SCENARIO")
    if require_scenario and not scenario:
        raise KeyError("YIGDESK_SCENARIO")
    return build_blackboard(scenario or DEFAULT_SCENARIO,
                            os.environ.get("YIGDESK_LEDGER", DEFAULT_LEDGER))

def decision_dict(d) -> dict[str, Any]:
    return {
        "id": d.id, "question": d.question,
        "decision_type": d.decision_type, "policy": d.policy, "status": d.status,
        "candidates": {cid: {"id": c.id, "author": c.author, "action": c.action, "status": c.status,
                             "consequence": (asdict(c.consequence) if c.consequence is not None else None)}
                       for cid, c in d.candidates.items()},
        "claims": {cid: cl.__dict__ for cid, cl in d.claims.items()},
        "approvals": [a.__dict__ for a in d.approvals],
        "resolution": asdict(d.resolution) if d.resolution else None,
    }

def board_dict(board) -> dict[str, Any]:
    return {"decisions": {did: decision_dict(d) for did, d in board.decisions.items()}}
