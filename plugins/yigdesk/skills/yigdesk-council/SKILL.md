---
name: yigdesk-council
description: Run the fast local Northwind decision council over the packaged synthetic scenario, with a live visual human gate and deterministic ledger-backed continuation.
---

# Yigdesk Council

Use only the packaged generated-synthetic `data/scenarios/council_discount/`
scenario. Never bind an upload: the live board does not consume bound sessions.
Never modify repository files during a council run. Never write back to the source
workbook, add production connectors, or invent financial or market evidence.

## Inputs

Require a unique decision id, submitted discount, current discount (default 0),
gross-margin floor, one distinct sales alternative, and one risk boundary.

## Start the live board

Start the unseeded frontend before opening the decision:

```powershell
python -m scripts.council_frontend --decision-id <decision-id> `
  --ready-file runtime/council-frontends/<decision-id>.json
```

Read the ready file within 10 seconds. Require `/api/health` and `/api/board` to
have produced `health_http_status=200` and `board_http_status=200`, plus a
non-empty `instance_id`; otherwise stop immediately
with `pending("frontend readiness deadline missed")`. Use the exact URL from that
file because the launcher may fall back from port 8787. The launcher never seeds
the board and expires after 15 minutes.

## Run the council

For every spawned agent use `fork_turns="none"`. Do not copy the parent conversation into a specialist.
Its first action must be its first required Yigdesk MCP call: no preamble, plan,
repository inspection, shell call, or browse step may precede it. Use `wait_agent`
with a timeout_ms of at least 10000.

1. Record the ledger's current last sequence and call `open_decision` as orchestrator
   with decision type `council_discount`, CFO approval, required grounded `risk`
   claim, and selector `max:headroom`.
2. Spawn `finance_analyst`, `sales_advocate`, and `risk_challenger` in parallel,
   each with `fork_turns="none"`.
3. Finance calls `read_board`, then `propose_candidate` only for
   `submitted_request` at the submitted percentage. Sales calls `read_board`,
   `propose_candidate` for `sales_submitted_assessment` at the submitted
   percentage, then `propose_candidate` for `sales_alternative`. Risk calls
   `read_board`, `propose_candidate` for `risk_boundary`, then `post_claim` once
   with type `risk` grounded on `Deal Inputs!B4`. Specialist agents never open,
   approve, or resolve.
4. Use one 10-second wait window. Then call `read_board` once and require the
   four exact candidate ids, one grounded risk claim, and matching source
   fingerprints. If anything is missing, stop with
   `pending("specialist deadline exceeded")`.
5. Only after the specialist writes are verified, spawn `decision_optimizer`
   with `fork_turns="none"`. It must make exactly two Yigdesk calls: `read_board`,
   then `post_claim(type="advisory")` grounded in a real model ref. It mirrors the
   gate selector and tie-break but cannot propose, approve, or resolve. Wait once,
   then require its grounded advisory claim on the board; otherwise stop with
   `pending("optimizer deadline exceeded")`.
6. Stop at the human approval boundary and return the verified live URL and
   elapsed time. Do not simulate approval. The trusted human surface records its
   intent through the existing `cast_approval` operation.

## Continue after the browser action

When the same Codex task is intentionally kept active for button continuation,
run the ledger watcher using the sequence captured before the human action:

```powershell
python -m yigdesk.continuation --scenario data/scenarios/council_discount `
  --ledger <ledger> --decision-id <decision-id> --after-seq <seq> --timeout <seconds>
```

An approval invokes the existing deterministic `request_resolve`; Hold is terminal.
Request revision returns a structured revision outcome, keeps input open, and permits
another human action only after a new priced candidate lands. A missed deadline
returns an explicit pending reason. This watcher is orchestration around the existing
six MCP operations, not a seventh operation or an undocumented Codex callback.

## Reporting

Call the selected result financially feasible, not commercially optimal, unless
commercial evidence was supplied. The append-only ledger and committed
`DecisionRecord` are the audit. After resolution, report the committed DecisionRecord.
Treat `pending` and `hold` as terminal.
