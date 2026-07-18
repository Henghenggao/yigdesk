from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest
from werkzeug.serving import make_server

from yigdesk.app import create_app
from yigdesk.mcp_tools import ToolCallError, YigdeskToolClient


@contextmanager
def live_demo(tmp_path):
    app = create_app(runtime_dir=tmp_path)
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_tool_client_returns_decision_ready_read_only_evidence(tmp_path):
    with live_demo(tmp_path) as base_url:
        client = YigdeskToolClient(base_url)

        context = client.get_deal_context()
        packet = client.preview_consequence()
        evidence = client.inspect_evidence("Deal Model!B4")

    assert context["scenario_id"] == "ready"
    assert context["request"]["subject"] == "Northstar renewal — approval for 12%"
    assert context["workbook"]["mode"] == "read-only"
    assert packet["packet_id"].startswith("cpkt-")
    assert packet["consequence"]["verdict"] == "READY FOR CFO"
    assert packet["analysis_bytes_unchanged"] is True
    assert evidence["address"] == "Deal Model!B4"
    assert evidence["value_verified"] is True


def test_tool_client_translates_unknown_evidence_into_actionable_error(tmp_path):
    with live_demo(tmp_path) as base_url:
        client = YigdeskToolClient(base_url)

        with pytest.raises(ToolCallError, match="Choose an address returned"):
            client.inspect_evidence("Deal Model!Z99")
