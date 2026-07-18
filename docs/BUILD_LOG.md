# Build log

## Starting point

The project built on prior conceptual work about deterministic consequences and human control. The Build Week implementation, Yigdesk brand, synthetic fixture, public API, UI, tests, and submission materials were assembled and validated with Codex.

## Decisions made with Codex

1. Chose a concrete office workflow: an emailed discount request backed by workbook formulas.
2. Replaced an abstract technical demo with a coherent three-panel product experience.
3. Made the compute wall explicit: the browser renders packet values and does not calculate finance metrics.
4. Added byte-level before/after fingerprints to prove read-only analysis.
5. Added a separate incomplete-evidence fixture so <code>HOLD</code> is demonstrated live.
6. Corrected the evidence binding for requested discount to <code>Deal Inputs!B6</code>.
7. Removed mutation, write-back, approval execution, signing, vault, and audit-service capabilities from the public submission.
8. Added automated boundary tests for domain neutrality and private-kernel signals.

## Public/private split

The repository is an independent synthetic implementation, not a source export from Yigrid. It shares the product thesis and presents a narrow public interface; private Yigrid kernel, graph, operational-history, governance, and trust-service code are absent.

## Verification

Python tests cover exact math, READY/HOLD behavior, byte identity, inspection lineage, absent commercial routes, and repository boundaries. Playwright drives both live scenarios. CI also builds the container.
