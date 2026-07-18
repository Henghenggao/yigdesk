"""Run a privacy-aware bare Codex versus Yigdesk-assisted comparison."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from yigdesk.agent import CodexRunner, DEFAULT_MODEL
from yigdesk.benchmark import BareCodexRunner, load_case, run_comparison


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare bare Codex with engine-grounded Yigdesk assistance on one private case."
    )
    parser.add_argument("--case", type=Path, required=True, help="Private case JSON path.")
    parser.add_argument("--runs", type=int, default=1, help="Paired runs per mode; use 3+ for evidence.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Same Codex model for both modes.")
    parser.add_argument("--output-dir", type=Path, help="Ignored local result directory.")
    parser.add_argument(
        "--acknowledge-data-sharing",
        action="store_true",
        help="Confirm that request and input fields will be sent to OpenAI in both modes.",
    )
    args = parser.parse_args()
    if not args.acknowledge_data_sharing:
        parser.error("--acknowledge-data-sharing is required for private-case model runs")
    case = load_case(args.case)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or Path("benchmark-results") / f"{case['case_id']}-{timestamp}"
    report = run_comparison(
        case,
        runs=args.runs,
        output_dir=output_dir,
        bare_runner=BareCodexRunner(model=args.model),
        assisted_runner=CodexRunner(model=args.model),
    )
    print(
        json.dumps(
            {
                "case_id": report["case_id"],
                "model": report["model"],
                "runs_per_mode": report["runs_per_mode"],
                "aggregate": report["aggregate"],
                "output_dir": str(output_dir.resolve()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
