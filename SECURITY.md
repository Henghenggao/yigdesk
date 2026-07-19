# Security and privacy

## Data boundary

Every name, organization, email, value, and workbook in this repository is synthetic. Do not add customer files, real inbox content, secrets, API tokens, or production connectors.

## Runtime boundary

The app binds to <code>127.0.0.1</code> by default. Runtime XLSX files live under ignored <code>runtime/</code> storage. Analysis fingerprints workbook bytes before and after and must leave them identical. Real Codex mode is off by default and must be explicitly enabled; credentials stay in the server environment and are never returned by an API.

The optional private A/B harness sends acknowledged private case content through
Codex stdin, never command-line arguments, and keeps the process environment on
the same explicit credential/runtime allowlist. Codex-launched subprocesses inherit
only an explicit runtime allowlist. Their home, profile, application-data, and temp
paths are redirected into the per-run workspace; `CODEX_HOME`, API keys, tokens,
secrets, and proxy credentials are not inherited. Generated reports omit raw
request and input content as well as exception messages.

The Codex runner uses an isolated temporary workspace and a custom least-privilege
permission profile. Model-launched commands can read only Codex's minimal runtime
paths and that one workspace; the profile deliberately grants neither filesystem-root
nor user-home access and enables no network. Parent-process authentication remains
available to Codex through its minimized environment, while `--ignore-user-config`,
`--ignore-rules`, disabled login shells, disabled web search, and strict config prevent
user extensions from widening the model-visible surface. The default shell tool and its
environment snapshot are disabled outright; the permission profile remains an OS-level
defense if a future tool surface changes. Codex CLI 0.138 or newer is
required for permission-profile enforcement; unsupported or misspelled config fails
closed.

The runner also uses a fixed JSON Schema, exactly three annotated read-only MCP tools,
and a separate minimized tool audit. It never trusts a model's self-reported tool list.
Verification requires item-level Codex JSONL trace events and rejects shell, web,
file-change, other-server, reordered, missing, or extra tool activity; the only accepted
active trace is the exact three-call Yigdesk MCP sequence. A missing trace, tool call,
source drift, packet mismatch, evidence mismatch, or figure mismatch rejects the run.
Real Codex mode is fail-closed unless the Flask host is loopback (`127.0.0.1`, `localhost`, or `::1`); public deployments remain local-preview only and must never expose Codex app-server or local MCP transports directly to the internet.
Starting a credential-backed run additionally requires a per-process same-origin token,
a loopback Host header, and a loopback peer address. A cross-origin form POST or a
non-loopback WSGI request cannot spend the operator's Codex quota.

This demo has no analyzed-model update, send, approval-execution, write-back, signing, vault, persistent audit-service, or identity-provider endpoint. Agent traces are ephemeral demo evidence. Scenario reset only regenerates a bundled synthetic fixture. <code>HOST=0.0.0.0</code> is intended only for an isolated synthetic demo deployment.

The upload endpoint accepts only macro-free <code>.xlsx</code> files up to 8 MB.
It requires a generated <code>SYNTHETIC</code> marker, rejects unexpected P&amp;L
schemas, bounds archive members, expanded archive bytes, sheets, rows, and
columns, ignores external workbook links, and never evaluates workbook macros.
Uploaded bytes live only in the configured ignored runtime directory. Analysis
hashes the source before and after parsing; the derived five-formula projection
has its own fingerprint. This is demo provenance, not malware scanning or a
production file-ingestion service.

Proposal evaluation, comparison, boundary, missing-evidence, and stress-test
routes are deterministic read operations. Stress assumptions are explicitly
non-persistent. Exact unrounded decimals decide the configured margin constraint;
rounded display values cannot turn a failing proposal into a pass.

## Reporting

Do not open public issues containing sensitive data. Report suspected vulnerabilities privately to the repository owner with reproduction steps and impact.
