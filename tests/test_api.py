from pathlib import Path
import threading
import time

import yigdesk.app as app_module
from yigdesk.agent import AgentExecutionError
from yigdesk.app import create_app
from yigdesk.workbook import create_workbook, fingerprint


class FakeCodexRunner:
    model = "gpt-test"

    def run(self, packet, *, base_url, revision_id=None):
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

    def run(self, packet, *, base_url, revision_id=None):
        raise AgentExecutionError("sensitive path C:\\private\\codex-auth.json")


class BlockingCodexRunner(FakeCodexRunner):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.revision_id = None

    def run(self, packet, *, base_url, revision_id=None):
        self.revision_id = revision_id
        self.started.set()
        assert self.release.wait(timeout=2)
        return super().run(packet, base_url=base_url, revision_id=revision_id)


def make_client(tmp_path: Path):
    app = create_app(runtime_dir=tmp_path / "runtime")
    app.config.update(TESTING=True)
    return app, app.test_client()


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

    assert state["agent"] == {"available": False, "mode": "local-preview", "model": None}
    assert response.status_code == 503
    assert response.get_json()["code"] == "CODEX_UNAVAILABLE"


def test_agent_run_returns_engine_packet_and_verified_codex_trace(tmp_path):
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        agent_enabled=True,
        agent_runner=FakeCodexRunner(),
    )
    client = app.test_client()

    created = client.post("/api/agent-runs", json={})
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
    limited = client.post("/api/agent-runs", json={})
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

    created = client.post("/api/agent-runs", json={})
    assert created.status_code == 202
    run_id = created.get_json()["run_id"]
    revision_id = created.get_json()["revision_id"]
    assert runner.started.wait(timeout=1)

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

    created = client.post("/api/agent-runs", json={})

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

    created = client.post("/api/agent-runs", json={})
    run_id = created.get_json()["run_id"]
    run = None
    for _ in range(50):
        run = client.get(f"/api/agent-runs/{run_id}").get_json()
        if run["status"] == "failed":
            break
        time.sleep(0.01)

    assert run["error"] == {
        "code": "AgentExecutionError",
        "message": "Codex did not produce a verified result.",
    }
    assert "private" not in str(run)
