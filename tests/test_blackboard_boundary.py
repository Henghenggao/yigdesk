import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
NEUTRAL = (list((ROOT/"yigdesk/core").glob("*.py"))
           + list((ROOT/"yigdesk/evaluator").glob("*.py"))
           + [ROOT/"yigdesk/blackboard_mcp.py"])

def test_blackboard_core_is_domain_neutral():
    forbidden = re.compile(r"discount|\barr\b|margin|cogs|cfo|saas|mrr|revenue|\bfinance\b", re.IGNORECASE)
    hits = {str(p.relative_to(ROOT)): sorted(set(forbidden.findall(p.read_text(encoding='utf-8'))))
            for p in NEUTRAL if p.exists() and forbidden.search(p.read_text(encoding='utf-8'))}
    assert not hits, f"business vocabulary leaked into the domain-neutral blackboard: {hits}"

def test_blackboard_never_writes_source():
    for f in ["yigdesk/core/blackboard.py", "yigdesk/blackboard_mcp.py"]:
        txt = (ROOT/f).read_text(encoding="utf-8")
        assert ".save(" not in txt and "write_cell" not in txt, f"{f} appears to write source"
