from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from semantic_poc.demo import (  # noqa: E402
    DemoError,
    clean_demo_output,
    create_demo_bundle,
    persist_demo_failure,
    render_demo_error,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the offline deterministic Milestone 8 demo.")
    parser.add_argument("--output-dir", default="demo-output")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.clean:
            clean_demo_output(args.output_dir)
        result = create_demo_bundle(
            fixture="semantic-trap",
            output_dir=args.output_dir,
            check=args.check,
        )
    except DemoError as error:
        persist_demo_failure(error, args.output_dir)
        print("POC_DEMO_NOT_ACCEPTED")
        print(render_demo_error(error), file=sys.stderr)
        return 1
    verdict = result.get("verdict")
    if args.check and verdict != "POC_DEMO_ACCEPTED":
        error = DemoError(
            "DEMO_VERDICT_NOT_ACCEPTED",
            "The checked demo did not produce the accepted reproducibility verdict.",
            stage="render_verdict",
            artifact=result.get("output_dir", args.output_dir),
            expected="POC_DEMO_ACCEPTED",
            actual=verdict,
        )
        persist_demo_failure(error, args.output_dir)
        print("POC_DEMO_NOT_ACCEPTED")
        print(render_demo_error(error), file=sys.stderr)
        return 1
    print("POC_DEMO_ACCEPTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
