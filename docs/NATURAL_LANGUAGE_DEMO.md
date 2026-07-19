# Natural-language Codex demo

The primary control surface is one Codex task opened at the Yigdesk repository.
The project Skill `$yigdesk-council` handles local intake, the Yigdesk read surface,
specialist-agent coordination, and audit verification. The browser at
<http://127.0.0.1:8787> is an optional evidence view, not a second control panel.

## One-request start

Attach or reference the generated synthetic workbook and say:

```text
使用 $yigdesk-council 分析我附上的 Northwind FY2024 合成 Excel。
当前折扣 0%，客户申请 2%，毛利率底线 30%。
让 finance、sales、risk 真实调用 Yigdesk 相互 challenge，最后给我审计通过的建议。
全程由你调度；只有缺少实质输入时才问我。
```

Codex then performs the real workflow without asking the user to switch windows:

1. The strict parser validates the actual XLSX bytes and extracts the FY2024 cells.
2. Yigdesk copies those bytes into a new ignored, immutable session and binds the
   source fingerprint plus decision inputs to one revision.
3. Codex makes the local read surface healthy and confirms state identity.
4. Finance, sales, and risk agents run their different, exact MCP sequences in
   parallel against that revision.
5. The optimizer compares their unique proposals only after the revision gate passes.
6. The independent audit verifier requires 15 accepted, actor-attributed calls before
   Codex releases the decision brief.

## Browser-uploaded start

For a video that visibly demonstrates real upload parsing, start the app, upload the
generated workbook at <http://127.0.0.1:8787>, and set the request inputs there. The
upload creates the active immutable session; it is not a browser-only copy. Then say
in the repository's existing Codex task:

```text
Use $yigdesk-council on the current bound Yigdesk revision. Keep orchestration in
this Codex Work task and return only an audit-verified recommendation.
```

The Skill reads the active identity before asking for inputs and reuses it when the
workbook and decision values are unchanged. While the council runs, the browser polls
only safe role/call-count status from the same session audit. Its finance, sales, risk,
and optimizer progress is therefore real MCP activity, not animated agent dialogue.
The optional nested single-agent browser harness is not part of this path.

The brief separates a financially feasible ceiling from a commercially supported
recommendation. With no market evidence, Yigdesk must not label the highest feasible
discount as commercially optimal.

The repository's real performance E2E keeps the same data flow and a hard 120-second
process budget. It uses `gpt-5.6-terra` on standard service tier with Fast explicitly
disabled, pins the four project agents and outer orchestrator to
`model_reasoning_effort="none"`, and
requires both the 15-call audit and an identity-bound candidate JSON. The harness—not
the outer model turn—performs the final audit check after Codex exits, so verification
does not consume another model round trip.

## Natural follow-ups

No new workbook or input:

```text
解释为什么 2.24% 看起来是 30.0% 却没有通过，不要重新跑 council。
```

Changed material input (creates a new session and clean audit):

```text
基于同一个源文件，把申请折扣改成 2.2%，底线仍为 30%，重新跑完整 council。
```

Optional visual evidence:

```text
现在打开证据面板，让我看当前 session 的源单元格、fingerprint 和 B4 lineage。
```

## What should be visible in a demo recording

- The source filename and the parser's real source-cell count, not a prerecorded value.
- A new session id, source fingerprint, projection fingerprint, and revision id.
- Three specialist agents with distinct tool sequences, followed by the optimizer.
- `verified: true` and 15 accepted calls from the session-specific audit.
- Exact boundary, largest safe 0.01-point value, first unsafe value, and `+5% COGS`
  stress result from Yigdesk responses.
- A terminal `HOLD` or `AGENT REJECTED` if evidence, identity, or audit is incomplete.

## Operator diagnostics

These commands are for debugging; they are not normal user steps:

```powershell
python -m yigdesk.cli bind --file <synthetic.xlsx> --discount 2 --floor 30
python -m yigdesk.app
python -m yigdesk.cli state
python -m yigdesk.cli analyze
python -m scripts.verify_a2a_audit
python -m scripts.run_real_council_e2e --runtime runtime --timeout-seconds 120 --acknowledge-data-sharing
```

Every session lives under ignored `runtime/sessions/`. Never edit or delete a session
during a run; bind a new one when the file or decision inputs change.
