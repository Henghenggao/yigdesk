from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .importer import WorkbookImportError, parse_decimal_field
from .session import bind_synthetic_session


ROOT = Path(__file__).resolve().parents[1]


def call(base_url: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base_url.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/json", "X-Yigdesk-Action": "codex-work"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = json.loads(exc.read().decode("utf-8"))
        raise SystemExit(json.dumps({"status": exc.code, **detail}, indent=2)) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Codex Work bridge for the Yigdesk demo")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
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
    sub.add_parser("state")
    reset = sub.add_parser("reset")
    reset.add_argument("--scenario", choices=("ready", "hold"), default="ready")
    sub.add_parser("analyze")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("address")
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
    elif args.command == "state":
        result = call(args.url, "/api/state")
    elif args.command == "reset":
        result = call(args.url, "/api/reset", {"scenario_id": args.scenario})
    elif args.command == "analyze":
        result = call(args.url, "/api/analyze", {})
    else:
        result = call(args.url, f"/api/inspect?address={quote(args.address)}")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
