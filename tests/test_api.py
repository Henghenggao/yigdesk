from pathlib import Path
from io import BytesIO
import json
import threading
import time

import yigdesk.app as app_module
from yigdesk.agent import AgentExecutionError, AgentVerificationError
from yigdesk.app import create_app
from yigdesk.session import load_active_session
from yigdesk.workbook import create_workbook, fingerprint
from scripts.generate_sample_workbook import generate


class FakeCodexRunner:
    model = "gpt-test"

    def run(self, packet, *, base_url, revision_id=None, progress_callback=None):
        if progress_callback is not None:
            progress_callback("sensitive raw runner event")
            progress_callback(
                {
                    "phase": "starting_codex",
                    "elapsed_ms": 4,
                    "stderr": "sensitive stderr must not cross the API boundary",
                }
            )
            progress_callback({"phase": "calling_yigdesk_tools", "elapsed_ms": 8})
            progress_callback({"phase": "verifying_result", "elapsed_ms": 11})
        consequence = packet["consequence"]
        return {
            "verified": True,
            "model": self.model,
            "thread_id": "thread-test",
            "latency_ms": 12,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "tool_calls": ["get_deal_context", "preview_consequence", "inspect_evidence"],
            "audit": [
                {"tool": "get_deal_context", "ok": True},
                {"tool": "preview_consequence", "ok": True, "packet_id": packet["packet_id"]},
                {"tool": "inspect_evidence", "ok": True, "address": "Deal Model!B4"},
            ],
            "answer": {
                "packet_id": packet["packet_id"],
                "verdict": consequence["verdict"],
                "summary": "Evidence is complete and ready for review.",
                "metrics": {
                    key: consequence["display"][key]
                    for key in ("net_arr", "arr_impact", "gross_margin", "headroom")
                },
                "inspected_evidence": "Deal Model!B4",
                "draft_status": "NOT_SENT",
            },
        }


class FailingCodexRunner:
    model = "gpt-test"

    def run(self, packet, *, base_url, revision_id=None, progress_callback=None):
        if progress_callback is not None:
            progress_callback({"phase": "starting_codex", "elapsed_ms": 5})
            progress_callback({"phase": "calling_yigdesk_tools", "elapsed_ms": 22})
        error = AgentExecutionError("sensitive path C:\\private\\codex-auth.json")
        error.phase = "starting_codex"
        error.elapsed_ms = 23
        raise error


class BlockingCodexRunner(FakeCodexRunner):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.revision_id = None

    def run(self, packet, *, base_url, revision_id=None, progress_callback=None):
        self.revision_id = revision_id
        self.started.set()
        assert self.release.wait(timeout=2)
        return super().run(
            packet,
            base_url=base_url,
            revision_id=revision_id,
            progress_callback=progress_callback,
        )


class VerificationFailingRunner:
    model = "gpt-test"

    def run(self, packet, *, base_url, revision_id=None, progress_callback=None):
        raise AgentVerificationError(
            "sensitive verifier detail",
            code="AGENT_EVIDENCE_MISMATCH",
        )


def make_client(tmp_path: Path):
    app = create_app(runtime_dir=tmp_path / "runtime")
    app.config.update(TESTING=True)
    return app, app.test_client()


def agent_run_headers(client):
    agent = client.get("/api/state").get_json()["agent"]
    return {"X-Yigdesk-Agent-Token": agent["request_token"]}


def test_analysis_is_byte_read_only_and_declares_demo_adapter_scope(tmp_path):
    app, client = make_client(tmp_path)
    state = app.config["YIGDESK_STATE"]
    before = fingerprint(state.workbook_path)

    response = client.post("/api/analyze", json={})

    assert response.status_code == 200
    packet = response.get_json()["packet"]
    assert packet["analysis_bytes_unchanged"] is True
    assert packet["source_fingerprint"] == before == fingerprint(state.workbook_path)
    assert packet["packet_id"].startswith("cpkt-")
    assert packet["protocol_version"] == "demo-consequence-packet/v1"
    assert packet["implementation_scope"] == "synthetic-five-formula-adapter"
    assert packet["capabilities"] == ["read_view", "inspect", "preview_consequence"]
    assert packet["consequence"]["display"]["net_arr"] == "$880k"


def test_state_and_preview_never_change_the_source_model(tmp_path):
    app, client = make_client(tmp_path)
    state = app.config["YIGDESK_STATE"]
    before = state.workbook_path.read_bytes()

    client.get("/api/state")
    client.get("/api/inspect", query_string={"address": "Deal Model!B4"})
    client.post("/api/analyze", json={})
    client.post("/api/analyze", json={})

    assert state.workbook_path.read_bytes() == before


def test_complete_and_incomplete_scenarios_are_both_live(tmp_path):
    _, client = make_client(tmp_path)
    ready = client.post("/api/analyze", json={}).get_json()["packet"]["consequence"]
    client.post("/api/reset", json={"scenario_id": "hold"})
    hold = client.post("/api/analyze", json={}).get_json()["packet"]["consequence"]

    assert ready["verdict"] == "READY FOR CFO"
    assert ready["complete"] is True
    assert hold["verdict"] == "HOLD"
    assert hold["complete"] is False
    assert hold["display"]["gross_margin"] == "Unavailable"


def test_reset_rejects_non_object_json_instead_of_raising(tmp_path):
    _, client = make_client(tmp_path)

    response = client.post("/api/reset", json=[])

    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_REQUEST"


def test_object_inspection_returns_formula_and_lineage(tmp_path):
    _, client = make_client(tmp_path)
    response = client.get("/api/inspect", query_string={"address": "Deal Model!B4"})

    assert response.status_code == 200
    inspection = response.get_json()
    assert inspection["formula"] == "Gross profit ÷ Net ARR"
    assert inspection["precedents"] == ["Deal Model!B3", "Deal Model!B2"]
    assert inspection["dependents"] == ["Deal Model!B5"]
    assert inspection["value_verified"] is True


def test_commercial_capability_routes_are_absent(tmp_path):
    app, client = make_client(tmp_path)
    registered = {rule.rule for rule in app.url_map.iter_rules()}
    for route in ("/api/approve", "/api/writeback", "/api/vault", "/api/attestation", "/api/audit"):
        assert route not in registered
        assert client.post(route, json={}).status_code in {404, 405}


def test_static_surface_exposes_neutral_components_and_public_scope(tmp_path):
    _, client = make_client(tmp_path)
    html = client.get("/").get_data(as_text=True)
    assert "Yigdesk" in html
    assert "<yig-grid" in html
    assert "<yig-model-inspector" in html
    assert "No business write-back" in html
    assert "proprietary Yigrid kernel not included" in html


def test_agent_run_is_explicitly_unavailable_without_codex_configuration(tmp_path):
    app = create_app(runtime_dir=tmp_path / "runtime", agent_enabled=False)
    client = app.test_client()

    state = client.get("/api/state").get_json()
    response = client.post("/api/agent-runs", json={})

    assert state["agent"] == {
        "available": False,
        "mode": "local-preview",
        "model": None,
        "reasoning_effort": None,
    }
    assert response.status_code == 503
    assert response.get_json()["code"] == "CODEX_UNAVAILABLE"


def test_legacy_codex_environment_flag_no_longer_starts_a_nested_runner(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("YIGDESK_CODEX_ENABLED", "1")
    monkeypatch.delenv("YIGDESK_NESTED_CODEX_ENABLED", raising=False)

    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_runner=FakeCodexRunner(),
    )

    assert app.test_client().get("/api/state").get_json()["agent"]["available"] is False


def test_nested_codex_runner_requires_the_explicit_opt_in_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("YIGDESK_NESTED_CODEX_ENABLED", "1")

    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_runner=FakeCodexRunner(),
    )

    agent = app.test_client().get("/api/state").get_json()["agent"]
    assert agent["available"] is True
    assert agent["mode"] == "codex-mcp"


def test_council_status_requires_a_bound_session_without_starting_codex(tmp_path):
    _, client = make_client(tmp_path)

    response = client.get("/api/council-status")

    assert response.status_code == 200
    status = response.get_json()
    assert status["mode"] == "codex-work"
    assert status["status"] == "unbound"
    assert status["expected_call_count"] == 15
    assert set(status["roles"]) == {
        "finance_analyst",
        "sales_advocate",
        "risk_challenger",
        "decision_optimizer",
    }


def test_agent_run_returns_engine_packet_and_verified_codex_trace(tmp_path):
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_enabled=True,
        agent_runner=FakeCodexRunner(),
    )
    client = app.test_client()
    headers = agent_run_headers(client)

    created = client.post("/api/agent-runs", json={}, headers=headers)
    assert created.status_code == 202
    run_id = created.get_json()["run_id"]

    run = None
    for _ in range(50):
        run = client.get(f"/api/agent-runs/{run_id}").get_json()
        if run["status"] == "completed":
            break
        time.sleep(0.01)

    assert run["status"] == "completed"
    assert run["stage"] == "review_ready"
    assert run["packet"]["packet_id"] == run["agent"]["answer"]["packet_id"]
    assert run["agent"]["verified"] is True
    assert run["agent"]["tool_calls"] == [
        "get_deal_context",
        "preview_consequence",
        "inspect_evidence",
    ]
    assert run["progress"] == [
        {"phase": "starting_codex", "elapsed_ms": 0},
        {"phase": "starting_codex", "elapsed_ms": 4},
        {"phase": "calling_yigdesk_tools", "elapsed_ms": 8},
        {"phase": "verifying_result", "elapsed_ms": 11},
    ]
    assert "sensitive" not in str(run)
    limited = client.post("/api/agent-runs", json={}, headers=headers)
    assert limited.status_code == 429
    assert limited.get_json()["code"] == "AGENT_RATE_LIMITED"


def test_agent_run_uses_one_immutable_revision_while_live_state_changes(tmp_path):
    runner = BlockingCodexRunner()
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_enabled=True,
        agent_runner=runner,
    )
    client = app.test_client()
    agent_headers = agent_run_headers(client)

    created = client.post("/api/agent-runs", json={}, headers=agent_headers)
    assert created.status_code == 202
    run_id = created.get_json()["run_id"]
    revision_id = created.get_json()["revision_id"]
    assert runner.started.wait(timeout=1)
    pending = client.get(f"/api/agent-runs/{run_id}").get_json()
    assert pending["stage"] == "starting_codex"
    assert pending["progress"] == [{"phase": "starting_codex", "elapsed_ms": 0}]

    reset = client.post("/api/reset", json={"scenario_id": "hold"})
    assert reset.status_code == 200
    headers = {"X-Yigdesk-Revision": revision_id}
    context = client.get("/api/state", headers=headers).get_json()
    packet = client.post("/api/analyze", json={}, headers=headers).get_json()["packet"]
    evidence = client.get(
        "/api/inspect",
        query_string={"address": "Deal Model!B4"},
        headers=headers,
    ).get_json()

    assert context["scenario_id"] == "ready"
    assert packet["consequence"]["verdict"] == "READY FOR CFO"
    assert {
        context["revision"]["source_fingerprint"],
        packet["source_fingerprint"],
        evidence["source_fingerprint"],
    } == {created.get_json()["source_fingerprint"]}
    assert {
        context["revision"]["packet_id"],
        packet["packet_id"],
        evidence["packet_id"],
    } == {created.get_json()["packet_id"]}
    assert {
        context["revision"]["revision_id"],
        packet["revision_id"],
        evidence["revision_id"],
    } == {revision_id}

    runner.release.set()
    for _ in range(50):
        run = client.get(f"/api/agent-runs/{run_id}").get_json()
        if run["status"] == "completed":
            break
        time.sleep(0.01)
    assert run["status"] == "completed"


def test_agent_snapshot_parses_packet_and_workbook_from_one_captured_byte_revision(
    tmp_path, monkeypatch
):
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_enabled=True,
        agent_runner=FakeCodexRunner(),
    )
    client = app.test_client()
    state = app.config["YIGDESK_STATE"]
    headers = agent_run_headers(client)
    original_workbook_snapshot = app_module.workbook_snapshot
    mutated = False

    def mutate_live_workbook_before_snapshot_parse(path):
        nonlocal mutated
        if not mutated:
            mutated = True
            create_workbook(state.workbook_path, state.scenarios["hold"])
        return original_workbook_snapshot(path)

    monkeypatch.setattr(
        app_module,
        "workbook_snapshot",
        mutate_live_workbook_before_snapshot_parse,
    )

    created = client.post("/api/agent-runs", json={}, headers=headers)

    assert created.status_code == 202
    revision_id = created.get_json()["revision_id"]
    headers = {"X-Yigdesk-Revision": revision_id}
    context = client.get("/api/state", headers=headers).get_json()
    packet = client.post("/api/analyze", json={}, headers=headers).get_json()["packet"]
    cells = {cell["address"]: cell for cell in context["workbook"]["cells"]}
    assert mutated is True
    assert packet["consequence"]["verdict"] == "READY FOR CFO"
    assert cells["Deal Inputs!B4"]["value"] == "$480k"
    assert context["workbook"]["fingerprint"] == packet["source_fingerprint"]


def test_real_codex_is_unavailable_when_server_binds_publicly(tmp_path, monkeypatch):
    monkeypatch.setenv("HOST", "0.0.0.0")
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_enabled=True,
        agent_runner=FakeCodexRunner(),
    )
    client = app.test_client()

    assert client.get("/api/state").get_json()["agent"]["available"] is False
    assert client.post("/api/agent-runs", json={}).status_code == 503


def test_agent_failure_response_never_exposes_internal_codex_diagnostics(tmp_path):
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_enabled=True,
        agent_runner=FailingCodexRunner(),
    )
    client = app.test_client()
    headers = agent_run_headers(client)

    created = client.post("/api/agent-runs", json={}, headers=headers)
    run_id = created.get_json()["run_id"]
    run = None
    for _ in range(50):
        run = client.get(f"/api/agent-runs/{run_id}").get_json()
        if run["status"] == "failed":
            break
        time.sleep(0.01)

    assert run["error"] == {
        "code": "AGENT_EXECUTION_FAILED",
        "message": "Codex did not produce a verified result.",
    }
    assert run["failure_phase"] == "calling_yigdesk_tools"
    assert run["elapsed_ms"] == 23
    assert run["progress"] == [
        {"phase": "starting_codex", "elapsed_ms": 0},
        {"phase": "starting_codex", "elapsed_ms": 5},
        {"phase": "calling_yigdesk_tools", "elapsed_ms": 22},
    ]
    assert "private" not in str(run)


def test_verification_failure_reports_the_verifying_phase_without_raw_detail(tmp_path):
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_enabled=True,
        agent_runner=VerificationFailingRunner(),
    )
    client = app.test_client()

    created = client.post(
        "/api/agent-runs",
        json={},
        headers=agent_run_headers(client),
    )
    run_id = created.get_json()["run_id"]
    run = None
    for _ in range(50):
        run = client.get(f"/api/agent-runs/{run_id}").get_json()
        if run["status"] == "failed":
            break
        time.sleep(0.01)

    assert run["stage"] == "rejected"
    assert run["failure_phase"] == "verifying_result"
    assert run["elapsed_ms"] == 0
    assert run["error"] == {
        "code": "AGENT_EVIDENCE_MISMATCH",
        "message": "Codex output did not pass Yigdesk verification.",
    }
    assert "sensitive" not in str(run)


def test_agent_run_requires_same_origin_token_and_loopback_request(tmp_path):
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_enabled=True,
        agent_runner=BlockingCodexRunner(),
    )
    client = app.test_client()
    headers = agent_run_headers(client)

    missing_token = client.post("/api/agent-runs", json={})
    remote_request = client.post(
        "/api/agent-runs",
        json={},
        headers=headers,
        environ_base={"REMOTE_ADDR": "203.0.113.9"},
    )
    public_host = client.post(
        "/api/agent-runs",
        json={},
        headers=headers,
        base_url="http://demo.example",
    )

    assert missing_token.status_code == 403
    assert missing_token.get_json()["code"] == "AGENT_REQUEST_FORBIDDEN"
    assert remote_request.status_code == 403
    assert public_host.status_code == 403
    assert app.config["YIGDESK_AGENT_RUNS"].active_id is None


def test_uploaded_workbook_drives_live_scenario_and_source_proof(tmp_path):
    app, client = make_client(tmp_path)
    upload_path = generate(tmp_path / "northwind-upload.xlsx")
    source_bytes = upload_path.read_bytes()

    response = client.post(
        "/api/upload",
        data={
            "workbook": (BytesIO(source_bytes), "northwind-upload.xlsx"),
            "requested_discount_pct": "2",
            "margin_floor_pct": "30",
            "current_discount_pct": "0",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    bound = load_active_session(tmp_path / "runtime")
    assert payload["scenario_id"].startswith("bound-")
    assert payload["session"] == {
        "protocol_version": "yigdesk-session/v1",
        "session_id": bound.session_id,
        "revision_id": bound.manifest["revision_id"],
    }
    assert payload["revision"]["revision_id"] == bound.manifest["revision_id"]
    assert payload["scenario"]["list_arr_k"] == "14658.2"
    assert payload["scenario"]["cogs_k"] == "10031.0"
    assert payload["source"]["kind"] == "uploaded-synthetic-xlsx"
    assert payload["source"]["extraction"]["revenue_cells"][0] == "P&L Report!D5"

    packet = client.post("/api/analyze").get_json()["packet"]
    assert packet["source_fingerprint"] == payload["source"]["sha256"]
    assert packet["projection_fingerprint"] != packet["source_fingerprint"]
    assert packet["analysis_bytes_unchanged"] is True
    assert packet["consequence"]["display"] == {
        "net_arr": "$14,365k",
        "arr_impact": "-$293k",
        "gross_profit": "$4,334k",
        "gross_margin": "30.2%",
        "headroom": "0.2%",
        "requested_discount": "2.0%",
    }
    assert app.config["YIGDESK_STATE"].active_source_path == bound.source_path
    assert bound.source_path.read_bytes() == source_bytes
    assert not bound.audit_path.exists()


def test_council_status_tracks_codex_work_audit_on_the_uploaded_revision(tmp_path):
    app, client = make_client(tmp_path)
    upload_path = generate(tmp_path / "northwind-council.xlsx")
    uploaded = client.post(
        "/api/upload",
        data={
            "workbook": (BytesIO(upload_path.read_bytes()), "northwind-council.xlsx"),
            "requested_discount_pct": "2",
            "margin_floor_pct": "30",
        },
        content_type="multipart/form-data",
    ).get_json()
    bound = load_active_session(tmp_path / "runtime")

    idle = client.get("/api/council-status")
    assert idle.status_code == 200
    assert idle.get_json()["status"] == "idle"

    revision = uploaded["revision"]
    events = [
        {
            "actor": "finance_analyst",
            "tool": "get_deal_context",
            "ok": True,
            **revision,
        },
        {
            "actor": "finance_analyst",
            "tool": "find_feasible_boundary",
            "ok": True,
            "step_pct": "0.01",
            "largest_safe_step_pct": "2.23",
            **revision,
        },
    ]
    bound.audit_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    running = client.get("/api/council-status")
    assert running.status_code == 200
    payload = running.get_json()
    assert payload["status"] == "running"
    assert payload["revision"] == revision
    assert payload["roles"]["finance_analyst"]["completed"] == 2
    assert "step_pct" not in str(payload)


def test_proposal_tools_share_revision_and_catch_the_display_rounding_trap(tmp_path):
    _, client = make_client(tmp_path)
    upload_path = generate(tmp_path / "northwind-tools.xlsx")
    uploaded = client.post(
        "/api/upload",
        data={
            "workbook": (BytesIO(upload_path.read_bytes()), "northwind-tools.xlsx"),
            "requested_discount_pct": "2",
            "margin_floor_pct": "30",
        },
        content_type="multipart/form-data",
    ).get_json()

    evaluated = client.post(
        "/api/proposals/evaluate", json={"requested_discount_pct": "2.24"}
    ).get_json()
    compared = client.post(
        "/api/proposals/compare", json={"discounts_pct": ["2", "2.23", "2.24"]}
    ).get_json()
    boundary = client.get("/api/proposals/boundary?step_pct=0.01").get_json()
    stressed = client.post(
        "/api/proposals/stress-test",
        json={"requested_discount_pct": "2", "cogs_change_pct": "5"},
    ).get_json()
    missing = client.get("/api/evidence/missing").get_json()

    revisions = {
        tuple(sorted(result["revision"].items()))
        for result in (evaluated, compared, boundary, stressed, missing)
    }
    assert len(revisions) == 1
    assert evaluated["revision"] == uploaded["revision"]
    assert evaluated["proposal"]["display"]["gross_margin"] == "30.0%"
    assert evaluated["proposal"]["constraint_pass"] is False
    assert boundary["boundary"]["largest_safe_step_pct"] == "2.23"
    assert compared["comparison"]["highest_feasible_proposal_pct"] == "2.23"
    assert stressed["stress_test"]["proposal"]["verdict"] == "HOLD"
    assert missing["status"] == "COMPLETE"


def test_upload_rejects_non_xlsx_without_replacing_current_revision(tmp_path):
    _, client = make_client(tmp_path)
    before = client.get("/api/state").get_json()["revision"]

    response = client.post(
        "/api/upload",
        data={"workbook": (BytesIO(b"not an xlsx"), "notes.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "UNSUPPORTED_FILE_TYPE"
    assert client.get("/api/state").get_json()["revision"] == before
