# Codex A2A decision protocol

Yigdesk is the shared, deterministic blackboard between Codex subagents. It is
not an agent-to-agent chat bus. Agents may disagree in prose, but their numbers
must resolve through the same read-only tools and the same immutable revision.

## Tool surface

The MCP server exposes eight tools—small enough for reliable selection, broad
enough for a real challenge workflow:

| Tool | Decision job |
| --- | --- |
| `get_deal_context` | Establish source, request, evidence addresses, and revision identity. |
| `preview_consequence` | Return the authoritative consequence for the submitted request. |
| `inspect_evidence` | Verify one canonical workbook object and its lineage. |
| `evaluate_proposal` | Test one concrete discount using exact, unrounded constraint math. |
| `compare_proposals` | Score two to twelve proposals on one revision without inventing a commercial winner. |
| `find_feasible_boundary` | Find the exact margin boundary, largest safe increment, and first unsafe increment. |
| `stress_test_assumption` | Re-evaluate a proposal under an explicit, non-persistent COGS assumption. |
| `list_missing_evidence` | Explain which missing facts make HOLD terminal. |

The tradeoff is deliberate: Yigdesk has no proposal database, messaging,
approval, send, signing, or write-back tool. Subagents coordinate through Codex;
Yigdesk only supplies reproducible decision facts. This keeps the public demo
read-only and prevents a persuasive agent from overriding exact policy math.

## Project-scoped agents

The repository includes four current Codex custom agents under
`.codex/agents/`: `finance_analyst`, `sales_advocate`, `risk_challenger`, and
`decision_optimizer`. Each role explicitly pins `gpt-5.6-terra`, reasoning effort
`none`, a role-specific tool allowlist, and the read-only Yigdesk MCP configuration;
the real performance harness also pins the outer orchestrator and disables Fast.
Each role passes an allowlisted
`actor` declaration on every tool call. Because Codex subagents inherit the
session environment forwarded to their own MCP clients, this label is audit attribution rather than authentication;
the Codex subagent trace establishes which thread acted, while the independent
Yigdesk audit establishes the declared role, tool sequence, success, and
revision consistency.
`.codex/config.toml` also maps every custom-agent name to its TOML file
explicitly. This prevents a same-named generic task from silently replacing the
role's developer instructions in non-interactive Codex runs.
`python -m scripts.verify_a2a_audit` resolves the active immutable session and accepts only
the exact role sequences on one revision and rejects unknown actors, failed calls,
or revision drift. Earlier nonconforming attempts remain visible rather than being
deleted.

Use the project Skill in a local Codex task; it binds the file, makes the read surface
healthy, expands the exact role protocol, and verifies the audit:

```text
Use $yigdesk-council to analyze my attached generated Northwind FY2024 workbook.
The submitted discount is 2%, current discount is 0%, and gross-margin floor is 30%.
Run the real challenged council and return only an audit-verified decision.
```

If the generated workbook was already uploaded through the browser, use the same task
and say `Use $yigdesk-council on the current bound Yigdesk revision.` The browser bind,
Codex agents, verifier, and progress view all resolve that one immutable session. The
primary path does not launch a nested `codex exec` process.

For the supplied FY2024 synthetic workbook, the requested 2% discount is
financially feasible at the configured 30% floor. The exact maximum is
2.239020%; at 0.01-point increments, 2.23% is the largest safe value. A 2.24%
proposal displays 30.0% gross margin but fails the exact constraint. That
rounding disagreement is intentional adversarial evidence for the risk agent.
It does not prove that 2.23% is commercially optimal; market and customer
evidence would be required for that claim.

## Evaluation set

`evals.xml` contains ten multi-tool decision questions spanning happy path,
partial evidence, invalid-address recovery, cross-revision rejection, rounding,
stress testing, source immutability, scope safety, and A2A synthesis.
