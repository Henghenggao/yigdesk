from __future__ import annotations
import ast, hashlib, json, operator
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation, DivisionByZero, localcontext
from yigdesk.core.model import Consequence, Metric

_EVAL_CODE_VERSION = "1"

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.USub: operator.neg}

def _eval(node, env):
    if isinstance(node, ast.Expression): return _eval(node.body, env)
    if isinstance(node, ast.Constant):  return Decimal(str(node.value))
    if isinstance(node, ast.Name):      return env[node.id]
    if isinstance(node, ast.BinOp):     return _OPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp):   return _OPS[type(node.op)](_eval(node.operand, env))
    raise ValueError(f"unsupported expression node: {type(node).__name__}")

def _q(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

_CMP = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}

class ExpressionEvaluator:
    """Deterministic evaluator whose model (metrics/formulas/constraints) is pure data."""
    def __init__(self, model: dict):
        self.model = model
        self.revision = "expr:v" + _EVAL_CODE_VERSION + ":" + hashlib.sha256(
            json.dumps(model, sort_keys=True).encode()).hexdigest()[:12]

    def ground(self, ref: str, source) -> bool:
        return source.exists(ref)

    def _compute(self, inputs: dict) -> dict[str, Decimal | None]:
        with localcontext() as ctx:
            ctx.prec = 28
            env = dict(inputs); out: dict[str, Decimal | None] = {}
            for spec in self.model["metrics"]:
                missing = [r for r in spec.get("requires", []) if r not in inputs]
                if missing:
                    out[spec["id"]] = None; continue
                try:
                    val = _eval(ast.parse(spec["formula"], mode="eval"), env)
                except (KeyError, InvalidOperation, DivisionByZero, ArithmeticError):
                    out[spec["id"]] = None; continue
                env[spec["id"]] = val; out[spec["id"]] = val
            return out

    def price(self, action: dict, source) -> Consequence:
        base = source.base_inputs()
        try:
            overrides = {k: Decimal(str(v)) for k, v in action.get("overrides", {}).items()}
        except (InvalidOperation, ValueError):
            metrics = [Metric(s["id"], s["label"], None, None, None, s.get("unit", ""))
                       for s in self.model["metrics"]]
            return Consequence("hold", metrics, list(self.model["input_refs"].values()), source.fingerprint)
        after_inputs = {**base, **overrides}
        before, after = self._compute(base), self._compute(after_inputs)
        metrics = [Metric(s["id"], s["label"],
                          None if after[s["id"]] is None else _q(after[s["id"]]),
                          None if before[s["id"]] is None else _q(before[s["id"]]),
                          None if after[s["id"]] is None else _q(after[s["id"]]),
                          s.get("unit", "")) for s in self.model["metrics"]]
        complete = all(after[s["id"]] is not None for s in self.model["metrics"])
        ok = complete and all(
            _CMP[c["op"]](after[c["metric"]], Decimal(str(c["value"])))
            for c in self.model.get("constraints", []))
        return Consequence("ok" if ok else "hold", metrics,
                           list(self.model["input_refs"].values()), source.fingerprint)
