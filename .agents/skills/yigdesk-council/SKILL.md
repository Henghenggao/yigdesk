---
name: yigdesk-council
description: Turn one natural-language discount request into a real local blackboard run - bind the synthetic workbook into an immutable session, open a decision on the packaged demo scenario, spawn the four Codex council roles over the six blackboard ops (read_board, propose_candidate, post_claim), then let the deterministic gate cast approval and resolve, and return the committed DecisionRecord. Use when the user asks to analyze a synthetic workbook, decide whether a discount such as 2% makes sense, find the financially safe percentage, challenge a proposal with subagents, or run the Yigdesk decision-council demo without leaving Codex.
---

# Yigdesk Council

Turn one natural-language request into a real local decision flow: bind the
synthetic workbook into an immutable session, open a decision on the deterministic
blackboard's packaged demo scenario, coordinate the project's specialist Codex agents over the six blackboard
ops, and let the deterministic gate - not the model - close the decision. The board
is an append-only ledger, so the ledger itself is the audit and the committed
DecisionRecord is the outcome. The browser is an optional evidence view, not a
required control surface.

## Scope guard

- Accept only a generated synthetic `.xlsx` carrying the required `SYNTHETIC` marker.
- Never write back to the source model: the blackboard prices candidates and records
  decisions to its own ledger but never mutates the workbook. Never approve outside
  the gate, send, sign, message, or connect a production system.
- Do not copy a file outside the ignored Yigdesk runtime except when the user asks.
- Treat a `pending` gate result, a `hold` candidate, a bind rejection, missing
  evidence, a rejected (ungrounded) claim, or an incomplete role as terminal for that
  attempt. Do not release an unverified result.
- Never invent market evidence. Financial feasibility is not commercial optimality.
- Keep orchestration in the current Codex Work task. Do not launch a nested
  `codex exec`; use the project custom agents and their inherited Yigdesk MCP client.

## 1. Resolve the request

First, if the request says "uploaded", "current", or "bound upload", run
`python -m yigdesk.cli state` before asking a question. Reuse an active uploaded
session when its state already contains the requested decision inputs; the immutable
session manifest is authoritative for the file path and values.

For a new bind, collect exactly these material inputs from the request or attached-file
context:

1. One local generated synthetic `.xlsx` path.
2. Submitted discount percentage.
3. Gross-margin floor percentage.
4. Current discount percentage, defaulting to `0` only when omitted.

If there is no reusable active session and the file, submitted discount, or margin
floor is missing, ask one concise combined question. Do not infer them. Resolve
attached files by inspecting the task's available file paths; do not ask the user to
move the file into the repository.

For a follow-up that refers to the current bound request and does not change the
workbook or decision inputs, reuse the current session without asking for its original
path. Any new file, discount, or floor requires a new bind and therefore a new scenario.

## 2. Bind an immutable session

Skip this section only when Section 1 found a matching active session. Otherwise, from
the repository root run:

```powershell
python -m yigdesk.cli bind --file <absolute-xlsx-path> --discount <submitted> --floor <floor> --current-discount <current>
```

Require JSON status `BOUND`, `analysis_bytes_unchanged: true`, a session id, source
fingerprint, projection fingerprint, and source-cell count. A rejected bind must not
replace the prior active scenario. Do not delete or edit anything under
`runtime/sessions/`. The session importer and T2's `source_for_active_session` adapter
are reused, but the board does not price this bound upload yet (see Section 3); binding
currently establishes session integrity, not the priced board scenario.

## 3. Point the blackboard at the demo scenario

The blackboard MCP server (`python -m yigdesk.blackboard_mcp`) reads two env vars:
`YIGDESK_SCENARIO`, the path to an immutable scenario directory holding `model.json`
and its workbook, and `YIGDESK_LEDGER`, the append-only board ledger (default
`runtime/board.jsonl`). For now the council prices the packaged demo scenario only: set
`YIGDESK_SCENARIO` to `data/scenarios/council_discount/`. The role agents inherit both
env vars from the project MCP config.

Pricing an uploaded binding on the live board is not yet wired, so it is not an option
here. The session importer and T2's `source_for_active_session` adapter still exist and
can build a `ModelSource` from a bound upload, but `yigdesk.blackboard_mcp` does not
consume that adapter yet - it only reads a static `YIGDESK_SCENARIO` directory. Do not
point `YIGDESK_SCENARIO` at anything but the demo scenario, and when a user uploads
their own workbook, say plainly that this council run prices the demo scenario until the
upload-to-board wiring lands (a known follow-up).

The bind/session/state steps above stay useful for session integrity only: run
`python -m yigdesk.cli state` to confirm the active session matches its bind receipt.
The optional read app (`python -m yigdesk.app`, `http://127.0.0.1:8787`) is only an
evidence view; do not make the user open it to obtain the answer.

## Council latency contract

For every specialist and optimizer `spawn_agent` call, set `fork_turns="none"`.
Do not copy the parent conversation into a specialist. Send one compact task containing
only the role identity, the decision id, the exact required op sequence and arguments,
the submitted discount, the margin floor, the scenario source fingerprint, and the
expected output fields. The spawned agent's first action must be its first required Yigdesk MCP call;
it must not browse, inspect the repository, run shell commands, write a plan, or emit a
preamble first. This keeps the real Codex Work path on standard tier with bounded
context; it does not enable Fast mode or launch a nested `codex exec`.

## 4. Open the decision and run the council over the six ops

The council speaks exactly six blackboard ops. Collectively the role agents use at most
three of them - read_board, propose_candidate, and post_claim - and no single role uses
all three (finance and sales price candidates; risk adds a claim; the optimizer only
reads and claims). open_decision, cast_approval, and request_resolve stay with the
orchestrator, so a role agent can never open, approve, or resolve - only the
deterministic gate closes a decision.

1. As the orchestrator, `open_decision` for the discount question, passing the decision
   id, the question, decision_type `council_discount`, and the scenario policy
   (required cfo approval, a required grounded `risk` claim, selector `max:headroom`).

2. Spawn `finance_analyst`, `sales_advocate`, and `risk_challenger` in parallel. Give
   each the decision id, the submitted discount, the margin floor, and its unique
   candidate/claim ids. Require these exact op sequences - no extra Yigdesk calls:
   - `finance_analyst`: `read_board`; `propose_candidate(submitted discount)`.
   - `sales_advocate`: `read_board`; `propose_candidate(submitted discount)`;
     `propose_candidate(one concrete alternative)`.
   - `risk_challenger`: `read_board`; `propose_candidate(boundary candidate)`;
     `post_claim(type="risk", grounded in "Deal Inputs!B4")`.
   The engine prices every candidate deterministically and fails a claim closed unless
   its refs ground to real cells, so no persona can invent a figure. Wait with
   `wait_agent` using a timeout_ms of at least 10000; shorter values are invalid and
   only add a failed orchestration round trip. Reject a role result that omits its
   priced consequence or its grounded claim.

3. Only after those three land on the board, spawn `decision_optimizer`. Require
   exactly: `read_board` (compare the priced candidates by exact headroom), then
   `post_claim` a non-binding advisory recommendation. The optimizer must not
   `propose_candidate` and must not `request_resolve` - it advises, it does not close.

4. As the orchestrator or human reviewer, `cast_approval` on the chosen candidate
   (verdict `approve`, scoped to the candidate id) to satisfy the policy's required cfo
   approval.

5. Call `request_resolve`. This runs the deterministic gate: it returns
   `pending(reason)` when a required approval or grounded risk claim is missing or no
   candidate passes the constraints, or it commits a `DecisionRecord` naming the chosen
   candidate, the closing selector, the evidence refs, and the source fingerprint.

If fewer than two priced candidates exist, stop with the pending reason instead of
fabricating one. The optimizer may name a highest financially feasible candidate, but
must not call it commercially optimal without supplied commercial evidence.

## 5. The ledger is the audit

There is no separate audit-verification step and no post-hoc verifier to run. The board
is an append-only ledger: every open_decision, propose_candidate, post_claim,
cast_approval, and resolve is recorded in order under `YIGDESK_LEDGER` (default
`runtime/board.jsonl`), so the ledger itself is the audit trail. Grounding is enforced
by the engine at write time - post_claim fails closed on any ungrounded ref, and the
gate refuses to resolve without the policy's required grounded risk claim and approval
- so there is nothing to re-verify after the fact. The committed DecisionRecord returned
by request_resolve is the outcome; a `pending(reason)` is a terminal HOLD for this
attempt.

## 6. Return the committed DecisionRecord

Lead with the answer and report the committed DecisionRecord:

- the chosen candidate and its discount, labeled `financially feasible` (selector
  `max:headroom`) rather than `commercially optimal` unless commercial evidence exists;
- the submitted discount's priced verdict and its exact headroom against the floor;
- the boundary candidate's remaining headroom and the grounded `risk` claim that gates
  the close;
- the closing selector, evidence refs, and source-fingerprint prefix from the record;
- `http://127.0.0.1:8787` only as an optional evidence-view link.

If `request_resolve` returned `pending`, report the reason and the missing approval or
grounded claim instead of a recommendation. Keep every figure grounded in the priced
consequences rather than recomputing them in prose.

## Natural-language triggers

- "用我上传的合成 Excel 判断 Northwind 的 2% 折扣是否合理，底线 30%。"
- "让 finance、sales、risk 相互 challenge，再告诉我安全的折扣是多少。"
- "跑一次真实 Yigdesk 决策黑板 demo，全程留在 Codex。"
- "基于当前绑定，把 2% 改成 2.2% 再重新评估。"
