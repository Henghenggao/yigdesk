import asyncio, os, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]

async def _roundtrip(ledger, scenario_dir):
    params = StdioServerParameters(command=sys.executable, args=["-m","yigdesk.blackboard_mcp"],
        env={**os.environ, "YIGDESK_LEDGER": ledger, "YIGDESK_SCENARIO": scenario_dir})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            await s.call_tool("open_decision", {"decision_id":"d1","question":"Approve 12%?"})
            await s.call_tool("propose_candidate", {"decision_id":"d1","candidate_id":"c1","overrides":{"discount":12}})
            board = (await s.call_tool("read_board", {})).structuredContent
            return names, board

def test_lists_six_ops_and_prices_candidate(tmp_path):
    names, board = asyncio.run(_roundtrip(str(tmp_path/"b.jsonl"), str(ROOT/"data/scenarios/discount_approval")))
    assert set(names) == {"open_decision","propose_candidate","post_claim","read_board","cast_approval","request_resolve"}
    assert board["decisions"]["d1"]["candidates"]["c1"]["consequence"]["verdict"] == "ok"
