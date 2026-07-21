"""Safe, declarative decision surfaces derived from a board projection.

The browser receives data, not agent-authored markup or executable code.  The
allow-list below is deliberately small; adding a visual capability is a renderer
change, not a way for an agent to inject a new behaviour into the page.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any


VIEW_VERSION = "yigdesk-decision-view/v3"
BLOCK_TYPES = {"comparison", "proof", "evidence", "warning", "actions", "history"}
ACTION_TYPES = {"approve_candidate", "hold", "request_revision", "resolve"}
PROOF_STATUSES = {"passed", "waiting", "blocked", "ready", "committed", "terminal"}
CANDIDATE_NAMES = {
    "submitted_request": "Submitted request",
    "sales_submitted_assessment": "Submitted request · Sales view",
    "sales_alternative": "Sales alternative",
    "risk_boundary": "Risk boundary",
}
CANDIDATE_DISPLAY_ORDER = {
    "submitted_request": 0,
    "sales_submitted_assessment": 1,
    "sales_alternative": 2,
    "risk_boundary": 3,
}


class ManifestError(ValueError):
    """A manifest is malformed or attempts to use an unsupported capability."""


def _expect_keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise ManifestError(f"{label} contains unsupported fields: {sorted(extra)}")


def _expect_string(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ManifestError(f"{label} must be a string")


def _expect_refs(value: Any, label: str) -> None:
    if not isinstance(value, list) or not all(isinstance(ref, str) for ref in value):
        raise ManifestError(f"{label} must be a list of strings")


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate the compact, versioned, renderer allow-list.

    This intentionally validates enough structure to make a future agent-facing
    manifest hand-off safe.  The built-in renderer still creates DOM nodes with
    ``textContent`` and never interprets HTML, URLs, or scripts.
    """
    if not isinstance(manifest, dict):
        raise ManifestError("manifest must be an object")
    _expect_keys(manifest, {"version", "decisions"}, "manifest")
    if manifest.get("version") != VIEW_VERSION:
        raise ManifestError("unsupported manifest version")
    if not isinstance(manifest.get("decisions"), list):
        raise ManifestError("decisions must be a list")
    for decision in manifest["decisions"]:
        if not isinstance(decision, dict):
            raise ManifestError("decision must be an object")
        _expect_keys(decision, {"id", "title", "status", "conclusion", "blocks"}, "decision")
        if not all(isinstance(decision.get(k), str) for k in ("id", "title", "status", "conclusion")):
            raise ManifestError("decision identity and copy must be strings")
        if not isinstance(decision.get("blocks"), list):
            raise ManifestError("blocks must be a list")
        for block in decision["blocks"]:
            if not isinstance(block, dict) or block.get("type") not in BLOCK_TYPES:
                raise ManifestError("unsupported block type")
            _expect_string(block.get("title"), "block title")
            if block["type"] == "comparison":
                _expect_keys(block, {"type", "title", "candidates", "metrics"}, "comparison block")
                if not isinstance(block.get("candidates"), list) or not isinstance(block.get("metrics"), list):
                    raise ManifestError("comparison lists required")
                if not all(isinstance(metric, str) for metric in block["metrics"]):
                    raise ManifestError("comparison metrics must be strings")
                for candidate in block["candidates"]:
                    if not isinstance(candidate, dict):
                        raise ManifestError("comparison candidate must be an object")
                    _expect_keys(
                        candidate,
                        {"id", "name", "label", "verdict", "gross_margin", "headroom"},
                        "comparison candidate",
                    )
                    for field in ("id", "name", "label", "verdict"):
                        _expect_string(candidate.get(field), f"comparison candidate {field}")
                    for field in ("gross_margin", "headroom"):
                        if candidate.get(field) is not None and not isinstance(
                            candidate[field], (str, int, float)
                        ):
                            raise ManifestError(f"comparison candidate {field} must be numeric text")
            elif block["type"] == "proof":
                _expect_keys(block, {"type", "title", "summary", "items", "record"}, "proof block")
                _expect_string(block.get("summary"), "proof summary")
                if not isinstance(block.get("items"), list):
                    raise ManifestError("proof items must be a list")
                for item in block["items"]:
                    if not isinstance(item, dict):
                        raise ManifestError("proof item must be an object")
                    _expect_keys(item, {"id", "label", "status", "detail"}, "proof item")
                    for field in ("id", "label", "status", "detail"):
                        _expect_string(item.get(field), f"proof item {field}")
                    if item["status"] not in PROOF_STATUSES:
                        raise ManifestError("unsupported proof status")
                record = block.get("record")
                if not isinstance(record, dict):
                    raise ManifestError("proof record must be an object")
                _expect_keys(
                    record,
                    {"source_fingerprint", "evaluator_revision", "cutoff_seq",
                     "ledger_seq", "closed_by", "agent_count"},
                    "proof record",
                )
                for field in ("source_fingerprint", "evaluator_revision", "closed_by"):
                    if record.get(field) is not None and not isinstance(record[field], str):
                        raise ManifestError(f"proof record {field} must be a string")
                for field in ("cutoff_seq", "ledger_seq", "agent_count"):
                    if record.get(field) is not None and not isinstance(record[field], int):
                        raise ManifestError(f"proof record {field} must be an integer")
            elif block["type"] == "evidence":
                _expect_keys(block, {"type", "title", "items"}, "evidence block")
                if not isinstance(block.get("items"), list):
                    raise ManifestError("evidence items must be a list")
                for item in block["items"]:
                    if not isinstance(item, dict):
                        raise ManifestError("evidence item must be an object")
                    _expect_keys(item, {"type", "body", "refs"}, "evidence item")
                    _expect_string(item.get("type"), "evidence type")
                    _expect_string(item.get("body"), "evidence body")
                    _expect_refs(item.get("refs"), "evidence refs")
            elif block["type"] == "warning":
                _expect_keys(block, {"type", "title", "body", "refs"}, "warning block")
                _expect_string(block.get("body"), "warning body")
                _expect_refs(block.get("refs"), "warning refs")
            elif block["type"] == "actions":
                _expect_keys(block, {"type", "title", "role", "items"}, "actions block")
                _expect_string(block.get("role"), "actions role")
                if not isinstance(block.get("items"), list):
                    raise ManifestError("action items must be a list")
                for action in block.get("items", []):
                    if not isinstance(action, dict):
                        raise ManifestError("action must be an object")
                    _expect_keys(action, {"id", "label", "action_type", "candidate_id", "enabled", "reason"}, "action")
                    if action.get("action_type") not in ACTION_TYPES:
                        raise ManifestError("unsupported action type")
                    for field in ("id", "label", "reason"):
                        _expect_string(action.get(field), f"action {field}")
                    if action.get("candidate_id") is not None and not isinstance(action["candidate_id"], str):
                        raise ManifestError("action candidate_id must be a string")
                    if not isinstance(action.get("enabled"), bool):
                        raise ManifestError("action enabled must be a boolean")
            else:
                _expect_keys(block, {"type", "title", "items"}, "history block")
                if not isinstance(block.get("items"), list):
                    raise ManifestError("history items must be a list")
    return manifest


def _metric(candidate: dict[str, Any], metric_id: str) -> Any:
    for metric in candidate.get("consequence", {}).get("metrics", []):
        if metric.get("id") == metric_id:
            return metric.get("after")
    return None


def _discount_label(candidate: dict[str, Any]) -> str:
    discount = candidate.get("action", {}).get("overrides", {}).get("discount")
    return f"{discount}%" if discount is not None else candidate["id"]


def _candidate_name(candidate: dict[str, Any]) -> str:
    return CANDIDATE_NAMES.get(candidate["id"], candidate["id"].replace("_", " ").title())


def _comparison_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Show one row per calculated action while retaining every ledger proposal.

    Multiple agents may independently price the submitted request. Their distinct
    identities remain in the board and DecisionRecord, but a focused executive
    comparison should not repeat an identical numeric option.
    """

    distinct: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = json.dumps(candidate.get("action", {}), sort_keys=True, separators=(",", ":"))
        current = distinct.get(key)
        if current is None or CANDIDATE_DISPLAY_ORDER.get(
            candidate["id"], 100
        ) < CANDIDATE_DISPLAY_ORDER.get(current["id"], 100):
            distinct[key] = candidate
    return sorted(
        distinct.values(),
        key=lambda candidate: (
            CANDIDATE_DISPLAY_ORDER.get(candidate["id"], 100), candidate["id"]
        ),
    )


def _required_role(decision: dict[str, Any]) -> str:
    approvals = decision.get("policy", {}).get("required_approvals", [])
    return approvals[0].get("role", "reviewer") if approvals else "reviewer"


def _role_label(role: str) -> str:
    return role.upper() if len(role) <= 4 else role.replace("_", " ").title()


def _latest_human_action(decision: dict[str, Any]) -> dict[str, Any] | None:
    action = next(
        (
            approval
            for approval in reversed(decision.get("approvals", []))
            if str(approval.get("actor", "")).startswith("human:")
        ),
        None,
    )
    if (
        action is not None
        and action.get("action_type") == "request_revision"
        and any(
            candidate.get("seq", 0) > action.get("seq", 0)
            for candidate in decision.get("candidates", {}).values()
        )
    ):
        return None
    return action


def _locked_action_reason(action: dict[str, Any] | None) -> str:
    if action is None:
        return ""
    return {
        "approve": "Human approval is recorded. Waiting for deterministic resolution.",
        "hold": "This decision is held. No further execution will run.",
        "reject": "Revision was requested. The active workflow must provide a new proposal.",
    }.get(action.get("verdict"), "A human action is already recorded.")


def _approval_met(decision: dict[str, Any]) -> bool:
    return all(
        any(
            approval.get("role") == requirement.get("role")
            and approval.get("verdict") == requirement.get("verdict")
            for approval in decision.get("approvals", [])
        )
        for requirement in decision.get("policy", {}).get("required_approvals", [])
    )


def _evidence_met(decision: dict[str, Any]) -> bool:
    return all(
        any(
            claim.get("type") == requirement.get("type")
            and claim.get("status", "grounded") == "grounded"
            for claim in decision.get("claims", {}).values()
        )
        for requirement in decision.get("policy", {}).get("required_claims", [])
    )


def _declared_agent_count(decision: dict[str, Any]) -> int:
    resolution = decision.get("resolution") or {}
    identities = list(resolution.get("agent_identities") or [])
    if not identities:
        identities = [decision.get("owner_identity")]
        identities.extend(
            candidate.get("agent_identity")
            for candidate in decision.get("candidates", {}).values()
        )
        identities.extend(
            claim.get("agent_identity")
            for claim in decision.get("claims", {}).values()
        )
    return len({
        json.dumps(identity, sort_keys=True, separators=(",", ":"))
        for identity in identities if isinstance(identity, dict)
    })


def _decision_proof(
    decision: dict[str, Any],
    candidates: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    human_action: dict[str, Any] | None,
    evaluator_revision: str | None,
) -> dict[str, Any]:
    fingerprints = {
        candidate.get("consequence", {}).get("fingerprint")
        for candidate in candidates
        if candidate.get("consequence", {}).get("fingerprint")
    }
    priced = [candidate for candidate in candidates if candidate.get("consequence") is not None]
    distinct_options = _comparison_candidates(candidates)
    shared_source = next(iter(fingerprints)) if fingerprints and len(fingerprints) == 1 else None
    source_ok = bool(candidates) and len(priced) == len(candidates) and shared_source is not None
    evidence_ok = _evidence_met(decision)
    approval_ok = _approval_met(decision)
    resolution = decision.get("resolution")
    cutoff_seq = (resolution or {}).get("cutoff_seq")
    if cutoff_seq is None:
        cutoff_seq = (decision.get("cutoff") or {}).get("seq")
    agent_count = _declared_agent_count(decision)
    role = _role_label(_required_role(decision))
    terminal_verdict = human_action.get("verdict") if human_action else None

    evidence_refs = sorted({
        ref
        for claim in decision.get("claims", {}).values()
        if claim.get("status", "grounded") == "grounded"
        for ref in claim.get("grounded_refs", [])
    })
    if not source_ok:
        summary = "Decision proof is incomplete. Candidate source fingerprints do not agree."
    elif terminal_verdict in {"hold", "reject"}:
        summary = "Human intent stopped this decision before deterministic commit."
    elif not approval_ok:
        summary = f"All priced candidates share one source. {role} approval is still required."
    elif not evidence_ok:
        summary = "All priced candidates share one source. Grounded evidence is still required."
    else:
        summary = "Human intent is durable. The deterministic gate is ready to commit."

    if terminal_verdict in {"hold", "reject"}:
        human_status, human_detail = "terminal", (
            "Hold recorded" if terminal_verdict == "hold" else "Revision requested"
        )
        gate_status, gate_detail = "terminal", "Stopped · no decision committed"
    elif approval_ok:
        human_status, human_detail = "passed", f"{role} approval recorded"
        gate_status, gate_detail = (
            ("ready", "Ready · deterministic requirements satisfied")
            if source_ok and evidence_ok and eligible
            else ("blocked", "Blocked · deterministic requirements incomplete")
        )
    else:
        human_status, human_detail = "waiting", f"{role} approval required"
        gate_status, gate_detail = "blocked", "Blocked · required approval missing"

    record = {
        "source_fingerprint": shared_source,
        "evaluator_revision": evaluator_revision,
        "cutoff_seq": cutoff_seq,
        "ledger_seq": None,
        "closed_by": None,
        "agent_count": agent_count,
    }
    if resolution:
        shared_source = resolution.get("source_fingerprint")
        source_ok = bool(shared_source)
        summary = "No agent committed this outcome. The deterministic gate did."
        human_status, human_detail = "passed", f"{role} approval recorded"
        gate_status, gate_detail = (
            "committed",
            f"Committed · DecisionRecord #{resolution.get('seq')}",
        )
        record = {
            "source_fingerprint": shared_source,
            "evaluator_revision": resolution.get("evaluator_revision"),
            "cutoff_seq": resolution.get("cutoff_seq", cutoff_seq),
            "ledger_seq": resolution.get("seq"),
            "closed_by": resolution.get("closed_by"),
            "agent_count": len(resolution.get("agent_identities") or []) or agent_count,
        }

    return {
        "type": "proof",
        "title": "Decision proof",
        "summary": summary,
        "items": [
            {
                "id": "source",
                "label": "Shared source",
                "status": "passed" if source_ok else "blocked",
                "detail": (
                    f"{len(priced)}/{len(candidates)} proposals · "
                    f"{len(distinct_options)} distinct options · source {shared_source[:12]}"
                    if source_ok else "Candidate source fingerprints disagree"
                ),
            },
            {
                "id": "evidence",
                "label": "Grounded evidence",
                "status": "passed" if evidence_ok else "waiting",
                "detail": "Grounded · " + " · ".join(evidence_refs) if evidence_ok else "Required evidence missing",
            },
            {"id": "human", "label": "Human authority", "status": human_status, "detail": human_detail},
            {"id": "gate", "label": "Deterministic gate", "status": gate_status, "detail": gate_detail},
        ],
        "record": record,
    }


def _policy_selected_candidate(
    decision: dict[str, Any], eligible: list[dict[str, Any]]
) -> dict[str, Any] | None:
    selector = decision.get("policy", {}).get("candidate_selector", "human_selected")
    if not selector.startswith("max:") or not eligible:
        return None
    metric_id = selector.split(":", 1)[1]

    def key(candidate: dict[str, Any]) -> tuple[Decimal, str]:
        value = _metric(candidate, metric_id)
        try:
            return Decimal(str(value)), candidate["id"]
        except (InvalidOperation, TypeError):
            return Decimal("-Infinity"), candidate["id"]

    selected = max(eligible, key=key)
    return selected if key(selected)[0].is_finite() else None


def build_manifest(
    board: dict[str, Any], *, evaluator_revision: str | None = None
) -> dict[str, Any]:
    """Produce the default focused composition from trusted board data only."""
    decisions = []
    for decision in board.get("decisions", {}).values():
        candidates = list(decision.get("candidates", {}).values())
        comparison_candidates = _comparison_candidates(candidates)
        eligible = [c for c in candidates if c.get("consequence", {}).get("verdict") == "ok"]
        resolved = bool(decision.get("resolution"))
        human_action = _latest_human_action(decision)
        locked_reason = _locked_action_reason(human_action)
        claims = list(decision.get("claims", {}).values())
        risk_claims = [c for c in claims if c.get("type") == "risk"]
        late_writes = decision.get("late_writes", [])
        other_claims = [c for c in claims if c.get("type") != "risk"]
        model_refs = sorted({
            ref
            for candidate in candidates
            for ref in candidate.get("consequence", {}).get("evidence_refs", [])
        })
        evidence_items = [{
            "type": "shared model",
            "body": "Every candidate was priced by the same deterministic evaluator against one immutable source.",
            "refs": model_refs,
        }]
        evidence_items.extend({
            "type": claim.get("type"),
            "body": claim.get("body"),
            "refs": claim.get("grounded_refs", []),
        } for claim in other_claims)
        action_items = []
        selector = decision.get("policy", {}).get("candidate_selector", "human_selected")
        policy_selected = _policy_selected_candidate(decision, eligible)
        if selector.startswith("max:"):
            label = _discount_label(policy_selected) if policy_selected else "unavailable"
            candidate_name = _candidate_name(policy_selected).lower() if policy_selected else "outcome"
            approval_label = (
                f"Approve {candidate_name} ({label})"
                if policy_selected and policy_selected["id"] in CANDIDATE_NAMES
                else f"Approve policy-selected outcome ({label})"
            )
            action_items.append({
                "id": "approve:policy-selected",
                "label": approval_label,
                "action_type": "approve_candidate",
                "candidate_id": policy_selected["id"] if policy_selected else None,
                "enabled": not resolved and human_action is None and policy_selected is not None,
                "reason": (
                    "Decision is already resolved."
                    if resolved
                    else locked_reason
                    if human_action is not None
                    else "The policy cannot select an eligible candidate."
                ) if resolved or human_action is not None or policy_selected is None else "",
            })
        else:
            for candidate in comparison_candidates:
                allowed = not resolved and human_action is None and candidate in eligible
                action_items.append({
                    "id": f"approve:{candidate['id']}",
                    "label": f"Approve {_candidate_name(candidate).lower()} ({_discount_label(candidate)})",
                    "action_type": "approve_candidate", "candidate_id": candidate["id"],
                    "enabled": allowed,
                    "reason": (
                        "Decision is already resolved." if resolved else
                        locked_reason if human_action is not None else
                        "Candidate does not pass the deterministic constraints."
                        if candidate not in eligible else ""
                    ),
                })
        action_items.extend([
            {"id": "hold", "label": "Hold", "action_type": "hold", "candidate_id": None,
             "enabled": not resolved and human_action is None,
             "reason": "Decision is already resolved." if resolved else locked_reason},
            {"id": "revision", "label": "Request revision", "action_type": "request_revision", "candidate_id": None,
             "enabled": not resolved and human_action is None,
             "reason": "Decision is already resolved." if resolved else locked_reason},
        ])
        required_role = _role_label(_required_role(decision))
        if resolved:
            view_status = "resolved"
            conclusion = f"Resolved: {decision['resolution']['chosen_candidate_id']} committed deterministically."
        elif human_action and human_action.get("verdict") == "approve":
            view_status = "awaiting_resolution"
            conclusion = f"{required_role} approval recorded. The Codex workflow can now run the deterministic gate."
        elif human_action and human_action.get("verdict") == "hold":
            view_status = "held"
            conclusion = f"Held by {required_role}. No resolution will be committed."
        elif human_action:
            view_status = "revision_requested"
            conclusion = f"Revision requested by {required_role}. The active workflow needs a new proposal."
        elif eligible and risk_claims:
            view_status = decision.get("status", "open")
            conclusion = f"Ready for {required_role} approval; the deterministic gate is the only remaining step."
        else:
            view_status = decision.get("status", "open")
            conclusion = "Waiting for an eligible candidate and grounded risk evidence."
        proof = _decision_proof(
            decision, candidates, eligible, human_action, evaluator_revision
        )
        risk_body = (
            risk_claims[0].get("body", "No grounded risk boundary yet.")
            if risk_claims else "No grounded risk boundary yet."
        )
        if late_writes:
            cutoff_seq = (decision.get("cutoff") or {}).get("seq")
            risk_body += (
                f" Input cutoff #{cutoff_seq} rejected and audited "
                f"{len(late_writes)} late agent write(s); none entered the gate."
            )
        decisions.append({
            "id": decision["id"], "title": decision.get("question") or decision["id"],
            "status": view_status, "conclusion": conclusion,
            "blocks": [
                {"type": "comparison", "title": "Candidate comparison", "metrics": ["gross_margin", "headroom"],
                 "candidates": [{"id": c["id"], "name": _candidate_name(c), "label": _discount_label(c),
                                  "verdict": c.get("consequence", {}).get("verdict", "hold"),
                                  "gross_margin": _metric(c, "gross_margin"), "headroom": _metric(c, "headroom")}
                                for c in comparison_candidates]},
                proof,
                {"type": "evidence", "title": "Grounded evidence",
                 "items": evidence_items},
                {"type": "warning", "title": "Risk boundary",
                 "body": risk_body,
                 "refs": risk_claims[0].get("grounded_refs", []) if risk_claims else []},
                {
                    "type": "actions",
                    "title": f"Human decision gate · {required_role}",
                    "role": _required_role(decision),
                    "items": action_items,
                },
                {"type": "history", "title": "Previous decisions", "items": []},
            ],
        })
    return validate_manifest({"version": VIEW_VERSION, "decisions": decisions})
