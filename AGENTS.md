# Yigdesk agent contract

This public repository is a read-only consequence-preview demo.

1. Start with <code>python -m yigdesk.app</code>.
2. Read state with <code>python -m yigdesk.cli state</code>.
3. Analyze with <code>python -m yigdesk.cli analyze</code>.
4. Inspect evidence with <code>python -m yigdesk.cli inspect "Deal Model!B4"</code>.
5. Treat <code>HOLD</code> as terminal when evidence is incomplete.
6. Never add source mutation, write-back, authorization, signing, vault, audit-service, or production connector capabilities here.
7. Keep <code>session.js</code>, <code>yig-grid.js</code>, and <code>yig-model-inspector.js</code> domain-neutral.
8. Use only generated synthetic data.
9. Run <code>python -m pytest</code> and <code>npm run test:e2e</code> before completion.

The demo packet may be copied for inspection; it is not a production Yigrid attestation.

When the user explicitly asks for the Northwind decision council, spawn the custom
agent types <code>finance_analyst</code>, <code>sales_advocate</code>, and
<code>risk_challenger</code> in parallel. Require these exact actor-attributed
Yigdesk sequences: finance = context, boundary, submitted evaluation, B4
inspection; sales = context, submitted evaluation, one alternative evaluation;
risk = context, missing evidence, boundary, submitted +5% COGS stress, B4
inspection. Do not replace them with a common sequence. Wait for all three,
require matching Yigdesk revision identifiers, then spawn
<code>decision_optimizer</code> for exactly context, unique-proposal comparison,
and B4 inspection. The council must not simulate agent dialogue or invent market data.
