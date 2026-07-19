from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .importer import WorkbookImportError, parse_decimal_field
from .session import bind_synthetic_session


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
    args = parser.parse_args()
    if args.command == "bind":
        try:
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
        except (WorkbookImportError, FileNotFoundError, ValueError) as error:
            code = getattr(error, "code", "BIND_FAILED")
            raise SystemExit(
                json.dumps(
                    {"status": "REJECTED", "code": code, "error": str(error)},
                    indent=2,
                )
            ) from error
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
