# Yigdesk — Web Board + Gate (Design Spec)

- Date: 2026-07-19
- Status: Approved in brainstorming; ready for implementation planning.
- Builds on: `2026-07-19-yigdesk-decision-blackboard-design.md` (§3 unit 4 "Human Bridge / Board UI", §11 MVP item 4). This is the board-UI rebuild that finishes that unit after the Plan 2 standalone cut demolished the old single-flow HTTP surface.

## 1. Goal

Rebuild Yigdesk's web surface as a **read + gate** board over the deterministic blackboard,
replacing the now-dead static UI (`static/app.js` / `session.js` still call the removed
`/api/analyze`, `/api/proposals/*`, `/api/agent-runs`, `/api/inspect`, `/api/council-status`
routes). The web board renders the live board projection and lets a **human drive the gate**
(cast approvals, request resolve) on the **same ledger** the MCP council agents write to, so
subagents and humans converge on one decision surface — the "deterministic membrane" of the
parent design.

## 2. Locked decisions (from brainstorming)

- **Scope = board view + gate panel.** The human READS the board (decisions, candidates with
  priced consequences + evidence, claims, approvals, resolution) and performs exactly two write
  actions: `cast_approval` (approve/hold, scoped to a candidate or the decision) and
  `request_resolve`. The human does NOT open decisions, propose candidates, or post claims from
  the web (those remain MCP-agent actions). Matches parent §3 unit 4 / §11.
- **Candidate/claim population = live MCP agents only.** No demo-seed path in the shipped app.
  Candidates and claims appear on the web board only because MCP council agents
  (finance/sales/risk/optimizer) appended them to the shared ledger via `blackboard_mcp`. The
  web board is the human end of the bridge; the agent end is the existing 6-op MCP surface.
- **One shared ledger.** The Flask app and the `blackboard_mcp` stdio server construct a
  `Blackboard` over the SAME `YIGDESK_LEDGER` + `YIGDESK_SCENARIO`. This is what makes agent
  writes visible to the human and human writes visible to agents.
- **Write surface = a single op endpoint** (`POST /api/board/op`), not fine-grained per-action
  routes — it embodies "human actions = the same ops," keeps one validated append path, and
  extends cleanly if human propose/claim is added later.
- **Reuse** the existing design system (`styles.css`) + domain-neutral components
  (`yig-grid.js`, `yig-model-inspector.js`) + the `index.html` shell; rewrite only `app.js`
  (board logic) and `session.js` (API client).

## 3. Architecture

Two processes over one ledger + one scenario:

```
  MCP council agents ──(6 ops via blackboard_mcp, stdio)──┐
                                                          ▼
                                    YIGDESK_LEDGER (append-only board.jsonl)
                                    YIGDESK_SCENARIO (model.json + workbook)
                                                          ▲
  Human (browser) ──(GET /api/board · POST /api/board/op)─┘
                        Flask app.py  ──Blackboard(ledger, evaluator, source)
```

- **Blackboard construction (app.py):** a helper builds a `Blackboard` exactly as
  `blackboard_mcp._bb()` does — `ModelSource(scenario/workbook, input_refs)` +
  `ExpressionEvaluator(model)` + `Blackboard(YIGDESK_LEDGER, evaluator, source)`. app.py reads
  the same two env vars (`YIGDESK_SCENARIO` required for board routes, `YIGDESK_LEDGER` default
  `runtime/board.jsonl`). Reuse, do not duplicate, the construction logic (extract a shared
  `build_blackboard(scenario_dir, ledger_path)` used by both `blackboard_mcp` and `app.py`).
- **Read path:** `GET /api/board` → `Blackboard.project()` serialized to the SAME JSON shape
  the MCP `read_board` tool returns (reuse the existing `_decision_dict` serializer — extract it
  to a shared module so the web and MCP surfaces render identically).
- **Write path:** `POST /api/board/op` validates `{kind, payload}` against an allowlist of the
  two human ops and dispatches to `Blackboard.cast_approval` / `Blackboard.request_resolve`,
  attributed `actor="human:web"`, `role` from the payload (e.g. `cfo`). Returns the fresh
  projection plus, for `request_resolve`, either `{pending: reason}` or the committed
  `DecisionRecord`.

## 4. HTTP API (added to app.py; existing health/upload/state/reset stay)

```
GET  /api/board
     → { "decisions": { <id>: { id, question, status, candidates{...consequence...},
                                claims{...}, approvals[...], resolution? } } }
       (identical shape to the MCP read_board tool)

POST /api/board/op    body: { "decision_id", "kind", "payload" }
     kind ∈ { "cast_approval", "request_resolve" }   // human-op allowlist; anything else → 400
     cast_approval payload:   { "verdict": "approve|hold|reject", "scope": "<candidate_id|decision>", "role": "<role>" }
     request_resolve payload: {}                       // decision_id identifies the target
     → 200 { "board": <projection>, "result": <null | {"pending": reason} | {"record": <DecisionRecord>}> }
     → 400 on unknown kind / malformed payload / unknown decision (fail-closed, never silent)
```

- No new auth beyond the existing same-origin + security-headers posture (local demo; the old
  loopback-token gate existed only for the removed nested-Codex spawn). YAGNI.
- No optimistic concurrency (deferred in Plan 1; single-user demo). The ledger remains
  single-writer-serialized within the process.
- **Board scenario vs. upload session are orthogonal.** The board routes operate on
  `YIGDESK_SCENARIO` + `YIGDESK_LEDGER` (the same the MCP agents use). The existing
  `/api/upload`/`/api/state`/`/api/reset` session-bind path (reused `session`/`importer`) is a
  SEPARATE capability and is NOT wired into the board in this MVP — binding an uploaded workbook
  as the board's live scenario remains the known deferred follow-up already flagged in the
  council `SKILL.md`. Keep the two clearly separate; do not couple them here.

## 5. Frontend (rebuild)

- **`session.js`** → a thin board client: `getBoard()` (GET /api/board), `castApproval(...)`,
  `requestResolve(...)` (both POST /api/board/op). Remove the retired `/api/analyze`,
  `/api/inspect`, `/api/proposals/*`, `/api/agent-runs`, `/api/council-status` methods and the
  old `capabilities`/ConsequencePacket vocabulary.
- **`app.js`** → render one decision's board: candidates with their priced consequence
  (verdict + metrics + evidence refs, via `yig-grid`/`yig-model-inspector` where they fit),
  claims (with grounded/rejected status), and current approvals; plus a **gate panel** —
  approve/hold controls scoped to a chosen candidate, a role selector (the human's role, e.g.
  `cfo`), and a **Resolve** button. Render the resolve outcome explicitly: `pending(reason)`
  (what's still missing) vs. the committed `DecisionRecord` (chosen candidate, closed_by,
  evidence). A **Refresh** button (and a light poll) picks up agent writes to the shared ledger.
- **`index.html`** → drop the a2a / nested-Codex / single-flow UI elements; keep the shell,
  header, and design-system tokens.
- The board view stays **domain-neutral in the shipped components**: `yig-grid.js` /
  `yig-model-inspector.js` / `session.js` must remain free of business vocabulary and of
  commercial-mutation vocab (existing `test_boundary.py` guards enforce this). Domain labels
  come from the board data (which came from the scenario), not hardcoded in the components.

## 6. Determinism & boundary invariants (unchanged, must stay green)

- The web NEVER prices or closes: all metrics come from the shared `Blackboard`/`ExpressionEvaluator`,
  and the deterministic gate stays the sole closer (D4). `POST /api/board/op` only appends ops.
- Source read-only (D1): the board routes never mutate the scenario workbook.
- Fail-closed (D3): unknown op kind / decision / malformed payload → 400 with a reason; a
  resolve that doesn't satisfy policy → `{pending: reason}`, never a silent close.
- Domain-neutral core + shipped components; no private-engine ("Yigrid") token (the T7 boundary
  guard already scans `app.js`/`session.js` transitively via `yigdesk/**`).

**Boundary-test reconciliation (required — this design legitimately changes one guard).**
`tests/test_boundary.py::test_public_runtime_has_no_commercial_mutation_or_trust_service`
currently forbids the tokens `approv(e|al)` and `human[-_ ]?gated` in the runtime seam
(`app.py`, `cli.py`, the static JS). That guard encoded the OLD open-core-demo constraint —
"the public read-only slice cannot approve or gate; approvals are the private commercial layer."
That premise no longer holds: in the standalone blackboard, **approvals + human-gated resolution
are the core product** (the deterministic gate — the entire point of this human bridge), and the
MCP surface already exposes `cast_approval`/`request_resolve`. So the plan must **remove
`approv(e|al)` and `human[-_ ]?gated` from that guard's forbidden set** and re-aim its intent at
what IS still out of scope for a standalone decision blackboard: **mutation of the SOURCE model**
and **external commercial/trust services**. The genuinely-still-forbidden tokens STAY:
`write[-_ ]?back` (source is read-only — D1), `reopen` (a resolved `DecisionRecord` is
immutable), `vault`, `attest`, `sign(ing|ature)`, `audit[-_ ]?trail`, `multi[-_ ]?tenant`,
`\bsso\b`. This is a corrected boundary, not a weakened one — approvals of a *decision* (ledger
ops) are in-scope; mutating the *source* or shipping a trust/attestation service is not. The
board code must still avoid those still-forbidden phrases (call it "ledger"/"board", never
"audit-trail"; never "write back" to source).

## 7. Testing

- **Backend (`tests/test_api.py` extended, or a new `tests/test_board_api.py`):** seed the shared
  ledger by driving the core `Blackboard` API directly (simulate the agent side — open a
  `council_discount` decision, propose candidates, post a grounded risk claim; NO live Codex),
  then assert: `GET /api/board` returns the projection with priced consequences; `POST
  /api/board/op cast_approval` records it; `POST request_resolve` before the required cfo
  approval → `{pending}`; after it → a committed `DecisionRecord`; unknown kind / decision → 400;
  source bytes unchanged across the flow.
- **e2e (`e2e/yigdesk.spec.ts` rewritten):** a fixture seeds a ledger (via a small Python
  helper or the CLI) with a populated decision, `run_e2e.mjs` serves the app, the spec loads the
  page, clicks approve + resolve, and asserts the committed record renders. Restores
  `npm run test:e2e` / drops the removed `test:council-real`.
- **Boundary:** existing `test_boundary.py` (domain-neutral components; no private-engine name;
  no commercial-mutation vocab in the runtime seam) must stay green over the rebuilt `app.js`/
  `session.js` and the new board routes.

## 8. Reuse / delete map

**Reuse:** `styles.css` (design system), `yig-grid.js` + `yig-model-inspector.js` (board
rendering), `index.html` shell, the existing `_decision_dict` serializer + `_bb()`/blackboard
construction in `blackboard_mcp.py` (extract to a shared module), the `Blackboard`/gate/evaluator
core (unchanged), `run_e2e.mjs` harness.

**Delete / rewrite:** the retired-route calls and a2a / nested-Codex / ConsequencePacket UI in
`app.js` + `session.js` + `index.html`; the deleted-flow blocks in `e2e/yigdesk.spec.ts`;
`scripts/capture.mjs` screenshot target if it points at removed UI (update or drop).

## 9. MVP scope / YAGNI

In: the 3 board routes (`GET /api/board`, `POST /api/board/op` for cast_approval + request_resolve),
the rebuilt board+gate frontend, shared-ledger wiring, backend + e2e tests, boundary green.

Deferred (not now): human propose/claim/open from the web; real-time push (poll instead);
optimistic concurrency; multi-decision board navigation beyond what one scenario needs; auth /
multi-tenant. Each is a clean later extension of the single op endpoint.
