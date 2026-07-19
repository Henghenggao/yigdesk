# Yigdesk Deterministic Blackboard — Backend Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the headless, deterministic decision-blackboard backend — an append-only ledger, a model-from-data evaluator, a deterministic gate, and an MCP surface — with zero business-scenario logic in code and zero association with any private engine.

**Architecture:** Non-deterministic contributors (LLM subagents + humans) write ops through the MCP surface; a domain-neutral Core folds the append-only op-log into a board projection, prices candidates and grounds claims through a pluggable Evaluator, and closes decisions with a deterministic gate. The only committed artifact is a `DecisionRecord`; source workbooks are read-only. Scenarios are pure data + config.

**Tech Stack:** Python 3.11, stdlib `dataclasses`/`ast`/`decimal`/`json`, `openpyxl` (read-only cell access), `mcp` (FastMCP), `pytest`. UI is Plan 2 (not in this plan).

**Design spec:** `docs/superpowers/specs/2026-07-19-yigdesk-decision-blackboard-design.md`

---

## File Structure

```
yigdesk/
  core/
    ops.py          # Op dataclass, op kinds, canonical JSON (de)serialization
    ledger.py       # append-only JSONL ledger: append(op)->Op, read()->[Op], next_seq()
    model.py        # Consequence, Decision, Candidate, Claim, Approval, Policy, DecisionRecord, Board
    projection.py   # fold([Op]) -> Board
    gate.py         # resolve(decision, policy) -> DecisionRecord | Pending  (pure, deterministic)
    blackboard.py   # Blackboard facade: submit(kind,...) validates+grounds+prices+appends; project(); request_resolve()
  evaluator/
    base.py         # Evaluator Protocol: price(action, source)->Consequence ; ground(ref, source)->bool
    model_source.py # ModelSource: base inputs from workbook cells + fingerprint + ref existence (reuses workbook.py)
    expression.py   # ExpressionEvaluator: model-from-data metrics/constraints, safe Decimal eval, HOLD
  workbook.py       # (REUSED, trimmed) read-only cell read + fingerprint
  mcp_server.py     # (REWRITTEN) FastMCP: open_decision/propose_candidate/post_claim/read_board/cast_approval/request_resolve
data/
  scenarios/
    discount_approval/{deal.xlsx, model.json, policy.json}
    saas_margin/{margin.xlsx, model.json, policy.json}      # 2nd scenario, data-only, proves no-lock
tests/
  test_ops.py test_ledger.py test_projection.py test_expression_evaluator.py
  test_gate.py test_blackboard.py test_determinism_conformance.py test_mcp_protocol.py test_boundary.py
```

> **REVISION 2026-07-19 (see spec §13):** This plan is now **purely ADDITIVE**. The repo
> contains a Codex Council/A2A subsystem being **integrated & reused**, so nothing is deleted
> or migrated here — the existing council + demo stay green throughout. **Task 0's demolition
> is cancelled**; scaffolding is folded into the tasks that need it. The new blackboard MCP
> server is a **new module `yigdesk/blackboard_mcp.py`** (the old `mcp_server.py` is untouched
> until Plan 2). `workbook.py` gets `read_cell` **added** (not replaced). `engine.py`,
> `importer.py`, `session.py`, `benchmark.py`, `agent.py` are **untouched** in Plan 1. All
> deletion + council migration is **Plan 2** (written after Plan 1 lands).

---

## Task 0: Scaffold package, remove obsolete code

**Files:**
- Create: `yigdesk/core/__init__.py`, `yigdesk/evaluator/__init__.py` (empty)
- Delete: `yigdesk/engine.py`, `yigdesk/mcp_tools.py`, `yigdesk/cli.py`, `docs/OPEN_CORE_BOUNDARY.md`, `tests/test_api.py`, `tests/test_engine.py`, `tests/test_mcp_tools.py`, `tests/test_mcp_protocol.py`

- [ ] **Step 1: Create empty package dirs**

```bash
mkdir -p yigdesk/core yigdesk/evaluator data/scenarios
: > yigdesk/core/__init__.py
: > yigdesk/evaluator/__init__.py
```

- [ ] **Step 2: Delete obsolete files (no traces)**

```bash
git rm yigdesk/engine.py yigdesk/mcp_tools.py yigdesk/cli.py docs/OPEN_CORE_BOUNDARY.md \
       tests/test_api.py tests/test_engine.py tests/test_mcp_tools.py tests/test_mcp_protocol.py
```

- [ ] **Step 3: Verify nothing else imports the deleted modules**

Run: `grep -rn "engine\|mcp_tools\|OPEN_CORE" yigdesk/ tests/ --include=*.py`
Expected: no references (any that remain will be rewritten in later tasks that touch those files).

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "chore: scaffold core/evaluator packages, remove single-flow demo code"
```

---

## Task 1: Op model + canonical serialization

**Files:**
- Create: `yigdesk/core/ops.py`
- Test: `tests/test_ops.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ops.py
from yigdesk.core.ops import Op, op_to_json, op_from_json, KINDS, PROPOSE_CANDIDATE

def test_op_roundtrip_is_canonical_and_stable():
    op = Op(seq=3, kind=PROPOSE_CANDIDATE, actor="agent:a", role="proposer",
            payload={"b": 2, "a": 1}, base_seq=2)
    line = op_to_json(op)
    assert line == '{"actor":"agent:a","base_seq":2,"kind":"propose_candidate","payload":{"a":1,"b":2},"role":"proposer","seq":3}'
    assert op_from_json(line) == op

def test_kinds_are_the_six_ops_plus_resolved():
    assert KINDS == {"open_decision","propose_candidate","post_claim","cast_approval","request_resolve","resolved"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ops.py -v`
Expected: FAIL with `ModuleNotFoundError: yigdesk.core.ops`

- [ ] **Step 3: Write minimal implementation**

```python
# yigdesk/core/ops.py
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any

OPEN_DECISION="open_decision"; PROPOSE_CANDIDATE="propose_candidate"; POST_CLAIM="post_claim"
CAST_APPROVAL="cast_approval"; REQUEST_RESOLVE="request_resolve"; RESOLVED="resolved"
KINDS={OPEN_DECISION,PROPOSE_CANDIDATE,POST_CLAIM,CAST_APPROVAL,REQUEST_RESOLVE,RESOLVED}

@dataclass(frozen=True)
class Op:
    seq: int
    kind: str
    actor: str
    role: str
    payload: dict[str, Any]
    base_seq: int = 0

def op_to_json(op: Op) -> str:
    return json.dumps({"seq":op.seq,"kind":op.kind,"actor":op.actor,"role":op.role,
                       "base_seq":op.base_seq,"payload":op.payload},
                      sort_keys=True, separators=(",", ":"))

def op_from_json(line: str) -> Op:
    d=json.loads(line)
    return Op(d["seq"], d["kind"], d["actor"], d["role"], d["payload"], d.get("base_seq",0))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ops.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add yigdesk/core/ops.py tests/test_ops.py && git commit -m "feat(core): op model with canonical serialization"
```

---

## Task 2: Append-only JSONL ledger

**Files:**
- Create: `yigdesk/core/ledger.py`
- Test: `tests/test_ledger.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py
from yigdesk.core.ledger import Ledger
from yigdesk.core.ops import OPEN_DECISION

def test_append_assigns_monotonic_seq_and_persists(tmp_path):
    led = Ledger(tmp_path / "board.jsonl")
    a = led.append(OPEN_DECISION, "human:cfo", "owner", {"question": "q1"})
    b = led.append(OPEN_DECISION, "human:cfo", "owner", {"question": "q2"})
    assert (a.seq, b.seq) == (1, 2)
    assert [o.payload["question"] for o in Ledger(tmp_path / "board.jsonl").read()] == ["q1", "q2"]

def test_read_of_empty_ledger_is_empty(tmp_path):
    assert Ledger(tmp_path / "empty.jsonl").read() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: yigdesk.core.ledger`

- [ ] **Step 3: Write minimal implementation**

```python
# yigdesk/core/ledger.py
from __future__ import annotations
from pathlib import Path
from .ops import Op, op_to_json, op_from_json

class Ledger:
    """Append-only op-log. The single writer; assigns monotonic seq."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def read(self) -> list[Op]:
        text = self.path.read_text(encoding="utf-8")
        return [op_from_json(l) for l in text.splitlines() if l.strip()]

    def next_seq(self) -> int:
        ops = self.read()
        return ops[-1].seq + 1 if ops else 1

    def append(self, kind, actor, role, payload, base_seq=0) -> Op:
        op = Op(self.next_seq(), kind, actor, role, payload, base_seq)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(op_to_json(op) + "\n")
        return op
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ledger.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add yigdesk/core/ledger.py tests/test_ledger.py && git commit -m "feat(core): append-only JSONL ledger"
```

---

## Task 3: Domain model dataclasses

**Files:**
- Create: `yigdesk/core/model.py`

- [ ] **Step 1: Write the model (no behavior yet, so a smoke test)**

```python
# tests/test_model_smoke.py
from yigdesk.core.model import Consequence, Metric, Board

def test_consequence_and_board_construct():
    c = Consequence(verdict="ok", metrics=[Metric("net_arr","Net ARR","880","900","880","$k")],
                    evidence_refs=["Deal Inputs!B2"], fingerprint="abc")
    assert c.verdict == "ok" and Board(decisions={}).decisions == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_model_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: yigdesk.core.model`

- [ ] **Step 3: Write the dataclasses**

```python
# yigdesk/core/model.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class Metric:
    id: str; label: str; value: str | None; before: str | None; after: str | None; unit: str = ""

@dataclass(frozen=True)
class Consequence:
    verdict: str                      # "ok" | "hold"
    metrics: list[Metric]
    evidence_refs: list[str]
    fingerprint: str

@dataclass
class Candidate:
    id: str; author: str; action: dict[str, Any]
    consequence: Consequence | None = None
    status: str = "priced"            # "priced" | "hold"

@dataclass
class Claim:
    id: str; author: str; type: str; target: str; body: str
    grounded_refs: list[str] = field(default_factory=list)
    status: str = "grounded"          # "grounded" | "rejected"

@dataclass
class Approval:
    actor: str; role: str; verdict: str; scope: str   # verdict: approve|hold|reject

@dataclass
class DecisionRecord:
    decision_id: str; chosen_candidate_id: str; closed_by: str; rationale: str
    evidence_refs: list[str]; approvals: list[dict]; evaluator_revision: str
    source_fingerprint: str; seq: int

@dataclass
class Decision:
    id: str; question: str; decision_type: str; policy: dict[str, Any]
    status: str = "open"              # "open" | "resolved" | "held"
    candidates: dict[str, Candidate] = field(default_factory=dict)
    claims: dict[str, Claim] = field(default_factory=dict)
    approvals: list[Approval] = field(default_factory=list)
    resolution: DecisionRecord | None = None

@dataclass
class Board:
    decisions: dict[str, Decision] = field(default_factory=dict)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_model_smoke.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add yigdesk/core/model.py tests/test_model_smoke.py && git commit -m "feat(core): decision/candidate/claim/consequence model"
```

---

## Task 4: Projection (fold op-log → Board)

**Files:**
- Create: `yigdesk/core/projection.py`
- Test: `tests/test_projection.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_projection.py
from yigdesk.core.ops import Op, OPEN_DECISION, PROPOSE_CANDIDATE, CAST_APPROVAL
from yigdesk.core.projection import fold

def _ops():
    return [
        Op(1, OPEN_DECISION, "human:cfo", "owner", {"decision_id":"d1","question":"Approve 12%?","decision_type":"discount","policy":{}}),
        Op(2, PROPOSE_CANDIDATE, "agent:a", "proposer", {"decision_id":"d1","candidate_id":"c1","action":{"overrides":{"discount":12}}}),
        Op(3, CAST_APPROVAL, "human:cfo", "cfo", {"decision_id":"d1","verdict":"approve","scope":"c1"}),
    ]

def test_fold_builds_decision_with_candidate_and_approval():
    board = fold(_ops())
    d = board.decisions["d1"]
    assert d.question == "Approve 12%?" and "c1" in d.candidates
    assert d.approvals[0].verdict == "approve"

def test_fold_is_pure_same_input_same_output():
    assert fold(_ops()) == fold(_ops())
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_projection.py -v`
Expected: FAIL with `ModuleNotFoundError: yigdesk.core.projection`

- [ ] **Step 3: Write minimal implementation**

```python
# yigdesk/core/projection.py
from __future__ import annotations
from .model import Board, Decision, Candidate, Claim, Approval, DecisionRecord
from .ops import OPEN_DECISION, PROPOSE_CANDIDATE, POST_CLAIM, CAST_APPROVAL, RESOLVED

def fold(ops) -> Board:
    board = Board()
    for op in ops:                       # ops are already in seq order from the ledger
        p = op.payload; d = board.decisions.get(p.get("decision_id"))
        if op.kind == OPEN_DECISION:
            board.decisions[p["decision_id"]] = Decision(
                id=p["decision_id"], question=p["question"],
                decision_type=p["decision_type"], policy=p.get("policy", {}))
        elif op.kind == PROPOSE_CANDIDATE and d:
            d.candidates[p["candidate_id"]] = Candidate(
                id=p["candidate_id"], author=op.actor, action=p["action"],
                consequence=p.get("consequence"), status=p.get("status", "priced"))
        elif op.kind == POST_CLAIM and d:
            d.claims[p["claim_id"]] = Claim(
                id=p["claim_id"], author=op.actor, type=p["type"], target=p["target"],
                body=p.get("body",""), grounded_refs=p.get("grounded_refs", []),
                status=p.get("status", "grounded"))
        elif op.kind == CAST_APPROVAL and d:
            d.approvals.append(Approval(op.actor, op.role, p["verdict"], p["scope"]))
        elif op.kind == RESOLVED and d:
            d.resolution = DecisionRecord(**p["record"]); d.status = "resolved"
    return board
```

Note: `consequence`/`record` in payloads are plain dicts here; Task 8 stores the real objects via the blackboard. For projection equality in the test they are absent, which is fine.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_projection.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add yigdesk/core/projection.py tests/test_projection.py && git commit -m "feat(core): pure fold of op-log into board projection"
```

---

## Task 5: Evaluator interface + ModelSource

**Files:**
- Create: `yigdesk/evaluator/base.py`, `yigdesk/evaluator/model_source.py`
- Modify: `yigdesk/workbook.py` (keep only `read_cell` + `fingerprint`; delete the finance snapshot/inspect helpers)
- Test: `tests/test_model_source.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_source.py
from openpyxl import Workbook
from yigdesk.evaluator.model_source import ModelSource

def _wb(tmp_path):
    wb = Workbook(); ws = wb.active; ws.title = "Deal Inputs"
    ws["B2"] = 1000; ws["B3"] = 10
    p = tmp_path / "deal.xlsx"; wb.save(p); return p

def test_reads_named_inputs_and_reports_missing(tmp_path):
    src = ModelSource(_wb(tmp_path), {"list_arr":"Deal Inputs!B2","discount":"Deal Inputs!B3","cogs":"Deal Inputs!B4"})
    inputs = src.base_inputs()
    assert str(inputs["list_arr"]) == "1000" and str(inputs["discount"]) == "10"
    assert "cogs" not in inputs                     # empty cell -> missing (fail-closed input)
    assert src.exists("Deal Inputs!B2") and not src.exists("Deal Inputs!B4")
    assert len(src.fingerprint) == 64
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_model_source.py -v`
Expected: FAIL with `ModuleNotFoundError: yigdesk.evaluator.model_source`

- [ ] **Step 3: Trim `workbook.py` to the reusable read-only primitives**

Replace the whole file with:

```python
# yigdesk/workbook.py  (read-only cell access + fingerprint; scenario logic lives in data)
from __future__ import annotations
import hashlib
from decimal import Decimal
from pathlib import Path
import openpyxl

def fingerprint(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read_cell(path, ref: str):
    sheet, addr = ref.split("!", 1)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        v = wb[sheet][addr].value
    finally:
        wb.close()
    return None if v is None else Decimal(str(v))
```

- [ ] **Step 4: Write the Evaluator interface + ModelSource**

```python
# yigdesk/evaluator/base.py
from __future__ import annotations
from typing import Protocol
from yigdesk.core.model import Consequence

class Evaluator(Protocol):
    revision: str
    def price(self, action: dict, source) -> Consequence: ...
    def ground(self, ref: str, source) -> bool: ...
```

```python
# yigdesk/evaluator/model_source.py
from __future__ import annotations
from yigdesk.workbook import read_cell, fingerprint

class ModelSource:
    """Read-only source of base inputs (from workbook cells) + fingerprint + ref existence."""
    def __init__(self, path, input_refs: dict[str, str]):
        self.path = path
        self.input_refs = input_refs
        self.fingerprint = fingerprint(path)

    def base_inputs(self) -> dict:
        out = {}
        for name, ref in self.input_refs.items():
            v = read_cell(self.path, ref)
            if v is not None:
                out[name] = v
        return out

    def exists(self, ref: str) -> bool:
        return read_cell(self.path, ref) is not None
```

- [ ] **Step 5: Run to verify it passes, then commit**

Run: `python -m pytest tests/test_model_source.py -v`
Expected: PASS (1 passed)

```bash
git add yigdesk/evaluator/base.py yigdesk/evaluator/model_source.py yigdesk/workbook.py tests/test_model_source.py
git commit -m "feat(evaluator): generic Evaluator interface + read-only ModelSource"
```

---

## Task 6: ExpressionEvaluator (model-from-data, safe Decimal eval, HOLD)

**Files:**
- Create: `yigdesk/evaluator/expression.py`
- Test: `tests/test_expression_evaluator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_expression_evaluator.py
from openpyxl import Workbook
from yigdesk.evaluator.model_source import ModelSource
from yigdesk.evaluator.expression import ExpressionEvaluator

MODEL = {
    "input_refs": {"list_arr":"Deal Inputs!B2","discount":"Deal Inputs!B3","cogs":"Deal Inputs!B4","floor":"Deal Inputs!B5"},
    "metrics": [
        {"id":"net_arr","label":"Net ARR","formula":"list_arr * (1 - discount/100)","unit":"$k"},
        {"id":"gross_margin","label":"Gross margin","formula":"(net_arr - cogs) / net_arr * 100","requires":["cogs"],"unit":"%"},
        {"id":"headroom","label":"Headroom","formula":"gross_margin - floor","requires":["cogs"],"unit":"pt"},
    ],
    "constraints": [{"metric":"headroom","op":">=","value":0}],
}

def _src(tmp_path, cogs=True):
    wb = Workbook(); ws = wb.active; ws.title = "Deal Inputs"
    ws["B2"]=1000; ws["B3"]=10; ws["B5"]=40
    if cogs: ws["B4"]=480
    p = tmp_path/"deal.xlsx"; wb.save(p)
    return ModelSource(p, MODEL["input_refs"])

def test_prices_candidate_with_before_after(tmp_path):
    ev = ExpressionEvaluator(MODEL)
    c = ev.price({"overrides":{"discount":12}}, _src(tmp_path))
    m = {x.id: x for x in c.metrics}
    assert c.verdict == "ok"
    assert m["net_arr"].before == "900.00" and m["net_arr"].after == "880.00"
    assert m["headroom"].after == "5.45"          # (880-480)/880*100 - 40

def test_missing_required_input_yields_hold(tmp_path):
    ev = ExpressionEvaluator(MODEL)
    c = ev.price({"overrides":{"discount":12}}, _src(tmp_path, cogs=False))
    assert c.verdict == "hold"
    assert {x.id for x in c.metrics if x.after is None} >= {"gross_margin","headroom"}

def test_price_is_deterministic(tmp_path):
    ev = ExpressionEvaluator(MODEL); s = _src(tmp_path)
    assert ev.price({"overrides":{"discount":12}}, s) == ev.price({"overrides":{"discount":12}}, s)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_expression_evaluator.py -v`
Expected: FAIL with `ModuleNotFoundError: yigdesk.evaluator.expression`

- [ ] **Step 3: Write minimal implementation**

```python
# yigdesk/evaluator/expression.py
from __future__ import annotations
import ast, hashlib, json, operator
from decimal import Decimal, ROUND_HALF_UP
from yigdesk.core.model import Consequence, Metric

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.USub: operator.neg}

def _eval(node, env):
    if isinstance(node, ast.Expression): return _eval(node.body, env)
    if isinstance(node, ast.Constant):  return Decimal(str(node.value))
    if isinstance(node, ast.Name):      return env[node.id]              # KeyError if missing
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
        self.revision = "expr:" + hashlib.sha256(
            json.dumps(model, sort_keys=True).encode()).hexdigest()[:12]

    def ground(self, ref: str, source) -> bool:
        return source.exists(ref)

    def _compute(self, inputs: dict) -> dict[str, Decimal | None]:
        env = dict(inputs); out: dict[str, Decimal | None] = {}
        for spec in self.model["metrics"]:
            missing = [r for r in spec.get("requires", []) if r not in inputs]
            if missing:
                out[spec["id"]] = None; continue
            try:
                val = _eval(ast.parse(spec["formula"], mode="eval"), env)
            except KeyError:
                out[spec["id"]] = None; continue
            env[spec["id"]] = val; out[spec["id"]] = val
        return out

    def price(self, action: dict, source) -> Consequence:
        base = source.base_inputs()
        after_inputs = {**base, **{k: Decimal(str(v)) for k, v in action.get("overrides", {}).items()}}
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_expression_evaluator.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add yigdesk/evaluator/expression.py tests/test_expression_evaluator.py
git commit -m "feat(evaluator): model-from-data ExpressionEvaluator (Decimal, HOLD, before/after)"
```

---

## Task 7: Deterministic gate

**Files:**
- Create: `yigdesk/core/gate.py`
- Test: `tests/test_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate.py
from yigdesk.core.model import Decision, Candidate, Consequence, Metric, Approval
from yigdesk.core.gate import resolve, Pending

def _decision(selector, approvals):
    d = Decision("d1", "q", "discount",
                 {"required_approvals":[{"role":"cfo","verdict":"approve"}],
                  "candidate_selector": selector, "constraint_metric":"headroom"})
    d.candidates["c1"] = Candidate("c1","agent:a",{"overrides":{"discount":12}},
        Consequence("ok",[Metric("headroom","Headroom","5.45","6.0","5.45","pt")],[],"fp"))
    d.approvals = approvals
    return d

def test_pending_when_required_approval_missing():
    r = resolve(_decision("max:headroom", []), "expr:rev")
    assert isinstance(r, Pending) and "approval" in r.reason

def test_policy_selector_closes_deterministically():
    d = _decision("max:headroom", [Approval("human:cfo","cfo","approve","c1")])
    rec = resolve(d, "expr:rev")
    assert not isinstance(rec, Pending)
    assert rec.chosen_candidate_id == "c1" and rec.closed_by == "policy" and rec.evaluator_revision == "expr:rev"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: yigdesk.core.gate`

- [ ] **Step 3: Write minimal implementation**

```python
# yigdesk/core/gate.py
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from .model import Decision, DecisionRecord

@dataclass(frozen=True)
class Pending:
    reason: str

def _approvals_met(d: Decision) -> bool:
    for req in d.policy.get("required_approvals", []):
        if not any(a.role == req["role"] and a.verdict == req["verdict"] for a in d.approvals):
            return False
    return True

def _eligible(d: Decision):
    return [c for c in d.candidates.values()
            if c.consequence is not None and c.consequence.verdict == "ok"]

def resolve(d: Decision, evaluator_revision: str, seq: int = 0):
    """Deterministic close: no LLM, pure function of (candidates, approvals, policy, revision)."""
    if not _approvals_met(d):
        return Pending("required approval missing")
    eligible = _eligible(d)
    if not eligible:
        return Pending("no candidate passes constraints (all HOLD/failed)")
    selector = d.policy.get("candidate_selector", "human_selected")
    if selector == "human_selected":
        picked = [a.scope for a in d.approvals if a.verdict == "approve" and a.scope in d.candidates]
        if not picked:
            return Pending("human selection required")
        chosen_id, closed_by = sorted(picked)[0], "human"
    elif selector.startswith("max:"):
        metric = selector.split(":", 1)[1]
        def key(c):
            m = {x.id: x.after for x in c.consequence.metrics}
            return (Decimal(m[metric]), c.id)             # id tiebreak = deterministic
        chosen_id, closed_by = max(eligible, key=key).id, "policy"
    else:
        return Pending(f"unknown selector {selector}")
    chosen = d.candidates[chosen_id]
    return DecisionRecord(
        decision_id=d.id, chosen_candidate_id=chosen_id, closed_by=closed_by,
        rationale=f"selector={selector}", evidence_refs=chosen.consequence.evidence_refs,
        approvals=[a.__dict__ for a in d.approvals], evaluator_revision=evaluator_revision,
        source_fingerprint=chosen.consequence.fingerprint, seq=seq)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_gate.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add yigdesk/core/gate.py tests/test_gate.py && git commit -m "feat(core): deterministic hybrid gate (policy/human selector)"
```

---

## Task 8: Blackboard facade (validate + ground + price + append + resolve)

**Files:**
- Create: `yigdesk/core/blackboard.py`
- Test: `tests/test_blackboard.py`

- [ ] **Step 1: Write the failing test (end-to-end)**

```python
# tests/test_blackboard.py
from openpyxl import Workbook
from yigdesk.core.blackboard import Blackboard
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource
from tests.test_expression_evaluator import MODEL

def _bb(tmp_path):
    wb = Workbook(); ws = wb.active; ws.title="Deal Inputs"
    ws["B2"]=1000; ws["B3"]=10; ws["B4"]=480; ws["B5"]=40
    p = tmp_path/"deal.xlsx"; wb.save(p)
    ev = ExpressionEvaluator(MODEL); src = ModelSource(p, MODEL["input_refs"])
    return Blackboard(tmp_path/"board.jsonl", ev, src)

def test_end_to_end_open_propose_approve_resolve(tmp_path):
    bb = _bb(tmp_path)
    bb.open_decision("d1","Approve 12%?","discount",
                     {"required_approvals":[{"role":"cfo","verdict":"approve"}],"candidate_selector":"max:headroom"},
                     actor="human:cfo", role="owner")
    bb.propose_candidate("d1","c1",{"overrides":{"discount":12}}, actor="agent:a", role="proposer")
    assert bb.project().decisions["d1"].candidates["c1"].consequence.verdict == "ok"   # priced deterministically
    assert bb.request_resolve("d1", actor="agent:a", role="proposer").reason           # pending: no approval yet
    bb.cast_approval("d1","approve","c1", actor="human:cfo", role="cfo")
    rec = bb.request_resolve("d1", actor="human:cfo", role="cfo")
    assert rec.chosen_candidate_id == "c1"
    assert bb.project().decisions["d1"].status == "resolved"

def test_ungrounded_claim_is_rejected(tmp_path):
    bb = _bb(tmp_path)
    bb.open_decision("d1","q","discount",{}, actor="h", role="owner")
    claim = bb.post_claim("d1","cl1","evidence","d1","note",["Deal Inputs!Z99"], actor="agent:a", role="critic")
    assert claim.status == "rejected"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_blackboard.py -v`
Expected: FAIL with `ModuleNotFoundError: yigdesk.core.blackboard`

- [ ] **Step 3: Write minimal implementation**

```python
# yigdesk/core/blackboard.py
from __future__ import annotations
from dataclasses import asdict
from .ledger import Ledger
from .projection import fold
from .gate import resolve, Pending
from . import ops as K
from .model import Claim

def _consequence_payload(c):
    return {"verdict": c.verdict, "metrics": [asdict(m) for m in c.metrics],
            "evidence_refs": c.evidence_refs, "fingerprint": c.fingerprint}

class Blackboard:
    def __init__(self, ledger_path, evaluator, source):
        self.ledger = Ledger(ledger_path); self.ev = evaluator; self.src = source

    def project(self):
        return fold(self.ledger.read())

    def open_decision(self, decision_id, question, decision_type, policy, *, actor, role):
        return self.ledger.append(K.OPEN_DECISION, actor, role,
            {"decision_id": decision_id, "question": question,
             "decision_type": decision_type, "policy": policy})

    def propose_candidate(self, decision_id, candidate_id, action, *, actor, role):
        c = self.ev.price(action, self.src)                    # deterministic pricing at write time
        return self.ledger.append(K.PROPOSE_CANDIDATE, actor, role,
            {"decision_id": decision_id, "candidate_id": candidate_id, "action": action,
             "consequence": _consequence_payload(c), "status": "priced" if c.verdict == "ok" else "hold"})

    def post_claim(self, decision_id, claim_id, ctype, target, body, refs, *, actor, role):
        grounded = all(self.ev.ground(r, self.src) for r in refs) and bool(refs)
        status = "grounded" if grounded else "rejected"        # D3 fail-closed
        self.ledger.append(K.POST_CLAIM, actor, role,
            {"decision_id": decision_id, "claim_id": claim_id, "type": ctype, "target": target,
             "body": body, "grounded_refs": refs if grounded else [], "status": status})
        return Claim(claim_id, actor, ctype, target, body, refs if grounded else [], status)

    def cast_approval(self, decision_id, verdict, scope, *, actor, role):
        return self.ledger.append(K.CAST_APPROVAL, actor, role,
            {"decision_id": decision_id, "verdict": verdict, "scope": scope})

    def request_resolve(self, decision_id, *, actor, role):
        board = self.project(); d = board.decisions[decision_id]
        for cid, cand in d.candidates.items():                 # rehydrate consequence objects for the gate
            cand.consequence = _rehydrate(cand.consequence)
        result = resolve(d, self.ev.revision, seq=self.ledger.next_seq())
        if isinstance(result, Pending):
            return result
        self.ledger.append(K.RESOLVED, actor, role, {"decision_id": decision_id, "record": asdict(result)})
        return result

def _rehydrate(payload):
    from .model import Consequence, Metric
    if payload is None or not isinstance(payload, dict):
        return payload
    return Consequence(payload["verdict"], [Metric(**m) for m in payload["metrics"]],
                       payload["evidence_refs"], payload["fingerprint"])
```

Update `projection.fold` to keep `consequence` as the stored dict (already does). The blackboard rehydrates before calling the gate.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_blackboard.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add yigdesk/core/blackboard.py tests/test_blackboard.py && git commit -m "feat(core): blackboard facade wiring ledger+evaluator+gate"
```

---

## Task 9: Scenario data (two scenarios, pure data — proves no-lock)

**Files:**
- Create: `data/scenarios/discount_approval/{model.json, policy.json}` + a builder `scripts/build_scenarios.py` that writes `deal.xlsx` and `margin.xlsx`
- Create: `data/scenarios/saas_margin/{model.json, policy.json}`
- Test: `tests/test_scenarios_lift.py`

- [ ] **Step 1: Write the failing test (both scenarios price with zero code change)**

```python
# tests/test_scenarios_lift.py
import json, subprocess, sys
from pathlib import Path
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource

ROOT = Path(__file__).resolve().parents[1]

def _run_builder():
    subprocess.run([sys.executable, str(ROOT/"scripts/build_scenarios.py")], check=True)

def _price(scn, action):
    d = ROOT/"data/scenarios"/scn
    model = json.loads((d/"model.json").read_text())
    src = ModelSource(d/model["workbook"], model["input_refs"])
    return ExpressionEvaluator(model).price(action, src)

def test_both_scenarios_price_ok_with_same_engine():
    _run_builder()
    assert _price("discount_approval", {"overrides":{"discount":12}}).verdict == "ok"
    assert _price("saas_margin", {"overrides":{"expansion_pct":20}}).verdict in ("ok","hold")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_scenarios_lift.py -v`
Expected: FAIL (builder / model.json missing)

- [ ] **Step 3: Write the scenario data + builder**

```json
// data/scenarios/discount_approval/model.json
{
  "workbook": "deal.xlsx",
  "input_refs": {"list_arr":"Deal Inputs!B2","discount":"Deal Inputs!B3","cogs":"Deal Inputs!B4","floor":"Deal Inputs!B5"},
  "metrics": [
    {"id":"net_arr","label":"Net ARR","formula":"list_arr * (1 - discount/100)","unit":"$k"},
    {"id":"gross_margin","label":"Gross margin","formula":"(net_arr - cogs) / net_arr * 100","requires":["cogs"],"unit":"%"},
    {"id":"headroom","label":"Headroom","formula":"gross_margin - floor","requires":["cogs"],"unit":"pt"}
  ],
  "constraints": [{"metric":"headroom","op":">=","value":0}]
}
```

```json
// data/scenarios/discount_approval/policy.json
{"decision_type":"discount","required_approvals":[{"role":"cfo","verdict":"approve"}],"candidate_selector":"max:headroom"}
```

```json
// data/scenarios/saas_margin/model.json
{
  "workbook": "margin.xlsx",
  "input_refs": {"mrr":"Plan!B2","expansion_pct":"Plan!B3","infra_cost":"Plan!B4","target_margin":"Plan!B5"},
  "metrics": [
    {"id":"new_mrr","label":"New MRR","formula":"mrr * (1 + expansion_pct/100)","unit":"$k"},
    {"id":"gross_margin","label":"Gross margin","formula":"(new_mrr - infra_cost) / new_mrr * 100","requires":["infra_cost"],"unit":"%"},
    {"id":"headroom","label":"Headroom","formula":"gross_margin - target_margin","requires":["infra_cost"],"unit":"pt"}
  ],
  "constraints": [{"metric":"headroom","op":">=","value":0}]
}
```

```json
// data/scenarios/saas_margin/policy.json
{"decision_type":"saas_margin","required_approvals":[{"role":"vp_finance","verdict":"approve"}],"candidate_selector":"max:headroom"}
```

```python
# scripts/build_scenarios.py  (writes the synthetic workbooks; data only, no scenario logic)
from pathlib import Path
from openpyxl import Workbook
ROOT = Path(__file__).resolve().parents[1] / "data/scenarios"

def _sheet(path, title, rows):
    wb = Workbook(); ws = wb.active; ws.title = title
    for addr, val in rows.items(): ws[addr] = val
    path.parent.mkdir(parents=True, exist_ok=True); wb.save(path)

_sheet(ROOT/"discount_approval/deal.xlsx", "Deal Inputs", {"B2":1000,"B3":10,"B4":480,"B5":40})
_sheet(ROOT/"saas_margin/margin.xlsx", "Plan", {"B2":2000,"B3":10,"B4":700,"B5":60})
print("scenarios built")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_scenarios_lift.py -v`
Expected: PASS (1 passed) — same evaluator, two unrelated scenarios, zero code change.

- [ ] **Step 5: Commit**

```bash
git add data/scenarios scripts/build_scenarios.py tests/test_scenarios_lift.py
git commit -m "feat(data): two model-from-data scenarios (discount, saas) prove no scenario lock"
```

---

## Task 10: MCP surface (six op tools)

**Files:**
- Rewrite: `yigdesk/mcp_server.py`
- Test: `tests/test_mcp_protocol.py`

- [ ] **Step 1: Write the failing test (stdio protocol roundtrip)**

```python
# tests/test_mcp_protocol.py
import asyncio, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def _roundtrip(ledger, scenario_dir):
    params = StdioServerParameters(command=sys.executable, args=["-m","yigdesk.mcp_server"],
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
    names, board = asyncio.run(_roundtrip(str(tmp_path/"b.jsonl"),
        "data/scenarios/discount_approval"))
    assert set(names) == {"open_decision","propose_candidate","post_claim","read_board","cast_approval","request_resolve"}
    assert board["decisions"]["d1"]["candidates"]["c1"]["consequence"]["verdict"] == "ok"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_mcp_protocol.py -v`
Expected: FAIL (server still the old 3-tool version / env not read)

- [ ] **Step 3: Write minimal implementation**

```python
# yigdesk/mcp_server.py  (read/write the blackboard; never mutates source; no private-engine reference)
from __future__ import annotations
import json, os
from dataclasses import asdict
from mcp.server.fastmcp import FastMCP
from yigdesk.core.blackboard import Blackboard
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource
from yigdesk.core.gate import Pending

INSTRUCTIONS = """Yigdesk is a deterministic decision blackboard. Propose candidates and post
grounded claims; never invent figures — the engine prices every candidate and rejects ungrounded
claims. Treat HOLD as terminal for a candidate. Decisions close only through request_resolve; this
server never writes back to the source model."""

mcp = FastMCP("Yigdesk", instructions=INSTRUCTIONS, json_response=True)

def _bb() -> Blackboard:
    scn = os.environ.get("YIGDESK_SCENARIO", "data/scenarios/discount_approval")
    model = json.loads((__import__("pathlib").Path(scn) / "model.json").read_text())
    src = ModelSource(__import__("pathlib").Path(scn) / model["workbook"], model["input_refs"])
    return Blackboard(os.environ.get("YIGDESK_LEDGER", "runtime/board.jsonl"),
                      ExpressionEvaluator(model), src)

def _board_dict(bb): 
    b = bb.project()
    return {"decisions": {did: _decision_dict(d) for did, d in b.decisions.items()}}

def _decision_dict(d):
    return {"id": d.id, "question": d.question, "status": d.status,
            "candidates": {cid: {"id": c.id, "author": c.author, "action": c.action,
                                 "status": c.status, "consequence": c.consequence} for cid, c in d.candidates.items()},
            "claims": {cid: cl.__dict__ for cid, cl in d.claims.items()},
            "approvals": [a.__dict__ for a in d.approvals],
            "resolution": asdict(d.resolution) if d.resolution else None}

@mcp.tool()
def open_decision(decision_id: str, question: str, decision_type: str = "", policy: dict | None = None) -> dict:
    """Open a decision on the board."""
    _bb().open_decision(decision_id, question, decision_type, policy or {}, actor="agent:mcp", role="owner")
    return {"opened": decision_id}

@mcp.tool()
def propose_candidate(decision_id: str, candidate_id: str, overrides: dict) -> dict:
    """Propose a candidate action (input overrides); the engine prices it deterministically."""
    op = _bb().propose_candidate(decision_id, candidate_id, {"overrides": overrides}, actor="agent:mcp", role="proposer")
    return {"candidate_id": candidate_id, "consequence": op.payload["consequence"]}

@mcp.tool()
def post_claim(decision_id: str, claim_id: str, type: str, target: str, body: str, refs: list[str]) -> dict:
    """Post a claim; rejected fail-closed unless every ref grounds to real evidence."""
    c = _bb().post_claim(decision_id, claim_id, type, target, body, refs, actor="agent:mcp", role="critic")
    return {"claim_id": claim_id, "status": c.status}

@mcp.tool()
def cast_approval(decision_id: str, verdict: str, scope: str, role: str = "reviewer") -> dict:
    """Record an approval verdict (approve|hold|reject) scoped to a candidate or the decision."""
    _bb().cast_approval(decision_id, verdict, scope, actor="agent:mcp", role=role)
    return {"recorded": verdict}

@mcp.tool()
def request_resolve(decision_id: str) -> dict:
    """Run the deterministic gate; returns pending(reason) or the committed decision record."""
    r = _bb().request_resolve(decision_id, actor="agent:mcp", role="resolver")
    return {"pending": r.reason} if isinstance(r, Pending) else {"record": asdict(r)}

@mcp.tool()
def read_board() -> dict:
    """Return the deterministic board projection."""
    return _board_dict(_bb())

def main() -> None:
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_mcp_protocol.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add yigdesk/mcp_server.py tests/test_mcp_protocol.py && git commit -m "feat(mcp): six-op blackboard surface replacing the read-only demo tools"
```

---

## Task 11: Determinism conformance suite

**Files:**
- Test: `tests/test_determinism_conformance.py`

- [ ] **Step 1: Write the conformance tests**

```python
# tests/test_determinism_conformance.py
from openpyxl import Workbook
from yigdesk.core.ledger import Ledger
from yigdesk.core.projection import fold
from yigdesk.core.blackboard import Blackboard
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource
from tests.test_expression_evaluator import MODEL

def _bb(tmp_path, name="board.jsonl"):
    wb = Workbook(); ws = wb.active; ws.title="Deal Inputs"
    ws["B2"]=1000; ws["B3"]=10; ws["B4"]=480; ws["B5"]=40
    p = tmp_path/"deal.xlsx"; wb.save(p)
    return Blackboard(tmp_path/name, ExpressionEvaluator(MODEL), ModelSource(p, MODEL["input_refs"]))

def test_d2_replay_of_same_log_yields_identical_projection(tmp_path):
    bb = _bb(tmp_path)
    bb.open_decision("d1","q","discount",{"candidate_selector":"max:headroom"}, actor="h", role="owner")
    bb.propose_candidate("d1","c1",{"overrides":{"discount":12}}, actor="a", role="proposer")
    ops = Ledger(tmp_path/"board.jsonl").read()
    assert fold(ops) == fold(ops)                     # pure fold
    assert repr(fold(ops)) == repr(fold(list(ops)))   # order-stable

def test_d1_same_action_same_consequence(tmp_path):
    bb = _bb(tmp_path)
    from tests.test_expression_evaluator import MODEL as M
    ev = bb.ev
    c1 = ev.price({"overrides":{"discount":12}}, bb.src)
    c2 = ev.price({"overrides":{"discount":12}}, bb.src)
    assert c1 == c2

def test_d4_resolve_is_pure(tmp_path):
    bb = _bb(tmp_path)
    bb.open_decision("d1","q","discount",{"required_approvals":[{"role":"cfo","verdict":"approve"}],"candidate_selector":"max:headroom"}, actor="h", role="owner")
    bb.propose_candidate("d1","c1",{"overrides":{"discount":12}}, actor="a", role="proposer")
    bb.cast_approval("d1","approve","c1", actor="cfo", role="cfo")
    from yigdesk.core.gate import resolve
    d = bb.project().decisions["d1"]
    from yigdesk.core.blackboard import _rehydrate
    for c in d.candidates.values(): c.consequence = _rehydrate(c.consequence)
    assert resolve(d, "rev") == resolve(d, "rev")
```

- [ ] **Step 2: Run to verify (should pass on the built modules)**

Run: `python -m pytest tests/test_determinism_conformance.py -v`
Expected: PASS (3 passed). If any fails, the offending module violates D1/D2/D4 — fix it before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_determinism_conformance.py && git commit -m "test: determinism conformance suite (D1/D2/D4)"
```

---

## Task 12: Strengthen the boundary test (domain-neutral core + no private-engine token + no source write)

**Files:**
- Rewrite: `tests/test_boundary.py`

- [ ] **Step 1: Write the boundary tests**

```python
# tests/test_boundary.py
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

CORE = list((ROOT/"yigdesk/core").glob("*.py")) + list((ROOT/"yigdesk/evaluator").glob("*.py"))
SHIPPED = [p for p in (ROOT/"yigdesk").rglob("*.py")] + [ROOT/"README.md"] + list((ROOT/"tests").glob("*.py"))

def test_core_and_evaluator_are_domain_neutral():
    forbidden = re.compile(r"discount|\barr\b|margin|cogs|cfo|saas|mrr|finance", re.IGNORECASE)
    hits = {str(p.relative_to(ROOT)): sorted(set(forbidden.findall(p.read_text(encoding='utf-8'))))
            for p in CORE if forbidden.search(p.read_text(encoding='utf-8'))}
    assert not hits, f"business vocabulary leaked into the domain-neutral core: {hits}"

def test_no_private_engine_association_in_shipped_surface():
    banned = "yig" + "rid"                       # the private engine name must not appear in shipped code/docs
    hits = [str(p.relative_to(ROOT)) for p in SHIPPED
            if p.exists() and banned.lower() in p.read_text(encoding='utf-8').lower()]
    assert not hits, f"private-engine association found in shipped surface: {hits}"

def test_open_core_boundary_doc_is_deleted():
    assert not (ROOT/"docs/OPEN_CORE_BOUNDARY.md").exists()

def test_mcp_server_declares_no_source_write_back():
    txt = (ROOT/"yigdesk/mcp_server.py").read_text(encoding="utf-8")
    assert "never writes back to the source" in txt
    assert "commit(" not in txt and "write_cell" not in txt
```

Note: this file names the private engine only via the split string `"yig"+"rid"` so the repo file itself contains no literal token.

- [ ] **Step 2: Run to verify it passes**

Run: `python -m pytest tests/test_boundary.py -v`
Expected: PASS (4 passed). If `test_no_private_engine_association...` fails, purge the offending reference (that is the point of the test).

- [ ] **Step 3: Full suite green**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_boundary.py && git commit -m "test(boundary): domain-neutral core + no private-engine association + no source write-back"
```

---

## Task 13: Rewrite README/ARCHITECTURE to stand alone

**Files:**
- Modify: `README.md`, `docs/ARCHITECTURE.md`

- [ ] **Step 1: Rewrite `README.md`** so it describes Yigdesk only — a standalone deterministic decision blackboard (MCP + ledger + evaluator), the six ops, the run commands (`python scripts/build_scenarios.py`, `python -m yigdesk.mcp_server`, `python -m pytest`), and the two scenarios. Remove every reference to the private engine and to the single-flow demo. (No code block needed; prose file.)

- [ ] **Step 2: Rewrite `docs/ARCHITECTURE.md`** to the five units (Core / Evaluator / MCP / Domain data / [UI in Plan 2]) and the D1–D4 guarantees, per the spec §3/§5.

- [ ] **Step 3: Verify the boundary test still passes (catches stray references)**

Run: `python -m pytest tests/test_boundary.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/ARCHITECTURE.md && git commit -m "docs: rewrite README/ARCHITECTURE as a standalone decision blackboard"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** §3 units → Tasks 1–10,13 (UI unit = Plan 2, noted). §4 model → Tasks 1,3,4. §5 D1/D2/D4 → Tasks 6,11; D3 grounding → Task 8 + `test_ungrounded_claim_is_rejected`. §6 flow/fail-closed → Task 8. §7 model-from-data evaluator → Tasks 5,6,9. §8 delete map → Task 0 (+ per-file rewrites). §9 separation → Tasks 0,12,13. §10 tests → Tasks 11,12 + per-task tests. §11 MVP ≥2 scenarios → Task 9. §12 Rust → out of scope (future). **No gaps for the backend spine; UI (spec §3 unit 4) is deferred to Plan 2 by design.**
- **Placeholder scan:** none — every code step has complete code; commands have expected output.
- **Type consistency:** `Op/Ledger/Board/Decision/Candidate/Claim/Approval/Consequence/Metric/DecisionRecord/Pending` used identically across tasks; `Evaluator.price(action, source)`/`ground(ref, source)` consistent (Tasks 5,6,8,10); `Blackboard.{open_decision,propose_candidate,post_claim,cast_approval,request_resolve,project}` consistent (Tasks 8,10,11); `resolve(decision, evaluator_revision, seq)` consistent (Tasks 7,8,11).

---

## Next: Plan 2 (Board UI / human bridge)

Not in this plan. Plan 2 will extend `static/yig-grid.js` + `yig-model-inspector.js` + `session.js` into a board view + gate panel, and add the Flask read/append endpoints, on top of this backend (`Blackboard.project()` / the six ops). It depends on this plan being complete and green.
