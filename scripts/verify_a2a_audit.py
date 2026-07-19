"""Verify a Codex A2A audit produced by the project Yigdesk MCP server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yigdesk.a2a import verify_council_audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path)
    args = parser.parse_args()
    events = [
        json.loads(line)
        for line in args.audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(json.dumps(verify_council_audit(events), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
