---
name: yigdesk-council
description: Bind a generated synthetic Northwind FY2024 XLSX to an immutable local Yigdesk session, run the exact four-role Codex decision council, verify its actor-attributed audit, and return a revision-bound discount recommendation. Use when the user asks in natural language to upload or analyze a synthetic workbook, decide whether a discount such as 2% makes sense, find the financially safe percentage, challenge a proposal with subagents, or demo the Yigdesk A2A workflow without switching between Codex, a terminal, and the browser.
---

# Yigdesk Council

Turn one natural-language request into a real local data flow: validate and copy the
synthetic workbook, bind decision inputs to an immutable revision, coordinate the
project's specialist Codex agents, verify their exact Yigdesk tool trace, and report
only supported conclusions. The browser is an optional evidence view, not a required
control surface.

## Scope guard

- Accept only a generated synthetic `.xlsx` carrying the required `SYNTHETIC` marker.
- Keep the workflow read-only. Never approve, send, sign, message, mutate the source,
  write back to a workbook, or connect a production system.
- Do not copy a file outside the ignored Yigdesk runtime except when the user asks.
- Treat `HOLD`, a bind rejection, revision drift, missing evidence, an incomplete role,
  or a failed audit as terminal for that revision. Do not release an unverified result.
- Never invent market evidence. Financial feasibility is not commercial optimality.

## 1. Resolve the request

Collect exactly these material inputs from the request or attached-file context:

1. One local generated synthetic `.xlsx` path.
2. Submitted discount percentage.
3. Gross-margin floor percentage.
4. Current discount percentage, defaulting to `0` only when omitted.

If the file, submitted discount, or margin floor is missing, ask one concise combined
question. Do not infer them. Resolve attached files by inspecting the task's available
file paths; do not ask the user to move the file into the repository.

For a follow-up that explicitly refers to the current bound request and does not change
the workbook or decision inputs, reuse the current session. Any new file, discount, or
floor requires a new bind and therefore a new session and audit.

## 2. Bind an immutable session

From the repository root run:

```powershell
python -m yigdesk.cli bind --file <absolute-xlsx-path> --discount <submitted> --floor <floor> --current-discount <current>
```

Require JSON status `BOUND`, `analysis_bytes_unchanged: true`, a session id, revision
id, source fingerprint, projection fingerprint, and source-cell count. A rejected bind
must not replace the prior active session. Do not delete or edit anything under
`runtime/sessions/`.

## 3. Make the read surface available

Check `http://127.0.0.1:8787/api/health`. If it is unavailable, start
`python -m yigdesk.app` in a yielded or long-lived background terminal and wait for the
health endpoint. In a sandboxed Windows task, prefer a yielded long-running command;
`Start-Process` children may be reaped when their shell exits. Keep control in this
Codex task; do not tell the user to open another terminal or browser.

Run `python -m yigdesk.cli state`. Require its session id, revision id, and source
fingerprint to equal the bind receipt. Then run `python -m yigdesk.cli analyze`.
If its packet is `HOLD`, return the missing evidence and stop before spawning agents.

## 4. Run the real Codex council

For a Northwind decision-council request, spawn `finance_analyst`, `sales_advocate`,
and `risk_challenger` in parallel. Give each the submitted discount and the bind
receipt identity. Require actor attribution on every Yigdesk call and these exact,
role-specific sequences—no additional Yigdesk calls:

- `finance_analyst`: `get_deal_context`; `find_feasible_boundary(0.01)`;
  `evaluate_proposal(submitted)`; `inspect_evidence("Deal Model!B4")`.
- `sales_advocate`: `get_deal_context`; `evaluate_proposal(submitted)`;
  `evaluate_proposal(one concrete alternative)`.
- `risk_challenger`: `get_deal_context`; `list_missing_evidence`;
  `find_feasible_boundary(0.01)`;
  `stress_test_assumption(submitted, 5)`;
  `inspect_evidence("Deal Model!B4")`.

Wait for all three. Require every output to carry the same revision id, source
fingerprint, and packet id. Reject a role result that omits its proposal, evidence, or
identity.

Only after all three pass the revision gate, spawn `decision_optimizer`. Give it the
three grounded outputs and require exactly:

1. `get_deal_context` with actor `decision_optimizer`.
2. `compare_proposals` with the unique concrete proposals from the roles.
3. `inspect_evidence("Deal Model!B4")` with actor `decision_optimizer`.

If fewer than two unique proposals exist, stop with `HOLD` instead of fabricating one.
The optimizer may identify a highest financially feasible candidate, but must not call
it commercially optimal without supplied commercial evidence.

## 5. Verify before reporting

Run:

```powershell
python -m scripts.verify_a2a_audit
```

This resolves the active session's own audit file. Require `verified: true`, exactly
15 accepted calls, all four expected roles, and the same revision identity. If the
command fails or any condition differs, report `AGENT REJECTED` or `HOLD` with the
specific reason and do not expose a recommendation.

## 6. Return one decision brief

Lead with the answer and include:

- submitted percentage: pass/fail under exact math;
- recommended percentage, labeled either `financially feasible` or
  `commercially supported` as the evidence permits;
- exact safe boundary, largest safe 0.01-point proposal, first unsafe proposal;
- base and `+5% COGS` stressed margin outcome;
- revision id, source fingerprint prefix, inspected address, and audit result;
- `http://127.0.0.1:8787` only as an optional evidence-view link.

Do not make the user visit the browser to obtain the answer. Keep all calculations and
claims grounded in Yigdesk tool responses rather than recomputing them in prose.

## Natural-language triggers

- “用我上传的合成 Excel 判断 Northwind 的 2% 折扣是否合理，底线 30%。”
- “让 finance、sales、risk 相互 challenge，再告诉我安全的折扣是多少。”
- “跑一次真实 Yigdesk A2A demo，全程留在 Codex。”
- “基于当前绑定版本，把 2% 改成 2.2% 再重新评估。”
