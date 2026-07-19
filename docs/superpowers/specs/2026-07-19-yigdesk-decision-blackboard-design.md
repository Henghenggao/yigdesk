# Yigdesk — Deterministic Decision Blackboard (Design Spec)

- Date: 2026-07-19
- Status: Approved in brainstorming; ready for implementation planning.

> Naming note: this spec deliberately does **not** name the private parent engine, so the
> repository carries no trace of any association with it (see §9).

## 1. Vision

Yigdesk is a **standalone, deterministic decision-making blackboard** that bridges AI
subagents and humans. Multiple subagents and human operators converge on decisions over a
shared surface; a deterministic core prices every candidate identically, forces every claim
to be grounded in real evidence, records everything to an append-only ledger, and closes
decisions by deterministic rule.

Delivered as an **MCP-native service + append-only ledger + web board/gate UI**. It contains
**zero business-scenario logic** and has **zero dependency on, or reference to, any
external/private engine**. Scenarios are pure **data + config**.

## 2. Principles / boundaries

- **Source read-only.** The only thing ever committed is a `DecisionRecord`; source models
  are never mutated (bytes-unchanged, fail-closed).
- **No scenario logic in code.** All domain formulas/constraints live in data/config.
- **Perfect separation** from any private engine — no imports, IPC, MCP calls, names, or
  docs (see §9), enforced by a boundary test.
- **Deterministic membrane.** LLM subagents + humans are non-deterministic *inputs*; the core
  is a deterministic function of the op-log.
- **Reuse first, delete cleanly.** Maximize reuse of existing modules; delete fully-obsolete
  parts with no traces — no parallel legacy endpoints, dead code, or aliases.
- **YAGNI.**

## 3. Architecture (5 units)

1. **Blackboard Core (domain-neutral).** Holds the append-only ledger; folds the op-log into a
   board projection; runs the deterministic gate.
   Interface: `append(op)->seq` · `project()->Board` · `resolve(decision_id)->DecisionRecord|pending`.
   Depends only on the Evaluator interface. Zero domain vocabulary; no UI/MCP knowledge.
2. **Evaluator (pluggable interface + one shipped general impl).**
   `price(action, state)->consequence` · `ground(refs)->ok|fail`. Deterministic, read-only.
   Shipped impl = a general **model-from-data** evaluator (§7). The interface is generic; no
   engine-specific evaluator ships or is named.
3. **MCP Surface (agent bridge).** Tools: `open_decision`, `propose_candidate`, `post_claim`,
   `read_board`, `cast_approval`, `request_resolve`. Writes = `append(op)`; reads = `project()`.
4. **Human Bridge / Board UI.** Renders `project()` into a board view (decision; candidates with
   consequences + evidence; claims) + a gate panel (approve/hold/select). Human actions = the
   same ops. Reuses the existing domain-neutral web components + design system.
5. **Domain App (data + config only).** Decision types, policies, model definitions, seed data.
   All domain vocabulary lives here.

Invariants: (a) Core fully domain-neutral; (b) Evaluator pluggable & read-only; (c) humans and
agents use the **same ops**, different surfaces.

## 4. Protocol / data model

```
Op            { seq, actor, role, kind, payload, base_seq, wall_ts? }   // append-only entry
  kind ∈ open_decision | propose_candidate | post_claim | cast_approval | request_resolve | resolved
  seq       = Core-assigned monotonic integer (total order = logical clock)
  base_seq  = seq the actor observed (optimistic concurrency)
  wall_ts   = annotation only; never read by the fold

Decision      { id, question, decision_type, policy_ref, status(open|resolved|held),
                candidates[], claims[], approvals[], resolution? }
Candidate     { id, decision_id, author, action, consequence, evidence_refs[], status(priced|hold) }
Claim         { id, decision_id, author, type(evidence|critique|risk|assumption),
                target(decision|candidate), body, grounded_refs[], status(grounded|rejected) }
Approval      { id, decision_id, actor, role, verdict(approve|hold|reject), scope }
Policy        { decision_type, required_approvals[], constraints[], candidate_selector }
  candidate_selector = "human_selected" | deterministic selector (e.g. max metric passing constraints)
DecisionRecord{ decision_id, chosen_candidate_id, closed_by(policy|human), rationale,
                evidence_refs[], approvals[], evaluator_revision, source_fingerprint,
                consequence_id, seq }   // the only committed artifact; lives in a `resolved` op; immutable

consequence   { verdict(ok|hold), metrics:[{id,label,value,before,after,unit}],
                evidence_refs[], fingerprint }   // GENERIC envelope — no scenario-specific fields
action        = domain-neutral descriptor the Evaluator understands (e.g. {target, op, value})
```

`resolve` is not hand-authored: `request_resolve` is an op; the Core runs the deterministic
gate; if it closes, the Core itself appends a `resolved` op containing the `DecisionRecord`
(so the outcome is in the log and replayable).

## 5. Determinism guarantees (D1–D4) — all testable

- **D1 Pricing is deterministic.** Same `evaluator_revision` + same `action` → byte-identical
  consequence + evidence_refs. Source read-only; bytes-unchanged proof carried in the packet.
- **D2 State = pure fold.** `Board = fold(op-log)`; replaying the same log reproduces an
  identical projection + DecisionRecords. `seq` is the logical clock; wall-clock never enters
  the fold. Serialization is canonical (sorted keys) for byte-stability.
- **D3 Grounding mandatory / fail-closed.** A claim/candidate that does not resolve to real
  evidence is rejected (not admitted to state). A candidate with incomplete evidence gets
  `consequence.verdict=hold` and cannot be selected.
- **D4 Resolution is a deterministic function** of (candidates, claims, approvals, policy,
  evaluator_revision). No LLM in the close path; same inputs → same DecisionRecord.

Concurrency (part of D2): optimistic — ops carry `base_seq`; the Core assigns a total-order
`seq` on append; a write whose precondition is stale is rejected with the current seq and the
caller re-reads. The ledger is single-writer-serialized, so there is no merge ambiguity.

## 6. Coordination flow & fail-closed

1. `open_decision(question, type, policy)` → Decision(open).
2. subagents (parallel) `propose_candidate(action)` → `price()` → Candidate{consequence+evidence}
   (or `hold` if evidence incomplete).
3. subagents/humans `post_claim(type, target, refs)` → `ground()` → grounded, else **rejected**.
4. humans/agents `cast_approval(verdict, role, scope)`.
5. any `request_resolve` → deterministic gate(policy): needs-human-select / approvals-missing /
   constraints-fail → `pending(reason)` (never silent). Satisfied → Core appends `resolved`
   (DecisionRecord); source never mutated.
6. any `read_board` → identical deterministic projection.

Fail-closed catalogue: ungrounded contribution → rejected(reason); incomplete evidence → hold
(not selectable); resolve without conditions → pending(reason); stale write → rejected(current
seq); evaluator error/unavailable → price/ground fail closed (no guessed figures); write to
source or to a resolved record → rejected (immutable).

## 7. Evaluator: general, model-from-data, zero scenario lock

- The model (named metrics, derived formulas, constraints) is declared in **data/config**,
  never in code.
- Shipped **ExpressionEvaluator**: evaluates declared formulas deterministically (Decimal) over
  grounded source cells; returns the generic `consequence` envelope; `hold` when required
  evidence is missing; carries the source fingerprint (bytes-unchanged).
- Reuses the existing Decimal / HOLD / evidence-cell machinery. The previously **hardcoded
  scenario formulas are deleted from code** and expressed as demo data.
- The interface is generic: a third party may implement it for their own engine, in their own
  repository. Yigdesk ships none such and names none.

## 8. Reuse / delete map

**Reuse / adapt:** `engine.py` Decimal/HOLD/evidence machinery → ExpressionEvaluator internals
(formulas removed to data); `workbook.py` inspect/snapshot/fingerprint → evidence + `ground()`;
`static/yig-grid.js` + `yig-model-inspector.js` → board rendering; `static/session.js` → board
session; `index.html`/`app.js`/`styles.css` → design system + shell (page rebuilt to board);
FastMCP scaffolding + guardrail instructions → MCP Surface (toolset replaced); Flask shell +
security headers → board read/append API; `scenarios.json` → domain-app seed; `test_boundary.py`
→ kept & strengthened; `test_engine.py` → evaluator tests.

**Delete (no traces):** the single-shot `/api/analyze` + `demo-consequence-packet` one-decision
semantics; the old 3 fixed read-only MCP tools as the primary surface; `mcp_tools.py` (old
read-only HTTP client); `cli.py` single-flow commands (→ board CLI); old contract assertions in
`test_api.py`/`test_mcp_protocol.py`/`test_mcp_tools.py`; single-shot-only fields in
`scenarios.json`; **`docs/OPEN_CORE_BOUNDARY.md` and all "open-core slice" framing** (§9);
**the hardcoded scenario formulas** in `engine.py` (→ data).

## 9. Separation from any private engine ("perfect cut")

Enforced rules:
- **Code:** no import / subprocess / IPC / MCP client targeting a private engine; the Evaluator
  interface is generic (not engine-shaped).
- **Tokens:** the shipped surface (`yigdesk/**`, `README`, shipped `docs/`, `tests/`) contains
  no reference to any private engine's name — enforced by extending `test_boundary.py`'s
  forbidden-token scan (same mechanism already used for legacy brand/machine markers).
- **Docs:** delete `docs/OPEN_CORE_BOUNDARY.md`; rewrite `README.md` / `docs/ARCHITECTURE.md`
  to describe Yigdesk on its own terms only.
- **Demo data:** re-authored in Yigdesk-native model-from-data format; runs entirely on
  Yigdesk's own evaluator; needs no external mapping schema or external kernel.
- This planning spec omits the private engine's literal name for the same reason.

Scope consequence: Yigdesk does **not** re-implement a heavy multi-dimensional compute kernel.
A light, self-contained deterministic evaluator (§7) is sufficient for the blackboard. Heavy
engines can implement the generic Evaluator interface in a separate repo.

## 10. Testing

- **Determinism conformance suite:** fixed op-log → identical board + DecisionRecords across
  replays; same action+revision → identical consequence; grounding rejects ungrounded;
  `resolve` is a pure function; concurrency ordering deterministic.
- **Boundary tests (strengthened):** Core stays domain-neutral; writes only ever produce
  Op/DecisionRecord and never touch source (bytes-unchanged); no LLM in the resolve path;
  **no private-engine token** anywhere in the shipped surface.
- **Evaluator contract tests:** the shipped ExpressionEvaluator satisfies price/ground +
  determinism over the demo models.

## 11. MVP scope

End-to-end spine, fully standalone:
1. **Core** — append-only JSONL ledger + fold projection + deterministic gate.
2. **ExpressionEvaluator** — model-from-data, reusing the Decimal/HOLD/evidence machinery.
3. **MCP Surface** — the 6 op tools + `read_board` (FastMCP scaffolding + guardrails).
4. **Board UI + gate** — extend the existing web components; minimal Flask read/append.
5. **≥2 scenarios as pure data** to prove no-lock — e.g. a discount-approval model + a
   structurally different one (a multi-dimensional dataset re-authored Yigdesk-native).
6. **Tests** — determinism conformance + strengthened boundary + evaluator contract.

Deferred: richer policy DSL; auth / multi-tenant; persistence beyond JSONL; real-time push;
any third-party heavy-compute evaluator.

## 12. Future option: Rust Core

Not now — conflicts with the reuse constraint, low ROI (heavy compute lives in the evaluator,
not the core), and determinism is a discipline achievable in Python. The Core's crisp interface
+ conformance suite keep a later **Rust reimplementation of the Core alone** a clean drop-in if
a concrete trigger appears (embeddable single-binary kernel / stronger audit guarantees / the
core becoming CPU-bound).
