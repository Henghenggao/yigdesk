# Yigdesk agent contract

This public repository is a deterministic decision blackboard demo. The source model
is read-only: the engine prices candidates and records decisions to its own
append-only ledger, but never writes back to the workbook.

1. The blackboard MCP server is <code>python -m yigdesk.blackboard_mcp</code>; it reads
   <code>YIGDESK_SCENARIO</code> (a scenario directory with <code>model.json</code>,
   default <code>data/scenarios/council_discount/</code>) and
   <code>YIGDESK_LEDGER</code> (the append-only board ledger, default
   <code>runtime/board.jsonl</code>).
2. The six blackboard ops are the whole surface: <code>read_board</code>,
   <code>propose_candidate</code>, <code>post_claim</code>, <code>open_decision</code>,
   <code>cast_approval</code>, <code>request_resolve</code>.
3. The engine prices every candidate deterministically and fails a
   <code>post_claim</code> closed unless every ref grounds to a real cell; never invent
   figures.
4. Only <code>request_resolve</code> closes a decision, through the deterministic gate,
   which returns <code>pending(reason)</code> or commits a <code>DecisionRecord</code>.
   Treat <code>pending</code> and <code>hold</code> as terminal.
5. Never add source mutation, write-back, authorization, signing, vault, audit-service, or production connector capabilities here.
6. Keep <code>session.js</code>, <code>yig-grid.js</code>, and <code>yig-model-inspector.js</code> domain-neutral.
7. Use only generated synthetic data.
8. Run <code>python -m pytest</code> and <code>npm run test:e2e</code> before completion.

The committed DecisionRecord and the append-only ledger are the audit; there is no
separate audit-service attestation here.

When the user explicitly asks for the Northwind decision council, open a decision as
the orchestrator, then spawn the custom agent types <code>finance_analyst</code>,
<code>sales_advocate</code>, and <code>risk_challenger</code> in parallel over the
blackboard ops: finance proposes the submitted discount as a candidate; sales proposes
the submitted discount and one concrete alternative; risk proposes a boundary candidate
and posts a grounded <code>risk</code> claim on the COGS cell
<code>Deal Inputs!B4</code>. The role agents never open, approve, or resolve. Wait for
all three, require their candidates and the grounded claim on the board, then spawn
<code>decision_optimizer</code> for exactly <code>read_board</code> plus a non-binding
advisory <code>post_claim</code>; it must not propose or resolve. Finally the
orchestrator (or human) casts approval and calls <code>request_resolve</code>; the
deterministic gate returns <code>pending(reason)</code> or commits the
<code>DecisionRecord</code>. The council must not simulate agent dialogue or invent
market data.
