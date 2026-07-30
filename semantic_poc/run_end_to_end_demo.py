from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from semantic_poc.end_to_end_demo import (  # noqa: E402
    EndToEndDemoError,
    SUMMARY_LINES,
    clean_end_to_end_output,
    create_end_to_end_bundle,
    render_error,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deterministic Task 9 end-to-end POC.")
    parser.add_argument("--output-dir", default="end-to-end-output")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.clean:
            clean_end_to_end_output(args.output_dir)
        result = create_end_to_end_bundle(output_dir=args.output_dir, check=args.check)
        if result.get("verdict") != "END_TO_END_POC_ACCEPTED":
            raise EndToEndDemoError(
                "END_TO_END_VERDICT_NOT_ACCEPTED",
                "The end-to-end POC did not reach its accepted local-only verdict.",
                stage="render_verdict",
                expected="END_TO_END_POC_ACCEPTED",
                actual=result.get("verdict"),
            )
    except EndToEndDemoError as error:
        print("END_TO_END_POC_NOT_ACCEPTED")
        print(render_error(error), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - protects the public command boundary
        error = EndToEndDemoError(
            "END_TO_END_UNEXPECTED_FAILURE",
            f"Unexpected end-to-end failure: {type(exc).__name__}: {exc}",
            stage="unexpected_failure",
            remediation="Inspect the diagnostic and rerun from a clean checkout.",
        )
        print("END_TO_END_POC_NOT_ACCEPTED")
        print(render_error(error), file=sys.stderr)
        return 1
    for line in SUMMARY_LINES:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
