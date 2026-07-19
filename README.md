# Yigdesk

**A deterministic decision blackboard for AI subagents and humans.**

Yigdesk is a standalone service where autonomous agents and human reviewers
decide *together* on a shared, append-only board. Agents propose candidate
actions and post grounded claims; a pluggable evaluator prices every candidate
from real source data; humans (or a policy) approve; and a deterministic gate —
never a language model — closes the decision and commits an auditable record.

The whole system is three small parts:

- an **append-only ledger** of operations (the board is a pure fold of this log);
- a **pluggable, model-from-data evaluator** that prices actions and grounds claims;
- an **MCP-native surface** of six operations that agents and orchestrators call.

Nothing writes back to the source model. The ledger *is* the audit trail, and the
committed `DecisionRecord` is the outcome.

## The six board operations

The entire agent-facing surface is six operations (`yigdesk/blackboard_mcp.py`),
each recorded as one immutable entry on the ledger:

| Op | Who calls it | Effect |
| --- | --- | --- |
| `open_decision` | orchestrator / owner | Opens a decision with a question, type, and policy. |
| `propose_candidate` | proposer agent | Submits input overrides; the engine **prices** the candidate deterministically. |
| `post_claim` | critic agent | Posts a typed claim; **rejected fail-closed** unless every ref grounds to real evidence. |
| `cast_approval` | reviewer / human | Records an `approve` / `hold` / `reject` verdict scoped to a candidate or the decision. |
| `request_resolve` | resolver | Runs the deterministic gate → `pending(reason)` or a committed `DecisionRecord`. |
| `read_board` | anyone | Returns the deterministic board projection. |

Roles are separated on purpose: proposer/critic agents only propose and claim;
opening, approving, and resolving stay with the orchestrator or human. A role
agent can never close a decision — only the gate can.

## Determinism guarantees

Yigdesk is built so that the same inputs always produce the same board and the
same outcome. Four guarantees are enforced by code and pinned by
`tests/test_determinism_conformance.py`:

- **D1 — deterministic pricing.** A candidate's consequence is a pure function of
  its action and the source. `ExpressionEvaluator` evaluates a data-defined model
  (metrics, formulas, constraints) over exact `Decimal` arithmetic with fixed
  rounding — no floats, no ambient state. The same action always prices identically.
- **D2 — state is a pure fold.** The board is `fold(ledger)` over the append-only
  op log (`yigdesk/core/projection.py`). Replaying the same ledger yields a
  byte-identical projection every time.
- **D3 — grounding fails closed.** `post_claim` is accepted only when *every*
  reference resolves to a real evidence cell; an ungrounded claim is recorded as
  `rejected` and never enters the projection. The gate refuses to resolve a
  decision whose policy requires a grounded claim that is absent.
- **D4 — deterministic resolution.** `request_resolve` (`yigdesk/core/gate.py`) is
  a pure function of the candidates, approvals, policy, and evaluator revision. It
  returns `pending(reason)` or commits a `DecisionRecord`; no language model
  decides the close.

## Scenarios and the council app

A **scenario** is a domain app: a directory with a `model.json` (the evaluator
model: input cell refs, metrics, formulas, constraints), a `policy.json` (required
approvals, required claims, candidate selector), and a synthetic workbook holding
the source cells. Three ship in `data/scenarios/`:

- **`discount_approval`** — should a discount be approved under a gross-margin
  floor? Requires a CFO approval; selects the surviving candidate with the most
  headroom (`max:headroom`).
- **`saas_margin`** — does a plan hold a target margin after expansion? Requires a
  VP-Finance approval; selects on `max:headroom`.
- **`council_discount`** — the flagship multi-agent app. Same discount question,
  but the policy additionally requires a **grounded `risk` claim** before the gate
  will resolve. Four Codex personas (`.codex/agents/`) work the board over the six
  ops, coordinated by the `yigdesk-council` skill (`.agents/skills/`):
  - `finance_analyst` — prices the submitted discount as a candidate;
  - `sales_advocate` — prices the submitted discount and one customer-friendly alternative;
  - `risk_challenger` — prices a boundary candidate and posts a grounded `risk` claim on the COGS cell;
  - `decision_optimizer` — reads the board and posts a *non-binding* advisory claim (it never resolves).

The personas only read, propose, and claim. The orchestrator (or human) casts the
approval and calls `request_resolve`; the deterministic gate closes the decision.

## Quickstart

Requirements: Python 3.11+ (and Node.js 20+ only for the Playwright end-to-end
tests). Everything is local and synthetic; no external credential is needed to run
the blackboard, the evaluator, or the web shell.

```bash
python -m pip install -r requirements.txt
python scripts/build_scenarios.py        # materialize each scenario's workbook
python -m pytest                          # run the full test suite
```

Run the **MCP server** so an agent client (or MCP Inspector) can call the six ops.
It reads `YIGDESK_SCENARIO` (the scenario directory to price against — required)
and `YIGDESK_LEDGER` (the append-only board ledger, default `runtime/board.jsonl`):

```bash
YIGDESK_SCENARIO=data/scenarios/discount_approval python -m yigdesk.blackboard_mcp
```

Bind a synthetic upload into an **immutable session** (the offline intake path;
generate a sample first with `python -m scripts.generate_sample_workbook`):

```bash
python -m yigdesk.cli bind --file runtime/northwind-fy2024-synthetic.xlsx --discount 2 --floor 30
```

Serve the **minimal web shell** — a read-only evidence view exposing only
health, synthetic-workbook upload, and static assets — on <http://127.0.0.1:8787>:

```bash
python -m yigdesk.app
```

## Benchmark: bare agent vs. blackboard

`compare_agents` runs the same model twice on one case — once bare, once grounded
through the blackboard's priced candidate — and reports accuracy, determinism,
latency, and an observability score. It prices the `benchmark` scenario
deterministically as the independent reference. Both arms send the case to the
model, so the acknowledgement flag is mandatory:

```bash
python -m scripts.compare_agents --case benchmarks/case-template.json --runs 4 --acknowledge-data-sharing
```

`benchmarks/case-template.json` is a runnable template; for a real comparison, copy
it into the gitignored `benchmarks/private/` and replace it with your own
independently verified case and gold result. Reports are written under the ignored
`benchmark-results/` directory and contain only case id, verdict/metrics, latency,
token usage, source fingerprint, and tool names — never the raw case content.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — the five units, the op/data model, and D1–D4.
- [Design system](docs/DESIGN_SYSTEM.md) — the Evidence Ledger visual language for the web shell.

## License and rights

Files in this repository are licensed under Apache-2.0 (see [LICENSE](LICENSE)).
"Yigdesk" is a product identifier; the Apache License grants no right to use the
name, logo, or marks to identify or promote derived products (see [NOTICE](NOTICE)).

All people, organizations, messages, values, and workbook contents in this
repository are fictional and synthetic.
