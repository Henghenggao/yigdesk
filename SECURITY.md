# Security and privacy

## Data boundary

Every name, organization, email, value, and workbook in this repository is synthetic. Do not add customer files, real inbox content, secrets, API tokens, or production connectors.

## Runtime boundary

The app binds to <code>127.0.0.1</code> by default. The browser board and gate have
no authentication: roles are local attribution, not verified identity. Running with
<code>HOST=0.0.0.0</code> is therefore suitable only for an isolated synthetic demo,
not a production or multi-user deployment.

The MCP server uses local stdio and should not be exposed directly to a network. MCP
and Web share the ledger selected by <code>YIGDESK_LEDGER</code>; an adjacent
cross-process lock serializes writes and resolution. A committed decision is terminal:
later mutation attempts are rejected, and replay keeps the first committed
<code>DecisionRecord</code> authoritative.

Runtime XLSX files and ledgers live under ignored <code>runtime/</code> storage. The
evaluator reads source workbooks and writes only to its own append-only ledger. Invalid
or missing evidence references are recorded as rejected claims rather than escaping the
MCP call.

The optional A/B benchmark requires explicit acknowledgement before case content is
sent to Codex. It uses stdin, a minimized process environment, an isolated workspace,
disabled network access, a fixed output schema, and trace validation requiring the
blackboard's <code>propose_candidate</code> activity. Generated benchmark reports omit
raw case content.

This demo has no analyzed-model update, send, approval execution, source write-back,
signing, vault, persistent audit service, or identity-provider endpoint. The board
ledger and committed <code>DecisionRecord</code> are the audit evidence.

The upload endpoint accepts only macro-free <code>.xlsx</code> files up to 8 MB.
It requires a generated <code>SYNTHETIC</code> marker, rejects unexpected P&amp;L
schemas, bounds archive members, expanded archive bytes, sheets, rows, and
columns, ignores external workbook links, and never evaluates workbook macros.
Uploaded bytes live only in the configured ignored runtime directory. Analysis
hashes the source before and after parsing; the derived five-formula projection
has its own fingerprint. This is demo provenance, not malware scanning or a
production file-ingestion service.

Candidate pricing and gate resolution are deterministic. Exact unrounded decimals
decide configured constraints; rounded display values cannot turn a failing candidate
into a pass. The browser never prices candidates and can invoke only
<code>cast_approval</code> and <code>request_resolve</code> on the shared board.

## Reporting

Do not open public issues containing sensitive data. Report suspected vulnerabilities privately to the repository owner with reproduction steps and impact.
