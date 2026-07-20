from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .importer import WorkbookImportError, parse_decimal_field
from .session import bind_synthetic_session, load_active_session


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline session-binding intake for the Yigdesk decision blackboard"
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        default=Path(os.environ.get("YIGDESK_RUNTIME", ROOT / "runtime")),
        help="Local ignored runtime used for immutable bound sessions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    bind = sub.add_parser(
        "bind", help="Bind a synthetic XLSX without requiring the web app."
    )
    bind.add_argument("--file", type=Path, required=True)
    bind.add_argument("--discount", required=True)
    bind.add_argument("--floor", required=True)
    bind.add_argument("--current-discount", default="0")
    bind.add_argument("--session")
    sub.add_parser("state", help="Report the active immutable bound session.")
    args = parser.parse_args()
    try:
        if args.command == "bind":
            result = bind_synthetic_session(
                args.file,
                runtime_root=args.runtime,
                requested_discount_pct=parse_decimal_field("discount", args.discount),
                margin_floor_pct=parse_decimal_field("floor", args.floor),
                current_discount_pct=parse_decimal_field(
                    "current_discount", args.current_discount
                ),
                session_id=args.session,
            )
        else:
            bound = load_active_session(args.runtime)
            scenario = bound.manifest["scenario"]
            result = {
                "status": "BOUND",
                "session_id": bound.session_id,
                "revision_id": bound.manifest["revision_id"],
                "source_fingerprint": bound.manifest["source"]["sha256"],
                "projection_fingerprint": bound.manifest["projection"]["sha256"],
                "source": {
                    "filename": bound.manifest["source"]["filename"],
                    "source_cell_count": bound.manifest["source"]["source_cell_count"],
                    "analysis_bytes_unchanged": True,
                },
                "decision": {
                    "current_discount_pct": scenario["current_discount_pct"],
                    "requested_discount_pct": scenario["requested_discount_pct"],
                    "margin_floor_pct": scenario["margin_floor_pct"],
                },
            }
    except (WorkbookImportError, FileNotFoundError, ValueError) as error:
        code = getattr(error, "code", "BIND_FAILED" if args.command == "bind" else "STATE_UNAVAILABLE")
        raise SystemExit(
            json.dumps(
                {"status": "REJECTED", "code": code, "error": str(error)},
                indent=2,
            )
        ) from error
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
