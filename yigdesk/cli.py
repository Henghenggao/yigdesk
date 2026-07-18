from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


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
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state")
    reset = sub.add_parser("reset")
    reset.add_argument("--scenario", choices=("ready", "hold"), default="ready")
    sub.add_parser("analyze")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("address")
    args = parser.parse_args()
    if args.command == "state":
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
