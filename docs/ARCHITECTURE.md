# Architecture

Yigdesk is intentionally small enough for a judge to audit. The public runtime has four boundaries.

## 1. Synthetic story adapter

<code>data/scenarios.json</code> is the only bundled business context. Startup generates <code>runtime/yigdesk-demo.xlsx</code>. <code>yigdesk/engine.py</code> contains five scenario-specific formulas; it is explicitly not the Yigrid kernel.

## 2. Read-only server

The Flask API exposes state, object inspection, scenario reset, health, and consequence preview. Reset only regenerates a bundled synthetic fixture. Analysis hashes the XLSX before and after evaluation and reports whether the bytes match.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | <code>/api/health</code> | Public-preview health |
| GET | <code>/api/state</code> | Synthetic email and workbook view |
| GET | <code>/api/inspect?address=...</code> | Formula and lineage for one object |
| POST | <code>/api/reset</code> | Choose a bundled synthetic scenario |
| POST | <code>/api/analyze</code> | Return a read-only demo consequence packet |
| POST | <code>/api/agent-runs</code> | Start a real Codex + MCP run when explicitly configured |
| GET | <code>/api/agent-runs/&lt;run_id&gt;</code> | Read one ephemeral verified run trace |

There is no action, write-back, authorization, signing, vault, persistent audit-service, or messaging endpoint.

## 3. Codex orchestration boundary

When enabled, <code>CodexRunner</code> starts <code>codex exec</code> in an isolated
temporary directory with a read-only sandbox and fixed output schema. A stdio MCP
server exposes exactly three read-only tools. The MCP process records a minimized
tool trace independently of model output; Yigdesk then verifies packet ID, verdict,
figures, inspected evidence, and source immutability before releasing the answer.
When disabled, the UI says local preview and never claims a Codex runtime call.

## 4. Domain-neutral public seam

<code>YigdeskSession</code> exposes only <code>getView</code>, <code>inspectRef</code>, and <code>previewConsequence</code>. <code>&lt;yig-grid&gt;</code> and <code>&lt;yig-model-inspector&gt;</code> consume generic object shapes. A boundary test rejects finance vocabulary in those three files.

## 5. Scenario UI

The finance story belongs in <code>index.html</code>, <code>app.js</code>, fixtures, and the fixed adapter. The browser renders packet values and never recomputes the deal math.

~~~mermaid
flowchart LR
    E["Synthetic email + XLSX"] --> C["Codex in read-only sandbox"]
    C --> M["Three Yigdesk MCP tools"]
    M --> A["Five-formula demo adapter"]
    A --> P["Demo consequence packet"]
    P --> V["Packet and trace verifier"]
    V --> G["Read-only grid"]
    V --> I["Model inspector"]
    V --> H["READY FOR CFO or HOLD"]
~~~

Private Yigrid engine, graph, operational-history, governance, mutation, and trust-service implementations are outside this repository.
