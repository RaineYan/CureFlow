"""CLI wrapper for the bounded OpenAI A/B pilot."""
from __future__ import annotations

import argparse
from pathlib import Path

from experiments.openai_ab_pilot import MAX_LIVE_CALLS, run_ab


def main() -> int:
    parser = argparse.ArgumentParser(description="CureFlow OpenAI A/B pilot")
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--confirm-max-calls", type=int)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts") / "openai_ab_pilot",
    )
    args = parser.parse_args()
    report = run_ab(
        output_dir=args.output_dir,
        execute_live=args.execute_live,
        confirm_max_calls=args.confirm_max_calls,
    )
    print(f"MODE={report['mode']}")
    print(f"MAX_LIVE_CALLS={MAX_LIVE_CALLS}")
    print(f"OPENAI_ACCEPTED={report['summary']['openaiAccepted']}/{len(report['openai'])}")
    print(f"REPORT={args.output_dir / 'openai_ab_pilot.md'}")
    if args.execute_live and not report["gate"]["allowed"]:
        print("BLOCKERS=" + ",".join(report["gate"]["blockers"]))
        return 2
    return 0 if not report["openai"] or all(
        row["accepted"] for row in report["openai"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())

