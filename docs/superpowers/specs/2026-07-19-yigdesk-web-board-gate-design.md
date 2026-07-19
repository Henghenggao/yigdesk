# Yigdesk — Web Board + Gate (Design Spec)

- Date: 2026-07-19
- Status: Revised after codebase review; ready for implementation planning.
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
- **Cross-process serialization is MVP correctness, not deferred concurrency.** Finance, sales,
  and risk run in parallel, each through its own MCP subprocess, while Flask is another process.
  The ledger must therefore provide a portable inter-process write transaction that covers
  reading the current tail, allocating the next monotonic `seq`, and appending one complete JSON
  line. Resolution must use the same transaction for snapshot + `DecisionRecord.seq` + append,
  so the record sequence always equals its `resolved` op sequence and no write can interleave.
- **Write surface = a single op endpoint** (`POST /api/board/op`), not fine-grained per-action
  routes — it embodies "human actions = the same ops," keeps one validated append path, and
  extends cleanly if human propose/claim is added later.
- **Policy is part of the shared board DTO.** Both MCP `read_board` and `GET /api/board` expose
  `decision_type` and `policy` alongside each decision. The web must know the required approval
  role, required claim types, and candidate selector before it can present a truthful gate.
- **Resolution is idempotent.** Once a decision has a committed `DecisionRecord`, every later
  `request_resolve` returns that same record without appending another `resolved` op.
- **Decision selection is explicit.** The API returns all decisions. The web auto-selects only
  when exactly one exists; with multiple decisions it requires an explicit choice (or a valid
  `decision_id` URL parameter) before enabling gate actions.
- **Reuse** the existing design system (`styles.css`) + domain-neutral components
  (`yig-grid.js`, `yig-model-inspector.js`) + the `index.html` shell; rewrite only `app.js`
  (board logic) and `session.js` (API client).

## 3. Architecture

Multiple processes over one ledger + one scenario:

```
  MCP council subprocesses ──(6 ops via blackboard_mcp, stdio)──┐
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
  to a shared module so the web and MCP surfaces render identically). The shared decision shape
  adds `decision_type` and `policy`; both surfaces change together.
- **Write path:** `POST /api/board/op` validates `{kind, payload}` against an allowlist of the
  two human ops and dispatches to `Blackboard.cast_approval` / `Blackboard.request_resolve`,
  attributed `actor="human:web"`, `role` from the payload (e.g. `cfo`). Returns the fresh
  projection plus, for `request_resolve`, either `{pending: reason}` or the committed
  `DecisionRecord`.
- **Ledger transaction:** strengthen `Ledger`, rather than adding a Flask-only mutex. Every MCP
  server and Flask process must use the same adjacent lock for reads/writes that participate in a
  mutation. `request_resolve` holds that transaction across projection, gate evaluation, sequence
  assignment, and append. A process crash must leave either no new line or one complete JSON line.

## 4. HTTP API (added to app.py; existing health/upload/state/reset stay)

```
GET  /api/board
     → { "decisions": { <id>: { id, question, decision_type, policy, status,
                                candidates{...consequence...}, claims{...},
                                approvals[...], resolution? } } }
       (identical shape to the MCP read_board tool)

POST /api/board/op    body: { "decision_id", "kind", "payload" }
     kind ∈ { "cast_approval", "request_resolve" }   // human-op allowlist; anything else → 400
     cast_approval payload:   { "verdict": "approve|hold|reject", "scope": "<candidate_id|decision_id>", "role": "<role>" }
     request_resolve payload: {}                       // decision_id identifies the target
     → 200 { "board": <projection>,
             "result": <null | {"pending": reason} |
                        {"record": <DecisionRecord>, "replayed": <boolean>}> }
     → 400 on unknown kind / malformed payload / unknown decision (fail-closed, never silent)
```

- Approval validation is selector-aware:
  - for `human_selected`, an `approve` scope must name an existing eligible candidate and that
    scope is the human's selection;
  - for `max:<metric>`, an `approve` scope is the decision id and means "authorize the declared
    policy selector". The UI previews the current policy winner; it must not imply that a
    candidate-scoped click controls the winner;
  - candidate scopes must exist, verdict must be one of the three allowed values, and when a
    policy declares required approval roles the submitted role must be one of them.
- The role is local attribution, not authenticated identity. The UI derives role choices from the
  policy and labels this demo boundary explicitly.
- The first committed `request_resolve` returns `replayed: false`. Every later call returns the
  existing record with `replayed: true` and appends nothing.
- No new auth beyond the existing same-origin + security-headers posture (local demo; the old
  loopback-token gate existed only for the removed nested-Codex spawn). YAGNI.
- No optimistic client concurrency / `base_seq` conflict UI in this MVP. This does NOT defer the
  required cross-process ledger transaction: agent subprocesses already write concurrently even
  in a single-user demo.
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
  grounded claims, and current approvals; plus a **gate panel**. Rejected claim attempts remain
  ledger events and are returned directly to the agent that posted them, but they are deliberately
  absent from `fold(ledger)` and therefore from the board view. A missing required claim appears
  through policy state and `pending(reason)`, not as a projected rejected claim.
- The gate panel is selector-aware. For `human_selected`, it offers candidate-scoped approval. For
  `max:<metric>`, it shows the declared selector and current eligible winner, and offers a
  decision-scoped "Authorize policy resolution" action. The role selector comes from
  `policy.required_approvals`; it is attribution only. Render the resolve outcome explicitly:
  `pending(reason)` vs. the committed `DecisionRecord` (chosen candidate, closed_by, evidence).
  Disable gate actions after resolution; a retried resolve displays the existing record.
- Decision selection states are deterministic: zero decisions → empty board; one → auto-select;
  multiple → show a lightweight id/question/status chooser and keep gate actions disabled until a
  decision is selected. Rich multi-decision navigation remains deferred.
- A **Refresh** button and a light poll pick up agent writes to the shared ledger. Polling reads do
  not choose a different decision behind the user's back.
- **`index.html`** → drop the a2a / nested-Codex / single-flow UI elements; keep the shell,
  header, and design-system tokens.
- The board view stays **domain-neutral in the shipped components**: `yig-grid.js` /
  `yig-model-inspector.js` / `session.js` must remain free of business vocabulary and of
  commercial-mutation vocab (existing `test_boundary.py` guards enforce this). Domain labels
  come from the board data (which came from the scenario), not hardcoded in the components.

## 6. Determinism & boundary invariants (unchanged, must stay green)

- The web NEVER prices or closes: all metrics come from the shared `Blackboard`/`ExpressionEvaluator`,
  and the deterministic gate stays the sole closer (D4). `POST /api/board/op` only appends ops.
- Concurrent writers cannot bypass the inter-process ledger transaction: accepted ops have
  unique, strictly increasing sequence numbers and complete JSON lines before any reader folds
  them.
- A committed resolution is immutable: repeat resolve calls return the same record and never
  append another `resolved` op.
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
  then assert: `GET /api/board` returns policy + priced consequences and omits rejected claims;
  `POST /api/board/op cast_approval` records a valid approval; malformed verdict/role/scope and
  unknown kind/decision → 400; resolve before the required approval → `{pending}`; after it → a
  committed `DecisionRecord`; a repeated resolve returns the identical record and leaves exactly
  one `resolved` op; source bytes remain unchanged.
- **Core/gate:** cover selector-aware approval semantics with two eligible candidates: approving a
  candidate selects it only for `human_selected`; a `max:<metric>` policy ignores candidate
  preference, exposes its winner, and requires decision-scoped authorization in the web API.
- **Ledger concurrency:** spawn at least three writer subprocesses over one temporary ledger while
  a reader polls. Assert every line is valid JSON, sequences are unique and strictly increasing,
  every expected op survives, and a concurrently exercised resolution has
  `DecisionRecord.seq == resolved_op.seq`.
- **e2e (`e2e/yigdesk.spec.ts` rewritten):** a dedicated test helper seeds a ledger with a
  populated decision (the current CLI only binds uploads and is not a board-seeding surface),
  `run_e2e.mjs` serves the app, and the spec loads the
  page, selects the decision, exercises both selector-aware gate presentations, clicks approve +
  resolve, retries resolve once, and asserts the same committed record renders. Cover zero, one,
  and multiple-decision selection states. Restore `npm run test:e2e` and remove the remaining
  retired real-Codex test block; there is no `test:council-real` script to preserve.
- **Boundary:** existing `test_boundary.py` (domain-neutral components; no private-engine name;
  no commercial-mutation vocab in the runtime seam) must stay green over the rebuilt `app.js`/
  `session.js` and the new board routes.

## 8. Reuse / delete map

**Reuse:** `styles.css` (design system), `yig-grid.js` + `yig-model-inspector.js` (board
rendering), `index.html` shell, the existing `_decision_dict` serializer + `_bb()`/blackboard
construction in `blackboard_mcp.py` (extract to a shared module), the existing gate/evaluator
semantics, and the `run_e2e.mjs` harness. Strengthen `Ledger` with the cross-process transaction,
make `Blackboard.request_resolve` idempotent, and extend the shared decision serializer with
`decision_type` + `policy`; these are required core corrections, not UI-only behavior.

**Delete / rewrite:** the retired-route calls and a2a / nested-Codex / ConsequencePacket UI in
`app.js` + `session.js` + `index.html`; the deleted-flow blocks in `e2e/yigdesk.spec.ts`;
`scripts/capture.mjs` screenshot target if it points at removed UI (update or drop).

## 9. MVP scope / YAGNI

In: the two board endpoints (`GET /api/board`, `POST /api/board/op` supporting
`cast_approval` + `request_resolve`), the rebuilt selector-aware board/gate frontend, explicit
decision selection, idempotent resolution, cross-process ledger serialization, backend +
concurrency + e2e tests, and a green boundary suite.

Deferred (not now): human propose/claim/open from the web; real-time push (poll instead);
optimistic client conflict handling beyond mandatory ledger serialization; rich multi-decision
navigation beyond the minimal explicit chooser; auth / multi-tenant. Each is a clean later
extension of the single op endpoint.
