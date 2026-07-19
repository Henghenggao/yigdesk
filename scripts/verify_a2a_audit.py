"""Verify a Codex A2A audit produced by the project Yigdesk MCP server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yigdesk.a2a import verify_council_audit
from yigdesk.a2a import CouncilAuditError
from yigdesk.session import load_active_session, resolve_council_audit_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path, nargs="?")
    parser.add_argument("--runtime", type=Path, default=Path("runtime"))
    args = parser.parse_args()
    bound = None if args.audit else load_active_session(args.runtime)
    audit = args.audit or resolve_council_audit_path(args.runtime)
    events = [
        json.loads(line)
        for line in audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    verified = verify_council_audit(events)
    if bound is not None:
        revision = verified["revision"]
        if (
            revision["revision_id"] != bound.manifest["revision_id"]
            or revision["source_fingerprint"] != bound.manifest["source"]["sha256"]
        ):
            raise CouncilAuditError("Verified audit does not match the active session.")
    result = {"audit": str(audit), **verified}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
