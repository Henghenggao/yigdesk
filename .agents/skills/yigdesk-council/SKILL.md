---
name: yigdesk-council
description: Run the packaged synthetic Northwind council with three parallel specialists, a grounded optimizer advisory, a live human gate, and deterministic ledger-backed continuation.
---

# Yigdesk Council

Read `plugins/yigdesk/skills/yigdesk-council/SKILL.md` completely and follow it as
the authoritative workflow. This repository wrapper intentionally contains no
second orchestration definition: the plugin skill is the single source of truth.

The mandatory contract is:

- use only `data/scenarios/council_discount/` and never mutate its source;
- keep the public MCP surface at exactly six blackboard operations;
- open the decision as orchestrator;
- run Finance, Sales, and Risk in parallel with `fork_turns="none"`;
- verify all specialist candidates plus the grounded risk claim on the board;
- then run `decision_optimizer` for exactly `read_board` and one grounded,
  non-binding advisory `post_claim`;
- stop at the human boundary; only `request_resolve` may invoke the deterministic
  gate and append a `DecisionRecord`;
- treat Hold as terminal; after Request revision, wait for a new priced proposal
  before accepting another human action;
- never invent market evidence, simulate agent dialogue, add a hidden callback,
  or write back to the workbook.
