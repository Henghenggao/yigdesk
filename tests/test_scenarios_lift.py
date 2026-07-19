import json, subprocess, sys
from pathlib import Path
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource

ROOT = Path(__file__).resolve().parents[1]

def _price(scn, action):
    d = ROOT/"data/scenarios"/scn
    model = json.loads((d/"model.json").read_text())
    src = ModelSource(d/model["workbook"], model["input_refs"])
    return ExpressionEvaluator(model).price(action, src)

def test_both_scenarios_price_with_same_engine():
    subprocess.run([sys.executable, str(ROOT/"scripts/build_scenarios.py")], check=True)
    assert _price("discount_approval", {"overrides":{"discount":12}}).verdict == "ok"
    assert _price("saas_margin", {"overrides":{"expansion_pct":20}}).verdict in ("ok","hold")
