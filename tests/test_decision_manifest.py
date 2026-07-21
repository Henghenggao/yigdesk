from __future__ import annotations

import pytest

from yigdesk.decision_manifest import ManifestError, VIEW_VERSION, build_manifest, validate_manifest


def _board():
    return {"decisions": {"d1": {
        "id": "d1", "question": "Approve Northwind?", "status": "open",
        "policy": {"required_approvals": [{"role": "cfo", "verdict": "approve"}], "candidate_selector": "max:headroom"},
        "candidates": {"d12": {"id": "d12", "action": {"overrides": {"discount": 12}}, "consequence": {"verdict": "ok", "metrics": [{"id": "gross_margin", "after": "45.45"}, {"id": "headroom", "after": "5.45"}], "evidence_refs": ["Deal Inputs!B2", "Deal Inputs!B4"], "fingerprint": "source-fingerprint-123456"}}},
        "claims": {"risk": {"type": "risk", "body": "COGS sensitivity", "grounded_refs": ["Deal Inputs!B4"]}},
        "resolution": None,
    }}}


def test_manifest_is_versioned_and_composes_only_trusted_blocks():
    manifest = build_manifest(_board(), evaluator_revision="expr:v1:model-revision")
    decision = manifest["decisions"][0]
    assert manifest["version"] == VIEW_VERSION
    assert VIEW_VERSION == "yigdesk-decision-view/v3"
    assert [block["type"] for block in decision["blocks"]] == ["comparison", "proof", "evidence", "warning", "actions", "history"]
    assert decision["blocks"][0]["candidates"][0]["label"] == "12%"
    proof = decision["blocks"][1]
    assert proof["summary"] == "All priced candidates share one source. CFO approval is still required."
    assert [(item["id"], item["status"]) for item in proof["items"]] == [
        ("source", "passed"),
        ("evidence", "passed"),
        ("human", "waiting"),
        ("gate", "blocked"),
    ]
    assert proof["record"] == {
        "source_fingerprint": "source-fingerprint-123456",
        "evaluator_revision": "expr:v1:model-revision",
        "cutoff_seq": None,
        "ledger_seq": None,
        "closed_by": None,
        "agent_count": 0,
    }
    assert decision["blocks"][3]["refs"] == ["Deal Inputs!B4"]


def test_max_headroom_policy_exposes_one_policy_approval_and_no_manual_resolve():
    board = _board()
    board["decisions"]["d1"]["candidates"]["d15"] = {
        "id": "d15",
        "action": {"overrides": {"discount": 15}},
        "consequence": {
            "verdict": "ok",
            "metrics": [
                {"id": "gross_margin", "after": "43.18"},
                {"id": "headroom", "after": "3.18"},
            ],
        },
    }

    decision = build_manifest(board)["decisions"][0]
    actions = next(block for block in decision["blocks"] if block["type"] == "actions")

    assert [item["action_type"] for item in actions["items"]] == [
        "approve_candidate",
        "hold",
        "request_revision",
    ]
    assert actions["items"][0]["candidate_id"] == "d12"
    assert actions["items"][0]["label"] == "Approve policy-selected outcome (12%)"


def test_manifest_surfaces_recorded_human_approval_as_awaiting_resolution():
    board = _board()
    board["decisions"]["d1"]["approvals"] = [{
        "actor": "human:web",
        "role": "cfo",
        "verdict": "approve",
        "scope": "d1",
    }]

    decision = build_manifest(board)["decisions"][0]
    actions = next(block for block in decision["blocks"] if block["type"] == "actions")

    assert decision["status"] == "awaiting_resolution"
    assert decision["conclusion"] == (
        "CFO approval recorded. The Codex workflow can now run the deterministic gate."
    )
    assert all(item["enabled"] is False for item in actions["items"])
    assert {item["reason"] for item in actions["items"]} == {
        "Human approval is recorded. Waiting for deterministic resolution."
    }


def test_resolved_manifest_proves_the_deterministic_commit_from_the_record():
    board = _board()
    board["decisions"]["d1"]["approvals"] = [{
        "actor": "human:web", "role": "cfo", "verdict": "approve", "scope": "d1",
    }]
    board["decisions"]["d1"]["resolution"] = {
        "decision_id": "d1",
        "chosen_candidate_id": "d12",
        "closed_by": "policy",
        "rationale": "selector=max:headroom",
        "evidence_refs": ["Deal Inputs!B2", "Deal Inputs!B4"],
        "approvals": board["decisions"]["d1"]["approvals"],
        "evaluator_revision": "expr:v1:committed-revision",
        "source_fingerprint": "source-fingerprint-123456",
        "seq": 18,
        "cutoff_seq": 17,
        "agent_identities": [{"agent_id": "finance_analyst"}],
    }

    decision = build_manifest(
        board, evaluator_revision="expr:v1:current-process-revision"
    )["decisions"][0]
    proof = next(block for block in decision["blocks"] if block["type"] == "proof")

    assert proof["summary"] == "No agent committed this outcome. The deterministic gate did."
    assert [(item["id"], item["status"]) for item in proof["items"]] == [
        ("source", "passed"),
        ("evidence", "passed"),
        ("human", "passed"),
        ("gate", "committed"),
    ]
    assert proof["items"][-1]["detail"] == "Committed · DecisionRecord #18"
    assert proof["record"] == {
        "source_fingerprint": "source-fingerprint-123456",
        "evaluator_revision": "expr:v1:committed-revision",
        "cutoff_seq": 17,
        "ledger_seq": 18,
        "closed_by": "policy",
        "agent_count": 1,
    }


def test_manifest_names_the_three_council_perspectives_contextually():
    board = _board()
    prototype = board["decisions"]["d1"]["candidates"].pop("d12")
    board["decisions"]["d1"]["candidates"] = {
        "submitted_request": {**prototype, "id": "submitted_request"},
        "sales_submitted_assessment": {
            **prototype,
            "id": "sales_submitted_assessment",
            "author": "agent:sales_advocate",
        },
        "sales_alternative": {
            **prototype,
            "id": "sales_alternative",
            "action": {"overrides": {"discount": 15}},
        },
        "risk_boundary": {
            **prototype,
            "id": "risk_boundary",
            "action": {"overrides": {"discount": 20}},
        },
    }

    decision = build_manifest(board)["decisions"][0]
    comparison = next(block for block in decision["blocks"] if block["type"] == "comparison")
    actions = next(block for block in decision["blocks"] if block["type"] == "actions")

    assert [(candidate["name"], candidate["label"]) for candidate in comparison["candidates"]] == [
        ("Submitted request", "12%"),
        ("Sales alternative", "15%"),
        ("Risk boundary", "20%"),
    ]
    assert actions["items"][0]["label"] == "Approve submitted request (12%)"


@pytest.mark.parametrize("payload", [
    {"version": VIEW_VERSION, "decisions": [], "script": "alert(1)"},
    {"version": VIEW_VERSION, "decisions": [{"id": "d", "title": "x", "status": "open", "conclusion": "x", "blocks": [{"type": "html", "body": "<script>"}]}]},
])
def test_manifest_rejects_unsafe_or_unknown_payloads(payload):
    with pytest.raises(ManifestError):
        validate_manifest(payload)


def test_manifest_rejects_unknown_fields_nested_inside_trusted_blocks():
    manifest = build_manifest(_board())
    comparison = manifest["decisions"][0]["blocks"][0]
    comparison["candidates"][0]["on_click"] = "run_agent_script()"

    with pytest.raises(ManifestError, match="comparison candidate contains unsupported fields"):
        validate_manifest(manifest)


@pytest.mark.parametrize("mutation, message", [
    (lambda proof: proof.__setitem__("html", "<script>alert(1)</script>"), "proof block contains unsupported fields"),
    (lambda proof: proof["items"][0].__setitem__("status", "agent_verified"), "unsupported proof status"),
])
def test_manifest_rejects_unsafe_or_invented_proof_capabilities(mutation, message):
    manifest = build_manifest(_board())
    proof = manifest["decisions"][0]["blocks"][1]
    mutation(proof)

    with pytest.raises(ManifestError, match=message):
        validate_manifest(manifest)
