# Security and privacy

## Data boundary

Every name, organization, email, value, and workbook in this repository is synthetic. Do not add customer files, real inbox content, secrets, API tokens, or production connectors.

## Runtime boundary

The app binds to <code>127.0.0.1</code> by default and needs no external service. Runtime XLSX files live under ignored <code>runtime/</code> storage. Analysis fingerprints workbook bytes before and after and must leave them identical.

This demo has no analyzed-model update, send, approval-execution, write-back, signing, vault, or identity-provider endpoint. Scenario reset only regenerates a bundled synthetic fixture. <code>HOST=0.0.0.0</code> is intended only for an isolated synthetic demo deployment.

## Reporting

Do not open public issues containing sensitive data. Report suspected vulnerabilities privately to the repository owner with reproduction steps and impact.
