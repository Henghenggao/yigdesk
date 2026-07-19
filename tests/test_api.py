from pathlib import Path
from io import BytesIO

from yigdesk.app import create_app
from yigdesk.session import load_active_session
from scripts.generate_sample_workbook import generate


def make_client(tmp_path: Path):
    app = create_app(runtime_dir=tmp_path / "runtime")
    app.config.update(TESTING=True)
    return app, app.test_client()


def test_health_reports_public_preview_mode(tmp_path):
    _, client = make_client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "mode": "public-preview"}


def test_every_response_carries_locked_down_security_headers(tmp_path):
    _, client = make_client(tmp_path)

    response = client.get("/api/health")

    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_static_surface_exposes_neutral_components_and_public_scope(tmp_path):
    _, client = make_client(tmp_path)
    html = client.get("/").get_data(as_text=True)
    assert "Yigdesk" in html
    assert "<yig-grid" in html
    assert "<yig-model-inspector" in html
    assert "No business write-back" in html
    assert "proprietary Yigrid kernel not included" in html


def test_state_returns_the_engine_free_session_shape(tmp_path):
    _, client = make_client(tmp_path)

    state = client.get("/api/state").get_json()

    # The synthetic fixture is unbound: session is None and no engine fields leak.
    assert state["scenario_id"] == "ready"
    assert state["session"] is None
    assert set(state) == {"scenario_id", "scenario", "source", "session"}
    assert "revision" not in state
    assert "workbook" not in state
    assert "agent" not in state


def test_reset_rejects_non_object_json_instead_of_raising(tmp_path):
    _, client = make_client(tmp_path)

    response = client.post("/api/reset", json=[])

    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_REQUEST"


def test_reset_switches_the_synthetic_fixture_scenario(tmp_path):
    _, client = make_client(tmp_path)

    hold = client.post("/api/reset", json={"scenario_id": "hold"})

    assert hold.status_code == 200
    assert hold.get_json()["scenario_id"] == "hold"
    assert client.get("/api/state").get_json()["scenario_id"] == "hold"


def test_reset_rejects_an_unknown_scenario(tmp_path):
    _, client = make_client(tmp_path)

    response = client.post("/api/reset", json={"scenario_id": "nope"})

    assert response.status_code == 400
    assert response.get_json()["code"] == "UNKNOWN_SCENARIO"


def test_commercial_capability_routes_are_absent(tmp_path):
    app, client = make_client(tmp_path)
    registered = {rule.rule for rule in app.url_map.iter_rules()}
    for route in ("/api/approve", "/api/writeback", "/api/vault", "/api/attestation", "/api/audit"):
        assert route not in registered
        assert client.post(route, json={}).status_code in {404, 405}


def test_decision_surface_routes_are_gone(tmp_path):
    app, client = make_client(tmp_path)
    registered = {rule.rule for rule in app.url_map.iter_rules()}
    for route in (
        "/api/analyze",
        "/api/inspect",
        "/api/council-status",
        "/api/agent-runs",
        "/api/proposals/evaluate",
        "/api/proposals/compare",
        "/api/proposals/boundary",
        "/api/proposals/stress-test",
        "/api/evidence/missing",
    ):
        assert route not in registered


def test_uploaded_workbook_binds_the_live_session_and_source_proof(tmp_path):
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
    assert payload["scenario"]["list_arr_k"] == "14658.2"
    assert payload["scenario"]["cogs_k"] == "10031.0"
    assert payload["source"]["kind"] == "uploaded-synthetic-xlsx"
    assert payload["source"]["extraction"]["revenue_cells"][0] == "P&L Report!D5"
    # The upload is copied byte-for-byte into the immutable session source.
    assert app.config["YIGDESK_STATE"].active_source_path == bound.source_path
    assert bound.source_path.read_bytes() == source_bytes
    assert not bound.audit_path.exists()


def test_reset_after_upload_stays_on_the_fixture_despite_the_active_pointer(tmp_path):
    _, client = make_client(tmp_path)
    upload_path = generate(tmp_path / "northwind-reset.xlsx")
    client.post(
        "/api/upload",
        data={
            "workbook": (BytesIO(upload_path.read_bytes()), "northwind-reset.xlsx"),
            "requested_discount_pct": "2",
            "margin_floor_pct": "30",
        },
        content_type="multipart/form-data",
    )
    assert client.get("/api/state").get_json()["session"] is not None

    reset = client.post("/api/reset", json={"scenario_id": "ready"})

    assert reset.status_code == 200
    assert reset.get_json()["session"] is None
    # The next request must NOT silently re-bind the still-active session pointer.
    followup = client.get("/api/state").get_json()
    assert followup["session"] is None
    assert followup["scenario_id"] == "ready"


def test_upload_rejects_non_xlsx_without_replacing_current_state(tmp_path):
    _, client = make_client(tmp_path)
    before = client.get("/api/state").get_json()

    response = client.post(
        "/api/upload",
        data={"workbook": (BytesIO(b"not an xlsx"), "notes.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "UNSUPPORTED_FILE_TYPE"
    assert client.get("/api/state").get_json() == before
