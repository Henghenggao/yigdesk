# Yigdesk Web Board + Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Yigdesk's web surface as a read + gate board over the deterministic blackboard, sharing one append-only ledger with the MCP council agents, so a human casts approvals and triggers resolution while agents propose/claim via MCP.

**Architecture:** Flask (`app.py`) and the `blackboard_mcp` stdio server each construct a `Blackboard` over the SAME `YIGDESK_LEDGER` + `YIGDESK_SCENARIO`. Because the council runs finance/sales/risk as parallel MCP subprocesses (plus Flask), the ledger gains a portable inter-process write transaction; `request_resolve` becomes idempotent; the board DTO carries `decision_type` + `policy`. The web adds `GET /api/board` + `POST /api/board/op` (cast_approval + request_resolve), and the static UI (`session.js`/`app.js`/`index.html`) is rebuilt to render the board + a selector-aware gate panel.

**Tech Stack:** Python 3.11, Flask/werkzeug, `mcp`/FastMCP, openpyxl, pytest; vanilla JS static UI + Playwright e2e (`run_e2e.mjs`). Portable file locking via stdlib `fcntl` (POSIX) / `msvcrt` (Windows).

**Design of record:** spec `docs/superpowers/specs/2026-07-19-yigdesk-web-board-gate-design.md`.

**Ordering principle:** core corrections first (ledger transaction → idempotent resolve → shared DTO), then the boundary-guard reconciliation, then the HTTP API, then the frontend, then e2e. Keep `python -m pytest` green at every commit (baseline **126**).

---

## Task 1: Portable inter-process ledger transaction

**Files:**
- Modify: `yigdesk/core/ledger.py`
- Test: `tests/test_ledger.py` (extend), `tests/test_ledger_concurrency.py` (create)

The current `Ledger.append` does `next_seq()` (read whole file) then a separate write with no lock — concurrent MCP subprocesses race on `seq`. Add a portable exclusive lock over a sidecar `<path>.lock` file, held across read-tail → allocate-seq → append. Expose a `transaction()` context manager so `request_resolve` (Task 2) can snapshot + append atomically under one lock. The OS releases the lock on process death (crash-safe), and each op is written as one full `line + "\n"`.

- [ ] **Step 1: Write the failing concurrency test** — `tests/test_ledger_concurrency.py`:

```python
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

# A worker script: append N ops to the shared ledger via the real Ledger API.
WORKER = """
import sys
from yigdesk.core.ledger import Ledger
led = Ledger(sys.argv[1])
actor = sys.argv[2]
for i in range(int(sys.argv[3])):
    led.append("open_decision", actor, "owner", {"decision_id": f"{actor}-{i}"})
"""


def test_parallel_writers_produce_unique_increasing_seqs(tmp_path):
    ledger = tmp_path / "board.jsonl"
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER, encoding="utf-8")
    per = 40
    procs = [
        subprocess.Popen([sys.executable, str(worker), str(ledger), f"w{n}", str(per)])
        for n in range(3)
    ]
    for p in procs:
        assert p.wait(timeout=60) == 0

    lines = [l for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    ops = [json.loads(l) for l in lines]  # every line must be complete JSON
    seqs = [o["seq"] for o in ops]
    assert len(ops) == 3 * per
    assert len(set(seqs)) == len(seqs)            # unique
    assert seqs == sorted(seqs)                    # strictly increasing in file order
    assert seqs == list(range(1, 3 * per + 1))     # dense 1..N, no gaps/dupes
```

- [ ] **Step 2: Run it, verify it FAILS** — Run: `python -m pytest tests/test_ledger_concurrency.py -q`. Expected: FAIL (duplicate seqs / gaps under the racy `append`).

- [ ] **Step 3: Implement the portable lock + transaction** — replace `yigdesk/core/ledger.py` with:

```python
from __future__ import annotations
import os
from contextlib import contextmanager
from pathlib import Path
from .ops import Op, op_to_json, op_from_json

try:                                    # POSIX
    import fcntl

    def _acquire(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
except ImportError:                     # Windows
    import msvcrt
    import time

    def _acquire(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.01)

    def _release(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class Ledger:
    """Append-only op log with a portable cross-process write transaction.

    read-tail -> allocate monotonic seq -> append one full line all run under an
    exclusive lock on a sidecar <path>.lock file (OS-released on process death).
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock_path = self.path.with_name(self.path.name + ".lock")

    def read(self) -> list[Op]:
        text = self.path.read_text(encoding="utf-8")
        return [op_from_json(l) for l in text.splitlines() if l.strip()]

    def next_seq(self) -> int:
        ops = self.read()
        return ops[-1].seq + 1 if ops else 1

    @contextmanager
    def transaction(self):
        """Hold the exclusive lock across a read/append critical section."""
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _acquire(fd)
            yield _Txn(self)
        finally:
            try:
                _release(fd)
            finally:
                os.close(fd)

    def append(self, kind, actor, role, payload, base_seq=0) -> Op:
        with self.transaction() as txn:
            return txn.append(kind, actor, role, payload, base_seq)


class _Txn:
    """Read/append helpers that run inside the ledger's held lock."""

    def __init__(self, ledger: Ledger):
        self._ledger = ledger

    def read(self) -> list[Op]:
        return self._ledger.read()

    def next_seq(self) -> int:
        return self._ledger.next_seq()

    def append(self, kind, actor, role, payload, base_seq=0) -> Op:
        op = Op(self._ledger.next_seq(), kind, actor, role, payload, base_seq)
        with self._ledger.path.open("a", encoding="utf-8") as f:
            f.write(op_to_json(op) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return op
```

- [ ] **Step 4: Run tests, verify PASS** — Run: `python -m pytest tests/test_ledger_concurrency.py tests/test_ledger.py -q`. Expected: PASS. Then full suite `python -m pytest -q` → still green (the public `Ledger.read/next_seq/append` signatures are unchanged; `transaction()`/`_Txn` are additive).

- [ ] **Step 5: Commit**

```bash
git add yigdesk/core/ledger.py tests/test_ledger_concurrency.py tests/test_ledger.py
git commit -m "fix(core): portable cross-process ledger write transaction"
```

---

## Task 2: Idempotent `request_resolve` under the ledger transaction

**Files:**
- Modify: `yigdesk/core/blackboard.py:43-49`
- Test: `tests/test_blackboard.py` (extend)

`request_resolve` currently appends a `RESOLVED` op unconditionally whenever the gate succeeds — calling it twice yields two resolved ops. Make it (a) run its snapshot + gate + append under one `ledger.transaction()` (so a concurrent writer can't interleave, and `record.seq == resolved_op.seq`), and (b) idempotent: if the decision is already resolved, return the existing record and append nothing.

- [ ] **Step 1: Write the failing test** — append to `tests/test_blackboard.py` (reuse the file's existing scenario/blackboard fixtures; if it builds a `Blackboard` via a helper, use that — otherwise construct one over a `council_discount` tmp scenario like `tests/test_council_audit.py` does):

```python
def test_request_resolve_is_idempotent(tmp_path):
    bb = _resolvable_blackboard(tmp_path)   # opens a decision, proposes a candidate,
                                            # posts the required grounded risk claim,
                                            # casts the required cfo approval
    first = bb.request_resolve("d1", actor="human:web", role="cfo")
    from yigdesk.core.gate import Pending
    assert not isinstance(first, Pending)
    resolved_ops_1 = [o for o in bb.ledger.read() if o.kind == "resolved"]
    assert len(resolved_ops_1) == 1
    assert first.seq == resolved_ops_1[0].seq          # record seq == resolved op seq

    second = bb.request_resolve("d1", actor="human:web", role="cfo")
    resolved_ops_2 = [o for o in bb.ledger.read() if o.kind == "resolved"]
    assert len(resolved_ops_2) == 1                     # no second resolved op
    assert second == first                              # same record returned
```

Provide the `_resolvable_blackboard(tmp_path)` helper in the test module. Model it on `tests/test_council_audit.py`'s setup (build the `council_discount` workbook via openpyxl or `scripts.build_scenarios`, `ModelSource` + `ExpressionEvaluator`, `Blackboard`), open `d1` with the `council_discount` policy, propose one candidate that prices `ok`, `post_claim` a grounded `risk` claim, `cast_approval("approve", <candidate_id>, role="cfo")`.

- [ ] **Step 2: Run it, verify it FAILS** — Run: `python -m pytest tests/test_blackboard.py -k idempotent -q`. Expected: FAIL (second call appends a 2nd `resolved` op → `len == 2`).

- [ ] **Step 3: Implement** — replace `request_resolve` in `yigdesk/core/blackboard.py`:

```python
    def request_resolve(self, decision_id, *, actor, role):
        with self.ledger.transaction() as txn:
            board = fold(txn.read())
            d = board.decisions[decision_id]
            if d.resolution is not None:           # idempotent: already closed
                return d.resolution
            result = resolve(d, self.ev.revision, seq=txn.next_seq())
            if isinstance(result, Pending):
                return result
            txn.append(K.RESOLVED, actor, role,
                       {"decision_id": decision_id, "record": asdict(result)})
            return result
```

- [ ] **Step 4: Run tests, verify PASS** — Run: `python -m pytest tests/test_blackboard.py tests/test_council_audit.py tests/test_determinism_conformance.py -q` then full `python -m pytest -q`. Expected: PASS (existing resolve behavior unchanged for the first call; determinism suite still green).

- [ ] **Step 5: Commit**

```bash
git add yigdesk/core/blackboard.py tests/test_blackboard.py
git commit -m "fix(core): idempotent request_resolve under the ledger transaction"
```

---

## Task 3: Shared board module (`build_blackboard` + serializer with policy)

**Files:**
- Create: `yigdesk/board.py`
- Modify: `yigdesk/blackboard_mcp.py` (import the shared helpers; add `decision_type`+`policy` to `read_board`)
- Test: `tests/test_blackboard_mcp.py` (extend), `tests/test_board_module.py` (create)

Extract the blackboard construction (`_bb`) and the decision serializer (`_decision_dict`) out of `blackboard_mcp.py` into a shared `yigdesk/board.py`, and extend the serialized decision with `decision_type` + `policy` so both the MCP `read_board` tool and the web `GET /api/board` render identically and the web can present a truthful gate. Keep `blackboard_mcp` behavior-equivalent aside from the two added fields.

- [ ] **Step 1: Write the failing tests** — `tests/test_board_module.py`:

```python
from __future__ import annotations
import json
from pathlib import Path
from yigdesk.board import build_blackboard, decision_dict


def _scenario(tmp_path) -> Path:
    # Reuse the packaged council scenario's model.json; build its tiny workbook here.
    from openpyxl import Workbook
    root = Path(__file__).resolve().parents[1]
    scn = tmp_path / "scn"
    scn.mkdir()
    (scn / "model.json").write_text(
        (root / "data" / "scenarios" / "council_discount" / "model.json").read_text("utf-8"),
        encoding="utf-8",
    )
    wb = Workbook(); ws = wb.active; ws.title = "Deal Inputs"
    for addr, val in {"B2": 1000, "B3": 0, "B4": 480, "B5": 40}.items():
        ws[addr] = val
    wb.save(scn / "council_deal.xlsx")
    return scn


def test_build_blackboard_prices_and_serializes_policy(tmp_path):
    scn = _scenario(tmp_path)
    bb = build_blackboard(scn, tmp_path / "board.jsonl")
    policy = json.loads((scn.parent.parent / "data" / "scenarios" / "council_discount" / "policy.json").read_text("utf-8")) \
        if False else {"decision_type": "council_discount",
                       "required_approvals": [{"role": "cfo", "verdict": "approve"}],
                       "required_claims": [{"type": "risk"}],
                       "candidate_selector": "max:headroom"}
    bb.open_decision("d1", "Approve discount?", "council_discount", policy,
                     actor="agent:mcp", role="owner")
    bb.propose_candidate("d1", "c1", {"overrides": {"discount": 2}},
                         actor="agent:mcp", role="proposer")
    d = bb.project().decisions["d1"]
    dto = decision_dict(d)
    assert dto["decision_type"] == "council_discount"
    assert dto["policy"]["candidate_selector"] == "max:headroom"
    assert dto["candidates"]["c1"]["consequence"]["verdict"] in {"ok", "hold"}
    assert "claims" in dto and "approvals" in dto
```

Extend `tests/test_blackboard_mcp.py` with an assertion that `read_board` now includes `decision_type` and `policy` on each decision (add it to whatever existing read_board test exists; if none, add `test_read_board_includes_policy`).

- [ ] **Step 2: Run, verify FAIL** — Run: `python -m pytest tests/test_board_module.py -q`. Expected: FAIL (`yigdesk.board` does not exist).

- [ ] **Step 3: Create `yigdesk/board.py`**:

```python
"""Shared board construction + serialization for the MCP and web surfaces."""
from __future__ import annotations
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any
from yigdesk.core.blackboard import Blackboard
from yigdesk.evaluator.expression import ExpressionEvaluator
from yigdesk.evaluator.model_source import ModelSource

DEFAULT_LEDGER = "runtime/board.jsonl"


def build_blackboard(scenario_dir, ledger_path=None) -> Blackboard:
    scn = Path(scenario_dir)
    model = json.loads((scn / "model.json").read_text(encoding="utf-8"))
    src = ModelSource(scn / model["workbook"], model["input_refs"])
    return Blackboard(str(ledger_path or DEFAULT_LEDGER), ExpressionEvaluator(model), src)


def build_blackboard_from_env() -> Blackboard:
    return build_blackboard(
        os.environ["YIGDESK_SCENARIO"],
        os.environ.get("YIGDESK_LEDGER", DEFAULT_LEDGER),
    )


def decision_dict(d) -> dict[str, Any]:
    return {
        "id": d.id,
        "question": d.question,
        "decision_type": d.decision_type,
        "policy": d.policy,
        "status": d.status,
        "candidates": {
            cid: {"id": c.id, "author": c.author, "action": c.action, "status": c.status,
                  "consequence": (asdict(c.consequence) if c.consequence is not None else None)}
            for cid, c in d.candidates.items()
        },
        "claims": {cid: cl.__dict__ for cid, cl in d.claims.items()},
        "approvals": [a.__dict__ for a in d.approvals],
        "resolution": asdict(d.resolution) if d.resolution else None,
    }


def board_dict(board) -> dict[str, Any]:
    return {"decisions": {did: decision_dict(d) for did, d in board.decisions.items()}}
```

(Verified: `Decision` in `yigdesk/core/model.py:39-45` exposes `decision_type` + `policy` + `resolution`, and `projection.fold` (`yigdesk/core/projection.py:16-18`) already sets `decision_type`/`policy` from the `open_decision` op. So `decision_dict` reads them directly — NO change to `projection.py` is needed.)

- [ ] **Step 4: Rewrite `blackboard_mcp.py` to use the shared module** — replace its `_bb` and `_decision_dict` with imports:

```python
from yigdesk.board import build_blackboard_from_env, board_dict, decision_dict
# delete the local _bb() and _decision_dict(); replace _bb() calls with build_blackboard_from_env()
# read_board() returns board_dict(_bb().project())
```

Keep every tool's behavior identical otherwise.

- [ ] **Step 5: Run, verify PASS** — Run: `python -m pytest tests/test_board_module.py tests/test_blackboard_mcp.py -q` then full `python -m pytest -q`. Expected: PASS (update the one read_board test to expect the two new fields).

- [ ] **Step 6: Commit**

```bash
git add yigdesk/board.py yigdesk/blackboard_mcp.py tests/test_board_module.py tests/test_blackboard_mcp.py
git commit -m "refactor(board): shared build_blackboard + serializer with decision_type/policy"
```

---

## Task 4: Reconcile the capability boundary guard

**Files:**
- Modify: `tests/test_boundary.py:37-48` (`test_public_runtime_has_no_commercial_mutation_or_trust_service`)

The standalone blackboard's core product now includes approvals + human-gated resolution, so `approv(e|al)` and `human[-_ ]?gated` are no longer forbidden in the runtime seam. Remove exactly those two tokens; keep every genuinely-out-of-scope commercial/trust-service token. This lands BEFORE Task 5 adds `cast_approval` to `app.py`, so the guard stays green.

- [ ] **Step 1: Edit the forbidden pattern** — in `tests/test_boundary.py`, change the regex in `test_public_runtime_has_no_commercial_mutation_or_trust_service` from:

```python
    forbidden = re.compile(
        r"approv(?:e|al)|write[-_ ]?back|vault|attest|audit[-_ ]?trail|reopen|sign(?:ing|ature)|"
        r"multi[-_ ]?tenant|\bsso\b|human[-_ ]?gated",
        re.IGNORECASE,
    )
```

to (drop `approv(?:e|al)` and `human[-_ ]?gated`; keep the rest):

```python
    # Approvals + human-gated resolution are the standalone blackboard's core product
    # (the deterministic gate); this guard now targets SOURCE mutation + external
    # commercial/trust services only.
    forbidden = re.compile(
        r"write[-_ ]?back|vault|attest|audit[-_ ]?trail|reopen|sign(?:ing|ature)|"
        r"multi[-_ ]?tenant|\bsso\b",
        re.IGNORECASE,
    )
```

- [ ] **Step 2: Run the boundary suite, verify PASS** — Run: `python -m pytest tests/test_boundary.py -q`. Expected: PASS (still green; nothing yet uses "approval" in the seam). Full `python -m pytest -q` → 126 green.

- [ ] **Step 3: Commit**

```bash
git add tests/test_boundary.py
git commit -m "test(boundary): approvals+gating are core product; guard source-mutation/trust-services"
```

---

## Task 5: Board HTTP API (`GET /api/board`, `POST /api/board/op`)

**Files:**
- Modify: `yigdesk/app.py` (add 2 routes + a board builder + selector-aware validation)
- Create: `tests/test_board_api.py`

Add the two board routes to the engine-free Flask shell, sharing the agents' ledger via `build_blackboard_from_env`. `POST /api/board/op` allowlists `cast_approval` + `request_resolve`, validates selector-aware, attributes `actor="human:web"`, and returns `{board, result}` where result is `null` (approval), `{pending}` , or `{record, replayed}`.

- [ ] **Step 1: Write the failing tests** — `tests/test_board_api.py`:

```python
from __future__ import annotations
import json
from pathlib import Path
import pytest
from openpyxl import Workbook
from yigdesk.app import create_app
from yigdesk.board import build_blackboard

ROOT = Path(__file__).resolve().parents[1]
POLICY = {"decision_type": "council_discount",
          "required_approvals": [{"role": "cfo", "verdict": "approve"}],
          "required_claims": [{"type": "risk"}],
          "candidate_selector": "max:headroom"}


def _scenario(tmp_path):
    scn = tmp_path / "scn"; scn.mkdir()
    (scn / "model.json").write_text(
        (ROOT / "data" / "scenarios" / "council_discount" / "model.json").read_text("utf-8"), "utf-8")
    wb = Workbook(); ws = wb.active; ws.title = "Deal Inputs"
    for a, v in {"B2": 1000, "B3": 0, "B4": 480, "B5": 40}.items():
        ws[a] = v
    wb.save(scn / "council_deal.xlsx")
    return scn


@pytest.fixture
def client(tmp_path, monkeypatch):
    scn = _scenario(tmp_path)
    ledger = tmp_path / "board.jsonl"
    monkeypatch.setenv("YIGDESK_SCENARIO", str(scn))
    monkeypatch.setenv("YIGDESK_LEDGER", str(ledger))
    # seed the shared ledger as the agent side would (NO live Codex)
    bb = build_blackboard(scn, ledger)
    bb.open_decision("d1", "Approve the discount?", "council_discount", POLICY,
                     actor="agent:mcp", role="owner")
    bb.propose_candidate("d1", "c1", {"overrides": {"discount": 2}}, actor="finance", role="proposer")
    bb.post_claim("d1", "k1", "risk", "c1", "cogs may rise", ["Deal Inputs!B4"],
                  actor="risk", role="critic")
    app = create_app(runtime_dir=tmp_path)
    return app.test_client()


def test_get_board_returns_policy_and_priced_candidate(client):
    r = client.get("/api/board")
    assert r.status_code == 200
    d = r.get_json()["decisions"]["d1"]
    assert d["decision_type"] == "council_discount"
    assert d["policy"]["candidate_selector"] == "max:headroom"
    assert d["candidates"]["c1"]["consequence"]["verdict"] == "ok"
    assert d["claims"]["k1"]["status"] == "grounded"


def test_resolve_pends_before_approval_then_commits_after(client):
    pend = client.post("/api/board/op", json={
        "decision_id": "d1", "kind": "request_resolve", "payload": {}}).get_json()
    assert pend["result"]["pending"]
    ok = client.post("/api/board/op", json={
        "decision_id": "d1", "kind": "cast_approval",
        "payload": {"verdict": "approve", "scope": "d1", "role": "cfo"}}).get_json()
    assert ok["result"] is None
    done = client.post("/api/board/op", json={
        "decision_id": "d1", "kind": "request_resolve", "payload": {}}).get_json()
    assert done["result"]["record"]["chosen_candidate_id"] == "c1"
    assert done["result"]["replayed"] is False
    again = client.post("/api/board/op", json={
        "decision_id": "d1", "kind": "request_resolve", "payload": {}}).get_json()
    assert again["result"]["replayed"] is True
    assert again["result"]["record"] == done["result"]["record"]


@pytest.mark.parametrize("body", [
    {"decision_id": "d1", "kind": "propose_candidate", "payload": {}},   # not allowlisted
    {"decision_id": "nope", "kind": "request_resolve", "payload": {}},   # unknown decision
    {"decision_id": "d1", "kind": "cast_approval",
     "payload": {"verdict": "maybe", "scope": "d1", "role": "cfo"}},     # bad verdict
    {"decision_id": "d1", "kind": "cast_approval",
     "payload": {"verdict": "approve", "scope": "ghost", "role": "cfo"}},# unknown candidate scope
])
def test_board_op_rejects_bad_requests(client, body):
    assert client.post("/api/board/op", json=body).status_code == 400
```

- [ ] **Step 2: Run, verify FAIL** — Run: `python -m pytest tests/test_board_api.py -q`. Expected: FAIL (routes 404).

- [ ] **Step 3: Implement the routes in `yigdesk/app.py`** — add near the other routes inside `create_app` (and `from yigdesk.board import build_blackboard_from_env, board_dict` at top; `from yigdesk.core.gate import Pending`):

```python
    HUMAN_OPS = {"cast_approval", "request_resolve"}
    VERDICTS = {"approve", "hold", "reject"}

    @app.get("/api/board")
    def get_board():
        return jsonify(board_dict(build_blackboard_from_env().project()))

    @app.post("/api/board/op")
    def board_op():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"code": "INVALID_REQUEST", "error": "JSON object required"}), 400
        kind = body.get("kind")
        decision_id = body.get("decision_id")
        payload = body.get("payload") or {}
        if kind not in HUMAN_OPS:
            return jsonify({"code": "OP_NOT_ALLOWED", "error": "kind must be cast_approval or request_resolve"}), 400
        bb = build_blackboard_from_env()
        board = bb.project()
        d = board.decisions.get(decision_id)
        if d is None:
            return jsonify({"code": "UNKNOWN_DECISION", "error": "no such decision"}), 400
        if kind == "cast_approval":
            verdict = payload.get("verdict"); scope = payload.get("scope"); role = payload.get("role")
            if verdict not in VERDICTS:
                return jsonify({"code": "BAD_VERDICT", "error": "verdict must be approve|hold|reject"}), 400
            required_roles = {a["role"] for a in d.policy.get("required_approvals", [])}
            if required_roles and role not in required_roles:
                return jsonify({"code": "BAD_ROLE", "error": f"role must be one of {sorted(required_roles)}"}), 400
            selector = d.policy.get("candidate_selector", "human_selected")
            if scope != decision_id:                      # candidate-scoped
                if scope not in d.candidates:
                    return jsonify({"code": "UNKNOWN_SCOPE", "error": "scope must be a candidate id or the decision id"}), 400
            elif verdict == "approve" and selector == "human_selected":
                return jsonify({"code": "SELECTION_REQUIRED", "error": "human_selected requires a candidate scope"}), 400
            bb.cast_approval(decision_id, verdict, scope, actor="human:web", role=role)
            return jsonify({"board": board_dict(bb.project()), "result": None})
        # request_resolve
        already = d.resolution is not None
        result = bb.request_resolve(decision_id, actor="human:web", role="reviewer")
        if isinstance(result, Pending):
            return jsonify({"board": board_dict(bb.project()), "result": {"pending": result.reason}})
        from dataclasses import asdict
        return jsonify({"board": board_dict(bb.project()),
                        "result": {"record": asdict(result), "replayed": already}})
```

- [ ] **Step 4: Run, verify PASS** — Run: `python -m pytest tests/test_board_api.py -q` then full `python -m pytest -q`. Expected: PASS (126 + new). Confirm `test_boundary.py` still green (app.py now contains "approval" — allowed after Task 4).

- [ ] **Step 5: Commit**

```bash
git add yigdesk/app.py tests/test_board_api.py
git commit -m "feat(app): board read + selector-aware gate op endpoints on the shared ledger"
```

---

## Task 6: `session.js` — board API client

**Files:**
- Rewrite: `yigdesk/static/session.js`

Replace the dead read-only client with a thin board client. Keep it domain-neutral (boundary-guarded). No business vocabulary; no `approval`-forbidden concern (that guard was reconciled in Task 4, but this file must still avoid `write[-_ ]?back`/`vault`/`attest`/`audit[-_ ]?trail`/`reopen`/`sign*`/`multi-tenant`/`sso`).

- [ ] **Step 1: Rewrite `yigdesk/static/session.js`** — full contents:

```javascript
// Thin client for the deterministic board surface. Domain-neutral: no business terms.
export class BoardSession {
  constructor(baseUrl = "") {
    this.baseUrl = baseUrl;
  }
  async #json(path, options) {
    const res = await fetch(this.baseUrl + path, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status})`);
      err.code = data.code;
      throw err;
    }
    return data;
  }
  getBoard() {
    return this.#json("/api/board");
  }
  #op(decisionId, kind, payload) {
    return this.#json("/api/board/op", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision_id: decisionId, kind, payload }),
    });
  }
  castApproval(decisionId, { verdict, scope, role }) {
    return this.#op(decisionId, "cast_approval", { verdict, scope, role });
  }
  requestResolve(decisionId) {
    return this.#op(decisionId, "request_resolve", {});
  }
}
```

- [ ] **Step 2: Verify boundary + syntax** — Run: `python -m pytest tests/test_boundary.py -q` (session.js must stay domain-neutral + free of the still-forbidden tokens). Expected: PASS. Also `node --check yigdesk/static/session.js` → no syntax error.

- [ ] **Step 3: Commit**

```bash
git add yigdesk/static/session.js
git commit -m "feat(web): board API client (session.js) replacing the dead read-only client"
```

---

## Task 7: `app.js` + `index.html` — board view + selector-aware gate panel

**Files:**
- Rewrite: `yigdesk/static/app.js`
- Modify: `yigdesk/static/index.html`

Render the board and the gate. This task's contract is the **DOM/behavior contract** the e2e (Task 8) asserts; build to it. Reuse `styles.css` + `yig-grid.js`/`yig-model-inspector.js` where they fit for candidate/consequence rendering.

**DOM contract (stable `data-testid` hooks the e2e depends on):**
- `[data-testid="board-empty"]` — shown when zero decisions.
- `[data-testid="decision-chooser"]` with one `[data-testid="decision-option"][data-decision-id=<id>]` per decision — shown only when >1 decision; hidden/absent when exactly one (auto-selected).
- `[data-testid="decision-view"][data-decision-id=<id>]` — the selected decision; contains the question, `decision_type`, and status.
- `[data-testid="candidate"][data-candidate-id=<id>]` per candidate, each showing verdict + metrics (via `yig-model-inspector`) + evidence refs.
- `[data-testid="claim"]` per grounded claim.
- `[data-testid="gate-panel"]` containing:
  - a `[data-testid="role-select"]` populated from `policy.required_approvals[].role`;
  - for `human_selected`: per-candidate `[data-testid="approve-candidate"][data-candidate-id=<id>]` + `hold` controls;
  - for `max:<metric>`: a `[data-testid="policy-winner"]` (the current eligible max) + a `[data-testid="authorize-policy"]` decision-scoped approve button;
  - a `[data-testid="resolve"]` button;
  - `[data-testid="resolve-outcome"]` rendering either `pending(reason)` (text of what's missing) or the committed record (`chosen_candidate_id`, `closed_by`).
- After a committed resolution, gate action buttons carry `disabled`; a retried resolve re-renders the same record in `[data-testid="resolve-outcome"]`.
- `[data-testid="refresh"]` triggers a re-fetch; a light poll (e.g. every 4s) refetches without changing the user's selected decision.

- [ ] **Step 1: Rewrite `yigdesk/static/app.js`** to implement the DOM contract above using `BoardSession` from `session.js`. Structure it as small pure-ish render functions (renderChooser, renderDecision, renderCandidate, renderGate, renderOutcome) + an event layer that calls `session.castApproval` / `session.requestResolve` then re-renders from the returned `board`. On load: `getBoard()`, apply the selection rule (0/1/many), render. Keep all domain labels sourced from board data; keep the file free of the still-forbidden boundary tokens. (Full implementation is built to the DOM contract; keep functions small and focused.)

- [ ] **Step 2: Update `yigdesk/static/index.html`** — remove the a2a / nested-Codex / single-flow markup and the elements the old `app.js` bound to; keep the shell header, the design-system `<link rel=stylesheet href=styles.css>`, the module `<script type="module" src="app.js">`, and a `<main data-testid="board-root">` container the new `app.js` renders into. Keep the footer neutral text.

- [ ] **Step 3: Verify boundary + syntax + backend still green** — Run: `node --check yigdesk/static/app.js`, then `python -m pytest tests/test_boundary.py tests/test_api.py -q` (domain-neutral components + shell still green). Expected: PASS.

- [ ] **Step 4: Manually smoke-render (optional but recommended)** — `YIGDESK_SCENARIO=data/scenarios/council_discount YIGDESK_LEDGER=runtime/board.jsonl python -m yigdesk.app` after seeding the ledger via a short Python snippet (open/propose/claim), open `http://127.0.0.1:8787`, confirm the board + gate render and an approve+resolve produces the record. (Build the scenario workbook first: `python scripts/build_scenarios.py`.)

- [ ] **Step 5: Commit**

```bash
git add yigdesk/static/app.js yigdesk/static/index.html
git commit -m "feat(web): board view + selector-aware gate panel (app.js/index.html)"
```

---

## Task 8: e2e rewrite + capture cleanup

**Files:**
- Rewrite: `e2e/yigdesk.spec.ts`
- Create: `scripts/seed_board.py` (a board-seeding helper for e2e; the CLI only binds uploads)
- Modify: `scripts/capture.mjs` (point at the board UI or drop the a2a screenshot), `package.json` (ensure `test:e2e` targets the rebuilt spec; there is no `test:council-real` to keep)

- [ ] **Step 1: Create `scripts/seed_board.py`** — a helper the e2e harness runs to populate a ledger deterministically (open a `council_discount` decision, propose one `ok` candidate, post a grounded risk claim), parameterized by `--scenario`, `--ledger`. Reuse `yigdesk.board.build_blackboard`.

```python
from __future__ import annotations
import argparse
from pathlib import Path
from yigdesk.board import build_blackboard

POLICY = {"decision_type": "council_discount",
          "required_approvals": [{"role": "cfo", "verdict": "approve"}],
          "required_claims": [{"type": "risk"}],
          "candidate_selector": "max:headroom"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True)
    a = ap.parse_args()
    bb = build_blackboard(a.scenario, a.ledger)
    bb.open_decision("d1", "Approve the discount?", "council_discount", POLICY,
                     actor="agent:mcp", role="owner")
    bb.propose_candidate("d1", "c1", {"overrides": {"discount": 2}}, actor="finance", role="proposer")
    bb.post_claim("d1", "k1", "risk", "c1", "cogs may rise", ["Deal Inputs!B4"], actor="risk", role="critic")
    print("seeded", a.ledger)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Rewrite `e2e/yigdesk.spec.ts`** — remove every block that drives the removed routes / the retired real-Codex council. New spec: build the scenario workbook (`python scripts/build_scenarios.py`), seed a temp ledger (`python -m scripts.seed_board --scenario data/scenarios/council_discount --ledger <tmp>`), launch the app with `YIGDESK_SCENARIO`/`YIGDESK_LEDGER` pointing there, then via Playwright: load the page, assert `[data-testid="decision-view"]` auto-selected (single decision), assert a `[data-testid="candidate"]` shows the priced verdict, pick the `cfo` role, click `[data-testid="authorize-policy"]` (max:headroom is decision-scoped), click `[data-testid="resolve"]`, assert `[data-testid="resolve-outcome"]` shows the committed record with `chosen_candidate_id`, click resolve again and assert the same record renders and gate buttons are `disabled`. Add a second spec that seeds two decisions and asserts `[data-testid="decision-chooser"]` gates the actions until one is chosen, and a third that seeds an empty ledger and asserts `[data-testid="board-empty"]`.

- [ ] **Step 3: Fix `scripts/capture.mjs` + `package.json`** — repoint or remove the a2a screenshot target in `capture.mjs`; confirm `package.json` `test:e2e` runs the rewritten spec via `run_e2e.mjs` and no script references a deleted target. Keep `test`, `test:boundary`, `test:e2e`, `compare`, `capture` valid.

- [ ] **Step 4: Verify** — Run `python -m pytest -q` (unaffected, still green) and, if a browser is available, `npm run test:e2e` (green). If Playwright/browser is unavailable in this environment, verify the spec + `run_e2e.mjs` at least parse (`node --check` where applicable) and note e2e as runnable where a browser exists.

- [ ] **Step 5: Commit**

```bash
git add e2e/yigdesk.spec.ts scripts/seed_board.py scripts/capture.mjs package.json
git commit -m "test(e2e): board + gate flow over a seeded shared ledger; drop retired UI e2e"
```

---

## Task 9: Final review

- [ ] Dispatch a final code-quality reviewer over the plan's commits (Task 1 base … HEAD): the web never prices/closes (D4), the ledger transaction genuinely serializes cross-process (re-run the concurrency test), `request_resolve` idempotency holds, the board DTO carries policy on both surfaces, selector-aware validation is correct for `human_selected` and `max:<metric>`, the boundary suite is green with the reconciled guard, no dangling references to removed routes remain in `app.js`/`session.js`/`index.html`/`e2e`, and the frontend is domain-neutral. Fix any Critical/Important findings, then re-verify `python -m pytest -q`.

---

## Self-Review (plan author)

- **Spec coverage:** shared ledger + cross-process transaction → T1; idempotent resolve → T2; policy-in-DTO + shared serializer/build_blackboard → T3; boundary reconciliation (approvals now core) → T4; `GET /api/board` + selector-aware `POST /api/board/op` + idempotent `replayed` → T5; board client → T6; board+gate frontend with explicit selection + selector-aware gate + disabled-after-resolve → T7; ledger-concurrency test (T1) + backend gate tests (T5) + e2e selection/selector/retry (T8); reuse/delete map (T6–T8); boundary green (T4/T6/T7). All spec §2–§9 items mapped.
- **Placeholder scan:** core/API tasks carry full test+impl code; the frontend tasks (T7 app.js) specify a concrete DOM/behavior contract the e2e asserts rather than pre-writing every line of vanilla JS — deliberate, since the e2e is the executable contract. No "TBD"/"handle edge cases"/undefined-symbol steps.
- **Type/name consistency:** `build_blackboard(scenario_dir, ledger_path)` / `build_blackboard_from_env()` / `decision_dict` / `board_dict` (T3) are used identically in T5/T8; `BoardSession.getBoard/castApproval/requestResolve` (T6) match the e2e; `data-testid` hooks in T7 match the e2e assertions in T8; the API result shape `{board, result:{pending|record,replayed}}` is consistent T5↔T8.
- **Risk note:** T1's Windows `msvcrt` lock path can't be exercised on the POSIX CI leg (and vice-versa); the concurrency test runs the real path for the host OS. T7/T8 need a browser for the full e2e; where absent, land the code + parse-check and run e2e where a browser exists. T3 touches `blackboard_mcp` + possibly `projection.py` (if it drops decision_type/policy) — keep `test_blackboard_mcp`/determinism green in lockstep.
