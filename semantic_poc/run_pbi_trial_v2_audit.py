from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from semantic_poc.agent.powerbi_snowflake_audit import (
    AuditInputError,
    AuditStateError,
    AuditStaleEvidenceError,
    audit_powerbi_snowflake,
    write_fixed_audit_artifacts,
)
from semantic_poc.agent.pbi_trial_v2_demo import prepare_demo


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = (
    REPOSITORY_ROOT
    / "semantic_poc"
    / "benchmark"
    / "pbi_trial_v2"
    / "fixtures"
    / "pbi_trial.SemanticModel"
)
SNOWFLAKE_YAML = (
    REPOSITORY_ROOT / "pbit" / "snowflake_semantic_view" / "pbi_trial.yaml"
)
BENCHMARK_SPEC = (
    REPOSITORY_ROOT
    / "semantic_poc"
    / "benchmark"
    / "pbi_trial_v2"
    / "measure-cases.yml"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or verify the fixed Power BI trial v2 conversion audit."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify committed outputs byte-for-byte without writing files.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print a machine-readable run summary."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        audit = audit_powerbi_snowflake(
            model_dir=MODEL_DIR,
            snowflake_yaml=SNOWFLAKE_YAML,
            benchmark_spec=BENCHMARK_SPEC,
            repository_root=REPOSITORY_ROOT,
        )
        artifacts = write_fixed_audit_artifacts(
            audit, repository_root=REPOSITORY_ROOT, check=args.check
        )
        demo = prepare_demo(
            audit, repository_root=REPOSITORY_ROOT, check=args.check
        )
    except AuditStaleEvidenceError as exc:
        print(f"STALE_AUDIT_EVIDENCE: {exc}", file=sys.stderr)
        return 4
    except AuditStateError as exc:
        print(f"AUDIT_ARTIFACT_STATE_CONFLICT: {exc}", file=sys.stderr)
        return 4
    except AuditInputError as exc:
        print(f"AUDIT_INPUT_INVALID: {exc}", file=sys.stderr)
        return 2
    result = {
        "audit_id": audit.audit_id,
        **demo,
        "check": args.check,
        "summary": dict(audit.summary),
        "artifacts": artifacts,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        action = "verified" if args.check else "generated"
        print(
            f"{action.capitalize()} {len(artifacts)} audit artifacts for "
            f"{audit.summary['source_measure_count']} Power BI measures."
        )
        print(
            f"Matched {audit.summary['matched_measure_count']}; "
            f"omitted {audit.summary['omitted_measure_count']}; "
            f"proven caught controls {audit.summary['proven_caught_count']}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
