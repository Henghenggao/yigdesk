# Yigdesk Council Integration + Demolition — Implementation Plan (Plan 2)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Migrate the existing Codex Council/A2A subsystem to run *on* the deterministic blackboard (Plan 1), then delete the obsolete old surface, and cut all association with the private engine — keeping the test suite green at every step.

**Depends on:** Plan 1 (branch `feat/decision-blackboard-backend`, blackboard backend complete, 147 tests green).

**Design of record:** spec `2026-07-19-yigdesk-decision-blackboard-design.md` §13.

**Ordering principle:** additive core features first (green), then migrate consumers onto the new surface (rewriting their tests in lockstep), then delete the now-unused old surface, then docs/cut. Never leave the suite red between commits.

**Tech Stack:** Python 3.11, existing `mcp`/`openpyxl`/`pytest`; the blackboard from Plan 1 (`yigdesk/core`, `yigdesk/evaluator`, `yigdesk/blackboard_mcp.py`).

---

## Task 1 (additive): claim-gating Policy in the gate (closes review finding I4)

**Files:** Modify `yigdesk/core/gate.py`; Test: append to `tests/test_gate.py`.

Add support for `policy["required_claims"]` — a list of `{"type": "..."}` requirements; `resolve` returns `Pending` unless the decision has ≥1 **grounded** claim of each required type. This is what lets a `decision_type` demand "≥1 grounded risk claim before close" (the a2a intent, re-expressed as Policy).

- [ ] **Step 1 — failing test** (append to `tests/test_gate.py`):
```python
def test_required_claim_type_blocks_until_present():
    from yigdesk.core.model import Decision, Candidate, Consequence, Metric, Approval, Claim
    d = Decision("d1","q","x",{"required_approvals":[{"role":"cfo","verdict":"approve"}],
                               "candidate_selector":"max:m","required_claims":[{"type":"risk"}]})
    d.candidates["c1"] = Candidate("c1","a",{}, Consequence("ok",[Metric("m","M","5","5","5","")],[],"f"))
    d.approvals = [Approval("h","cfo","approve","c1")]
    r = resolve(d, "rev")
    assert isinstance(r, Pending) and "claim" in r.reason
    d.claims["cl1"] = Claim("cl1","risk_agent","risk","c1","cogs may rise",["Deal!B4"],"grounded")
    assert not isinstance(resolve(d, "rev"), Pending)
```
- [ ] **Step 2 — run, verify FAIL** (`python -m pytest tests/test_gate.py -k required_claim -v`).
- [ ] **Step 3 — implement**: in `gate.py`, after the `_approvals_met` check in `resolve`, add:
```python
    for req in d.policy.get("required_claims", []):
        if not any(c.status == "grounded" and c.type == req["type"] for c in d.claims.values()):
            return Pending(f"required claim of type {req['type']} missing")
```
- [ ] **Step 4 — run, verify PASS**; full suite green.
- [ ] **Step 5 — commit** `feat(core): claim-gating policy (resolve requires grounded claim types)`.

---

## Task 2 (additive): session→source adapter (reuse importer + session)

**Files:** Create `yigdesk/evaluator/session_source.py`; Test: `tests/test_session_source.py`.

Provide a function that, given a bound session directory (produced by the existing `session.bind_synthetic_session` / `importer.import_synthetic_workbook`), returns a `ModelSource` + the model config for the blackboard — the "uploaded XLSX → source behind open_decision" path. **Read-only; reuse the existing `session`/`importer` modules, do not modify them.**

- [ ] **Step 1 — read** `yigdesk/session.py` and `yigdesk/importer.py` to learn the manifest shape + how the projection workbook + revision id are exposed (`load_active_session`, `live_revision_id`).
- [ ] **Step 2 — failing test** `tests/test_session_source.py`: bind a synthetic session via the existing API, then assert `session_source.source_for_active_session(vault)` returns a `ModelSource` whose `.fingerprint` matches the session's source fingerprint and whose `base_inputs()` reads the expected cells. (Write the test against the real `session` API you read in Step 1.)
- [ ] **Step 3 — run, verify FAIL.**
- [ ] **Step 4 — implement** `session_source.py`: load the active session, map its projection workbook + a model config (input_refs/metrics from a scenario `model.json`) into a `ModelSource`. Keep it a thin adapter over `session`/`importer` + `ModelSource`.
- [ ] **Step 5 — run, verify PASS**; full suite green.
- [ ] **Step 6 — commit** `feat(evaluator): session→ModelSource adapter (upload path reuse)`.

---

## Task 3 (migration): move the council pricer to the model-from-data evaluator

**Files:** Create `data/scenarios/council_discount/{model.json,policy.json}` mirroring the council's discount scenario (net ARR / gross margin / headroom, margin-floor constraint, `required_claims:[{"type":"risk"}]`, `candidate_selector:"max:headroom"`); Test: `tests/test_council_scenario.py`.

- [ ] **Step 1** — read `yigdesk/engine.py` to capture the exact 5-formula arithmetic + margin-floor rule, and express it as `data/scenarios/council_discount/model.json` (formulas as data) + `policy.json`.
- [ ] **Step 2 — failing test**: price the council discount candidate through `ExpressionEvaluator` on this scenario and assert the same figures `engine.py` produced (net ARR 880, headroom 5.5 for the canonical inputs), and that a missing-COGS input yields HOLD.
- [ ] **Step 3 — run FAIL; author the JSON; run PASS.** Do NOT delete `engine.py` yet (Task 6).
- [ ] **Step 4 — commit** `feat(data): council discount scenario as model-from-data`.

---

## Task 4 (migration): rewrite the four role personas + SKILL.md + AGENTS.md to the 6 ops

**Files:** Modify `.codex/agents/{finance_analyst,sales_advocate,risk_challenger,decision_optimizer}.toml`, `.agents/skills/yigdesk-council/SKILL.md`, `AGENTS.md`; Test: rewrite `tests/test_codex_council_config.py`.

Re-express each role against the 6 blackboard ops (open_decision/propose_candidate/post_claim/cast_approval/request_resolve/read_board) instead of the 8 read-only tools, and point `[mcp_servers.yigdesk]` at `python -m yigdesk.blackboard_mcp`. Map: finance/sales → `propose_candidate`; risk → `post_claim(type=risk)` (+ a boundary candidate); optimizer → `read_board` + a non-binding advisory `post_claim`, and **no** `request_resolve` access (the deterministic gate closes). SKILL.md orchestration: open_decision → spawn roles (propose/claim) → cast_approval → request_resolve → report the committed DecisionRecord.

- [ ] **Step 1** — read all four TOMLs + SKILL.md + AGENTS.md + the current `tests/test_codex_council_config.py` to learn its literal-substring assertions.
- [ ] **Step 2 — rewrite the config-contract test** first to assert the NEW op allowlists per role (finance/sales have `propose_candidate`; risk has `post_claim`; optimizer has `read_board` but NOT `request_resolve`), and the new server launch arg. Run → FAIL.
- [ ] **Step 3 — rewrite the four TOMLs + SKILL.md + AGENTS.md** to satisfy the new test + the mapping above. Run → PASS.
- [ ] **Step 4 — commit** `refactor(council): personas + orchestration speak the 6 blackboard ops`.

---

## Task 5 (migration): rewrite benchmark.py + agent.py onto the new surface

**Files:** Modify `yigdesk/agent.py`, `yigdesk/benchmark.py`; Tests: update `tests/test_agent_runner.py`, `tests/test_benchmark.py`.

`CodexRunner` (agent.py) currently spawns Codex against the old 3-tool surface and verifies field-drift; rewrite it to drive `blackboard_mcp` (the 6 ops) and rely on the blackboard's structural guarantees (a number only becomes real via `propose_candidate`), removing the field-drift verifier that D1/D3 make redundant. `benchmark.py` currently reaches into `mcp_tools.YigdeskToolClient` (dead) + `app._capture_agent_snapshot`; rewrite the "bare Codex vs Yigdesk-assisted" comparison to use the blackboard surface.

- [ ] **Step 1** — read `agent.py`, `benchmark.py`, and their tests fully; identify every use of `mcp_tools`, old 3 tools, `app._capture_agent_snapshot`, `engine`.
- [ ] **Step 2** — update the tests to the new surface first (TDD), then rewrite the modules to pass. Preserve the tests' real guarantees (sandbox isolation, secret hygiene, bare-vs-assisted scoring) on the new ops. Run → PASS after each module.
- [ ] **Step 3 — commit** (one per module) `refactor(agent): drive blackboard 6-op surface` / `refactor(benchmark): compare bare vs blackboard-assisted on 6 ops`.

---

## Task 6 (demolition): delete the obsolete old surface

**Files:** Delete `yigdesk/mcp_tools.py`, `yigdesk/mcp_server.py`, `yigdesk/a2a.py`, `yigdesk/engine.py`; remove the `/api/analyze` route + single-flow bits from `yigdesk/app.py`; trim `yigdesk/cli.py` to board commands; delete `docs/OPEN_CORE_BOUNDARY.md`. Rewrite/delete `tests/test_a2a.py` (→ a ledger/DecisionRecord audit test), `tests/test_mcp_tools.py`, `tests/test_mcp_protocol.py`, `tests/test_engine.py`, and the analyze bits of `tests/test_api.py`.

**Precondition:** Tasks 3–5 must have removed every remaining importer of these modules. Verify first.

- [ ] **Step 1** — `grep -rn "mcp_tools\|mcp_server\|from .a2a\|import a2a\|from .engine\|import engine\|/api/analyze"` across `yigdesk/`, `tests/`, `scripts/`; confirm only the files being deleted/rewritten reference them.
- [ ] **Step 2** — write the replacement `tests/test_council_audit.py` (verify a resolved decision's ledger + DecisionRecord grounding, replacing a2a's post-hoc sequence check) FIRST.
- [ ] **Step 3** — delete the modules + tests listed; trim app.py/cli.py. Run full suite → green (rewrite/adjust any straggler).
- [ ] **Step 4 — commit** `chore: remove obsolete single-flow + a2a surface (superseded by the blackboard)`.

---

## Task 7 (the cut): standalone docs + no-private-engine boundary

**Files:** Rewrite `README.md`, `docs/ARCHITECTURE.md`; extend `tests/test_boundary.py` (or add `tests/test_no_private_engine.py`).

- [ ] **Step 1** — add a boundary test asserting the shipped surface (`yigdesk/**`, `README.md`, shipped `docs/`, `tests/`) contains no reference to the private engine's name (use a split-string literal so the test file itself carries no token, per spec §9). Run → it will FAIL on current README/docs.
- [ ] **Step 2** — rewrite `README.md` + `docs/ARCHITECTURE.md` to describe Yigdesk standalone (the decision blackboard, the 6 ops, run commands, the two scenarios + the council app), purging the private-engine name. Run → PASS.
- [ ] **Step 3 — commit** `docs: standalone README/ARCHITECTURE + no-private-engine boundary guard`.

---

## Task 8: final review

- [ ] Dispatch a final code-quality reviewer over `feat/decision-blackboard-backend` (Plan 1 base … HEAD) focused on the migration + demolition: no dangling imports, tests still assert real guarantees, the council genuinely runs on the blackboard, determinism/domain-neutrality intact, and zero private-engine association remains. Fix any Critical/Important findings, then re-verify.

---

## Self-Review (plan author)

- **Spec §13 coverage:** claim-gating Policy → T1; upload→source → T2; pricer→model-from-data → T3; personas→6 ops → T4; benchmark/agent→6 ops → T5; retire a2a + delete old surface → T6; the cut (no private-engine name, standalone docs) → T7. All §13 items covered.
- **Green-preserving order:** additive (T1,T2,T3) → migrate consumers + their tests (T4,T5) → delete only after no importers remain (T6, guarded by a grep precondition) → docs/cut (T7). No step leaves the suite red.
- **Risk note:** T5/T6 touch real working code; implementers must READ the actual files first (these are integration/judgment tasks, not verbatim transcription) and rewrite tests in lockstep. Deferred from Plan 1: optimistic-concurrency (I6) remains a follow-up, not in this plan.
