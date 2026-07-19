# Architecture

Yigdesk is intentionally small enough for a judge to audit. The public runtime has four boundaries.

## 1. Synthetic evidence intake

<code>data/scenarios.json</code> provides two bundled safety fixtures. The browser
may also upload a macro-free XLSX that is explicitly marked synthetic. The strict
parser reads FY2024 quarterly cells from <code>P&amp;L Report</code>, records every
source address and the source SHA-256, and builds a five-formula consequence
projection. It does not contain or call the private Yigrid kernel.

## 2. Read-only server

The Flask API exposes state, object inspection, scenario reset, health, and consequence preview. Reset only regenerates a bundled synthetic fixture. Analysis hashes the XLSX before and after evaluation and reports whether the bytes match.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | <code>/api/health</code> | Public-preview health |
| GET | <code>/api/state</code> | Synthetic email and workbook view |
| GET | <code>/api/inspect?address=...</code> | Formula and lineage for one object |
| POST | <code>/api/reset</code> | Choose a bundled synthetic scenario |
| POST | <code>/api/upload</code> | Validate and parse one generated synthetic XLSX into an ephemeral revision |
| POST | <code>/api/analyze</code> | Return a read-only demo consequence packet |
| POST | <code>/api/proposals/evaluate</code> | Evaluate one proposal with exact policy math |
| POST | <code>/api/proposals/compare</code> | Compare grounded proposals on one revision |
| GET | <code>/api/proposals/boundary</code> | Find exact and step-aligned safe boundaries |
| POST | <code>/api/proposals/stress-test</code> | Apply one explicit non-persistent COGS assumption |
| GET | <code>/api/evidence/missing</code> | Explain terminal evidence gaps |
| POST | <code>/api/agent-runs</code> | Start a real Codex + MCP run when explicitly configured |
| GET | <code>/api/agent-runs/&lt;run_id&gt;</code> | Read one ephemeral verified run trace |

There is no action, write-back, authorization, signing, vault, persistent audit-service, or messaging endpoint.

## 3. Codex orchestration boundary

When enabled, <code>CodexRunner</code> starts <code>codex exec</code> in an isolated
temporary directory with a read-only sandbox and fixed output schema. A stdio MCP
server exposes eight read-only tools. The verified single-agent proof path is
restricted to an exact three-call subset; project-scoped Codex subagents use the
additional proposal, comparison, boundary, stress, and missing-evidence tools.
The MCP process records a minimized
tool trace independently of model output; Yigdesk then verifies packet ID, verdict,
figures, inspected evidence, and source immutability before releasing the answer.
When disabled, the UI says local preview and never claims a Codex runtime call.

## 4. Domain-neutral public seam

<code>YigdeskSession</code> exposes only <code>getView</code>, <code>inspectRef</code>, and <code>previewConsequence</code>. <code>&lt;yig-grid&gt;</code> and <code>&lt;yig-model-inspector&gt;</code> consume generic object shapes. A boundary test rejects finance vocabulary in those three files.

## 5. Scenario UI

The finance story belongs in <code>index.html</code>, <code>app.js</code>, fixtures, and the fixed adapter. The browser renders packet values and never recomputes the deal math.

~~~mermaid
flowchart LR
    E["Uploaded or generated synthetic XLSX"] --> X["Strict P&L parser + source fingerprint"]
    X --> C["Codex in read-only sandbox"]
    C --> M["Eight Yigdesk MCP tools"]
    M --> A["Five-formula demo adapter"]
    A --> P["Demo consequence packet"]
    P --> V["Packet and trace verifier"]
    V --> G["Read-only grid"]
    V --> I["Model inspector"]
    V --> H["READY FOR CFO or HOLD"]
    M --> B["A2A proposal board"]
~~~

Private Yigrid engine, graph, operational-history, governance, mutation, and trust-service implementations are outside this repository.
