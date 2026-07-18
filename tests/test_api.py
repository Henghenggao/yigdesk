from pathlib import Path

from yigdesk.app import create_app
from yigdesk.workbook import fingerprint


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
    assert "No business write-back capability" in html
    assert "proprietary Yigrid kernel not included" in html
