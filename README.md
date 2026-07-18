# Yigdesk

**See the consequence before the agent acts.**

Yigdesk is a polished, read-only office-agent demo built with Codex for OpenAI Build Week. A synthetic discount request arrives by email; Yigdesk reads its generated workbook, previews ARR and margin consequences, and shows the five exact cells behind the result. If cost evidence is missing, it returns an honest <code>HOLD</code>.

![Yigdesk consequence preview](docs/images/yigdesk-ready.png)

## The 90-second demo

1. Open the complete-evidence scenario and select **Analyze with Codex**.
2. See <code>READY FOR CFO</code>, net ARR <code>$880k</code>, ARR impact <code>-$20k</code>, gross margin <code>45.5%</code>, and <code>5.5%</code> headroom.
3. Inspect the highlighted workbook objects and their precedents/dependents.
4. Switch to **Missing cost evidence** and rerun. Margin becomes <code>Unavailable</code> and the verdict becomes <code>HOLD</code>.
5. Notice the source fingerprint and <code>Workbook bytes unchanged</code> proof. There is no business write-back endpoint in this public build.

The bundled scenario is synthetic. The fixed adapter evaluates:

~~~text
net ARR       = 1000 * (1 - 12%) = 880
ARR impact    = 880 - 900        = -20
gross profit  = 880 - 480        = 400
gross margin  = 400 / 880        = 45.5%
headroom      = 45.5% - 40%      = 5.5 points
~~~

## Run locally

Requirements: Python 3.11+ and Node.js 20+. Local deterministic preview needs no
OpenAI credential. Real Codex mode requires an authenticated Codex CLI; use an
existing Codex login locally or a server-side <code>CODEX_API_KEY</code> in automation.

~~~bash
python -m pip install -r requirements.txt
npm ci
python -m yigdesk.app
~~~

Open <http://127.0.0.1:8787>. With Codex disabled, the button is explicitly
labeled **Preview consequence locally** and all data plus the five-formula
adapter remain local and synthetic.

To enable the real Codex + MCP path in PowerShell:

~~~powershell
$env:YIGDESK_CODEX_ENABLED = "1"
$env:YIGDESK_CODEX_MODEL = "gpt-5.6-sol"
python -m yigdesk.app
~~~

The button is labeled **Analyze with Codex** only in this mode. Codex must call
<code>get_deal_context</code>, <code>preview_consequence</code>, and
<code>inspect_evidence</code>. Yigdesk rejects the answer if the audited tool
trace, packet ID, verdict, evidence address, or any displayed figure drifts from
the deterministic packet. There is no silent fallback.

CLI:

~~~bash
python -m yigdesk.cli state
python -m yigdesk.cli analyze
python -m yigdesk.cli inspect "Deal Model!B4"
python -m yigdesk.cli reset --scenario hold
~~~

MCP server, for direct Inspector or client testing:

~~~bash
python -m yigdesk.mcp_server
~~~

## Private A/B comparison

Copy <code>benchmarks/case-template.json</code> into the ignored
<code>benchmarks/private/</code> directory and replace it with an independently
verified case plus gold result. Then run the same Codex model bare and with
Yigdesk assistance:

~~~bash
python -m scripts.compare_agents \
  --case benchmarks/private/case.json \
  --runs 3 \
  --acknowledge-data-sharing
~~~

Both modes send the supplied request and inputs to OpenAI, so the acknowledgement
is mandatory. Reports are written under ignored <code>benchmark-results/</code>
and contain only case ID, verdict/metrics, latency, token usage, proof hashes, and
tool names. They exclude the email body and raw inputs. Private case content is
streamed to bare Codex over stdin and is never placed in process arguments.

Accuracy uses decimal values plus field-specific units, so <code>$880k</code> and
<code>$880.0k</code>, or <code>5.5%</code> and <code>5.50 percentage points</code>,
are semantically equivalent. Exact display-format consistency is reported
separately. Bare Codex receives the same intermediate rounding contract as the
reference engine: money intermediates use <code>0.01k / ROUND_HALF_UP</code>, while
margin and headroom use <code>0.1 percentage point / ROUND_HALF_UP</code>. Semantic
scoring then normalizes high-precision answers to the final display resolution:
whole thousands with <code>ROUND_HALF_EVEN</code> and percentage points to one
decimal with <code>ROUND_HALF_UP</code>. The JSON report embeds this contract.

The report also shows the deterministic engine result versus the independent
gold, and records individual trial failures plus failure rate instead of
discarding an otherwise usable comparison. Use at least three paired runs for a
meaningful comparison; the harness alternates AB/BA order.

Tests:

~~~bash
python -m pytest
npx playwright install chromium
npm run test:e2e
~~~

## What is open here

This repository is a deliberately narrow adoption surface:

- a domain-neutral read-only <code>YigdeskSession</code> interface;
- <code>&lt;yig-grid&gt;</code> and <code>&lt;yig-model-inspector&gt;</code> web components;
- the Yigdesk experience and synthetic finance fixture;
- a scenario-specific five-formula adapter and consequence-packet demo;
- tests, container setup, and Build Week materials.

It does **not** contain the proprietary Yigrid kernel, general DAG semantics, operational history, governance/authorization, persistent write-back, vault, signing, transparency log, compliance, SSO, or multi-tenant services. The public packet is a demo protocol, not a Yigrid production attestation.

See [Open-core boundary](docs/OPEN_CORE_BOUNDARY.md), [Architecture](docs/ARCHITECTURE.md), and the [Evidence Ledger design system](docs/DESIGN_SYSTEM.md).

## How Codex shaped the build

Codex was used as the primary engineering agent to narrow the office workflow, implement the full-stack demo, challenge the read-only and refusal claims, add the live incomplete-evidence path, polish the product surface, and drive Python plus browser acceptance tests. The important product decision was to make evidence visible before any action rather than asking users to trust an agent's prose.

The project targets the **Work & Productivity** category. The runtime now contains
a real Codex tool-use path in addition to Codex being the primary engineering
agent. The official submission requires a working project, description,
sub-three-minute public demo video, testable repository, and the primary Codex
<code>/feedback</code> session ID.

## License and rights

Files in this repository are licensed under Apache-2.0. That license applies only to this repository and grants no right to Yigrid proprietary source code, services, data, or trademarks. “Yigrid” and “Yigdesk” are product identifiers; see [NOTICE](NOTICE).

All people, organizations, messages, values, and workbook contents are fictional and synthetic.
