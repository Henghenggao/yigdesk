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

There is no action, write-back, authorization, signing, vault, audit-service, or messaging endpoint.

## 3. Domain-neutral public seam

<code>YigdeskSession</code> exposes only <code>getView</code>, <code>inspectRef</code>, and <code>previewConsequence</code>. <code>&lt;yig-grid&gt;</code> and <code>&lt;yig-model-inspector&gt;</code> consume generic object shapes. A boundary test rejects finance vocabulary in those three files.

## 4. Scenario UI

The finance story belongs in <code>index.html</code>, <code>app.js</code>, fixtures, and the fixed adapter. The browser renders packet values and never recomputes the deal math.

~~~mermaid
flowchart LR
    E["Synthetic email + XLSX"] --> A["Five-formula demo adapter"]
    A --> P["Demo consequence packet"]
    P --> G["Read-only grid"]
    P --> I["Model inspector"]
    P --> H["READY FOR CFO or HOLD"]
~~~

Private Yigrid engine, graph, operational-history, governance, mutation, and trust-service implementations are outside this repository.
