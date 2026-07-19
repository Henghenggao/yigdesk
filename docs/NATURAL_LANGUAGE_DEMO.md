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

The brief separates a financially feasible ceiling from a commercially supported
recommendation. With no market evidence, Yigdesk must not label the highest feasible
discount as commercially optimal.

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
```

Every session lives under ignored `runtime/sessions/`. Never edit or delete a session
during a run; bind a new one when the file or decision inputs change.
