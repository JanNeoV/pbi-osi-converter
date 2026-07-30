from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from semantic_poc.agent.canonical_apply import render_candidate
from semantic_poc.agent.change_store import ChangeStore
from semantic_poc.agent.proposal_engine import propose_change
from semantic_poc.agent.proposal_models import ProposalStatus
from semantic_poc.agent.schemas import MetricChangeRequest
from semantic_poc.canonical_drift import check_canonical_drift
from semantic_poc.demo import (
    FIXTURE_CANONICAL,
    FIXTURE_DATASET,
    FIXTURE_MODEL,
    FIXTURE_PBIP,
    FIXTURE_SNOWFLAKE,
    create_demo_bundle,
    finalize_demo_bundle,
)
from semantic_poc.review_memory import (
    ReviewMemoryError,
    load_review_registry,
    suggest_review_rule,
)
from semantic_poc.src.models import DBT_SEMANTIC_YAML, PROJECT_ROOT


MANAGED_BY = "semantic-agent-end-to-end-poc"
SCHEMA_VERSION = 1
DEFAULT_OUTPUT = PROJECT_ROOT / "end-to-end-output"
REQUEST_EXAMPLE = (
    PROJECT_ROOT
    / "semantic_poc"
    / "examples"
    / "requests"
    / "valid_sbr_finishers_add_filter.json"
)
REVIEW_DECISION = PROJECT_ROOT / "semantic_poc" / "end_to_end" / "review-decision.accepted.yml"
REVIEW_RULE = (
    PROJECT_ROOT
    / "semantic_poc"
    / "review_memory"
    / "accepted"
    / "unit_conversion_seconds_to_hours.yml"
)
M8_DECISIONS = PROJECT_ROOT / "semantic_poc" / "demo" / "review-decisions.accepted.yml"
LEGACY_CONTRACT = PROJECT_ROOT / "semantic" / "triathlon_metric_contract.yml"
EXPECTED_SUMMARY = PROJECT_ROOT / "semantic_poc" / "end_to_end" / "expected_summary.json"
OUTPUT_FILES = frozenset(
    {
        "executive-summary.md",
        "manifest.json",
        "initial-conversion-findings.md",
        "accepted-review-decision.yml",
        "review-memory-entry.yml",
        "corrected-candidate-equivalence.md",
        "canonical-change-diff.md",
        "regenerated-target-diff.md",
        "synchronization-report.md",
        "immutability-report.md",
        "hashes.json",
    }
)
SUMMARY_LINES = (
    "Initial conversion: BLOCKED_PENDING_REVIEW",
    "Blocking finding: UNIT_CONVERSION_MISMATCH",
    "Human review: ACCEPTED",
    "Review memory: RECORDED",
    "Corrected candidate equivalence: PASSED",
    "Canonical change detected: YES",
    "Power BI target regenerated: YES",
    "Snowflake target regenerated: YES",
    "Cross-target semantic drift: 0",
    "Source modified: NO",
    "Deployment performed: NO",
    "END_TO_END_POC_ACCEPTED",
)


class EndToEndDemoError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        artifact: str | None = None,
        expected: Any = None,
        actual: Any = None,
        remediation: str = "Review the retained inputs and rerun from a clean checkout.",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.artifact = artifact
        self.expected = expected
        self.actual = actual
        self.remediation = remediation


def render_error(error: EndToEndDemoError) -> str:
    return "\n".join(
        (
            f"ERROR_CODE: {error.code}",
            f"STAGE: {error.stage}",
            f"ARTIFACT: {error.artifact or 'N/A'}",
            f"EXPECTED: {error.expected if error.expected is not None else 'N/A'}",
            f"ACTUAL: {error.actual if error.actual is not None else 'N/A'}",
            f"MESSAGE: {error}",
            f"REMEDIATION: {error.remediation}",
        )
    )


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_hash(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _portable_text_hash(path: Path) -> str:
    value = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return _sha_bytes(value.encode("utf-8"))


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(
        (candidate for candidate in path.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(path).as_posix(),
    ):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
    return digest.hexdigest()


def _bundle_hashes(root: Path) -> dict[str, str]:
    return {
        item.relative_to(root).as_posix(): _file_hash(item)
        for item in sorted(
            (candidate for candidate in root.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(root).as_posix(),
        )
    }


def _safe_output(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    configured = candidate.absolute()
    resolved = configured.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise EndToEndDemoError(
            "END_TO_END_OUTPUT_UNSAFE",
            "Output must remain inside the repository.",
            stage="prepare_output",
            artifact=str(path),
        ) from exc
    protected = (
        DBT_SEMANTIC_YAML,
        LEGACY_CONTRACT,
        FIXTURE_MODEL / "definition",
        REVIEW_DECISION,
        REVIEW_RULE,
    )
    if configured.is_symlink() or any(
        resolved == item.resolve()
        or resolved in item.resolve().parents
        or item.resolve() in resolved.parents
        for item in protected
    ):
        raise EndToEndDemoError(
            "END_TO_END_OUTPUT_UNSAFE",
            "Output overlaps a protected input or uses a symbolic link.",
            stage="prepare_output",
            artifact=str(path),
        )
    return resolved


def _protected_hashes() -> dict[str, str]:
    return {
        "models/semantic/triathlon_semantic.yml": _file_hash(DBT_SEMANTIC_YAML),
        "semantic/triathlon_metric_contract.yml": _file_hash(LEGACY_CONTRACT),
        "semantic_poc/tests/fixtures/conversion_benchmark/b_semantic_traps.pbip": _portable_text_hash(
            FIXTURE_PBIP
        ),
        "semantic_poc/tests/fixtures/conversion_benchmark/b_semantic_traps.SemanticModel/definition": _tree_hash(
            FIXTURE_MODEL / "definition"
        ),
        "semantic_poc/tests/fixtures/conversion_benchmark/b_expected_canonical.yml": _file_hash(
            FIXTURE_CANONICAL
        ),
        "semantic_poc/tests/fixtures/conversion_benchmark/b_synthetic_autopilot.yml": _file_hash(
            FIXTURE_SNOWFLAKE
        ),
        "semantic_poc/tests/fixtures/conversion_benchmark/b_semantic_traps.csv": _file_hash(
            FIXTURE_DATASET
        ),
    }


def _protected_runtime_hashes() -> dict[str, str]:
    hashes = _protected_hashes()
    hashes["semantic_poc/tests/fixtures/conversion_benchmark/b_semantic_traps.pbip"] = (
        _file_hash(FIXTURE_PBIP)
    )
    return hashes


def _load_decision(finding: Mapping[str, Any], source_snapshot: str) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(REVIEW_DECISION.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise EndToEndDemoError(
            "REVIEW_DECISION_INVALID", str(exc), stage="load_human_review", artifact=str(REVIEW_DECISION)
        ) from exc
    required = {
        "schema_version",
        "decision_id",
        "status",
        "fixture_id",
        "finding_id",
        "finding_category",
        "source_snapshot_sha256",
        "action",
        "actor",
        "accepted_at",
        "rationale",
        "resolution",
        "human_confirmation",
        "approval_scope",
        "deployment_authorized",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise EndToEndDemoError(
            "REVIEW_DECISION_INVALID",
            "Accepted review decision has unexpected fields.",
            stage="load_human_review",
            artifact=str(REVIEW_DECISION),
        )
    valid = (
        value["schema_version"] == 1
        and value["status"] == "ACCEPTED"
        and value["fixture_id"] == "B_SEMANTIC_TRAPS"
        and value["finding_id"] == finding["finding_id"]
        and value["finding_category"] == finding["category"]
        and value["source_snapshot_sha256"] == source_snapshot
        and value["action"] == "CORRECT"
        and value["human_confirmation"] == "RECORDED"
        and value["approval_scope"] == "FIXTURE_REVIEW_ONLY"
        and value["deployment_authorized"] is False
        and value["resolution"]
        == {
            "kind": "USE_CANONICAL_SCALED_SUM",
            "canonical_metric": "duration_hours",
            "source_field": "duration_seconds",
            "scale_divisor": "3600",
        }
    )
    if not valid:
        raise EndToEndDemoError(
            "REVIEW_DECISION_STALE",
            "Accepted review decision is not bound to the exact unit-conversion finding.",
            stage="load_human_review",
            artifact=str(REVIEW_DECISION),
        )
    return value


def _write_artifacts(root: Path, artifacts: Mapping[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for name, payload in sorted(artifacts.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _markdown_reports(
    finding: Mapping[str, Any],
    decision: Mapping[str, Any],
    reuse: Mapping[str, Any],
    drift: Mapping[str, Any],
) -> dict[str, bytes]:
    metric = drift["changed_canonical_metrics"][0]
    before_pbi = drift["baseline_targets"]["power_bi"][metric]
    current_pbi = drift["expected_targets"]["power_bi"][metric]
    before_sf = drift["baseline_targets"]["snowflake"][metric]
    current_sf = drift["expected_targets"]["snowflake"][metric]
    return {
        "executive-summary.md": (
            "# End-to-end semantic assurance POC\n\n"
            + "\n".join(f"- {line}" for line in SUMMARY_LINES[:-1])
            + "\n\nAccepted review memory is versioned guidance, not model training. "
            "The later canonical change is rendered only in an isolated candidate, and no deployment is included.\n"
        ).encode("utf-8"),
        "initial-conversion-findings.md": (
            "# Initial conversion finding\n\n"
            "- Initial conversion: `BLOCKED_PENDING_REVIEW`\n"
            f"- Finding ID: `{finding['finding_id']}`\n"
            f"- Category: `{finding['category']}`\n"
            f"- Source: `{finding['source_object']}`\n"
            f"- Unsafe expression: `{finding['source_expression']}`\n"
            f"- Governed expression: `{finding['generated_expression']}`\n"
            "- Human decision required: `YES`\n"
        ).encode("utf-8"),
        "corrected-candidate-equivalence.md": (
            "# Corrected candidate equivalence\n\n"
            "- Status: `PASSED`\n"
            "- Metrics passed: `5/5`\n"
            f"- Accepted decision: `{decision['decision_id']}`\n"
            f"- Exact reuse result: `{reuse['status']}`\n"
            "- Future human confirmation: `REQUIRED`\n"
        ).encode("utf-8"),
        "canonical-change-diff.md": (
            "# Canonical candidate change\n\n"
            "- Change ID: `chg_20260718T190000Z_90909090`\n"
            "- Proposal status: `PROPOSED`\n"
            f"- Changed canonical metric: `{metric}`\n"
            "- Before: `is_valid_sbr_finisher = 1`\n"
            "- Candidate: `finish_status = 'FIN' AND is_valid_sbr_finisher = 1`\n"
            "- Approval: `NOT_REQUESTED`\n"
            "- Real canonical modified: `NO`\n"
        ).encode("utf-8"),
        "regenerated-target-diff.md": (
            "# Regenerated target candidates\n\n"
            f"## Power BI `{metric}`\n\n"
            f"- Before: `{before_pbi['dax']}`\n"
            f"- Candidate: `{current_pbi['dax']}`\n\n"
            f"## Snowflake `{metric}`\n\n"
            f"- Before: `{before_sf['expr']}`\n"
            f"- Candidate: `{current_sf['expr']}`\n"
        ).encode("utf-8"),
        "synchronization-report.md": (
            "# Synchronization report\n\n"
            f"- Canonical changes: `{drift['canonical_changes']}`\n"
            f"- Expected Power BI changes: `{drift['expected_power_bi_changes']}`\n"
            f"- Expected Snowflake changes: `{drift['expected_snowflake_changes']}`\n"
            f"- Unexpected target-only drift: `{drift['unexpected_target_only_drift']}`\n"
            f"- Unrelated object changes: `{drift['unrelated_object_changes']}`\n"
            f"- Cross-target semantic drift: `{drift['cross_target_semantic_drift']}`\n"
            f"- Synchronization status: `{drift['synchronization_status']}`\n"
        ).encode("utf-8"),
    }


def _run_once(runtime: Path, bundle: Path) -> dict[str, Any]:
    runtime_source_before = _protected_runtime_hashes()
    source_before = _protected_hashes()
    changes = runtime / "changes"
    imports = runtime / "imports"
    reviews = runtime / "reviews"
    candidates = runtime / "candidates"
    for path in (changes, imports, reviews, candidates):
        path.mkdir(parents=True, exist_ok=True)

    m8_demo = candidates / "m8-demo"
    initial = create_demo_bundle(
        fixture="semantic-trap", output_dir=m8_demo, check=True
    )
    if initial.get("semantic_status") != "BLOCKED_PENDING_REVIEW":
        raise EndToEndDemoError(
            "INITIAL_CONVERSION_NOT_BLOCKED",
            "The unsafe conversion did not remain blocked.",
            stage="initial_conversion",
            expected="BLOCKED_PENDING_REVIEW",
            actual=initial.get("semantic_status"),
        )
    comparison = json.loads((m8_demo / "conversion-comparison.json").read_text(encoding="utf-8"))
    unit_findings = [
        item
        for item in comparison["findings"]
        if item["rule_id"] == "LINT_TIME_UNIT_CONVERSION"
        and item["category"] == "UNIT_CONVERSION_MISMATCH"
    ]
    if len(unit_findings) != 1:
        raise EndToEndDemoError(
            "UNIT_FINDING_NOT_EXACT",
            "Expected exactly one unit-conversion mismatch.",
            stage="initial_conversion",
            expected=1,
            actual=len(unit_findings),
        )
    finding = unit_findings[0]
    source_snapshot = initial["summary"] and json.loads(
        (m8_demo / "manifest.json").read_text(encoding="utf-8")
    )["source_hashes"][
        "semantic_poc/tests/fixtures/conversion_benchmark/b_semantic_traps.SemanticModel/definition"
    ]
    decision = _load_decision(finding, source_snapshot)
    try:
        registry = load_review_registry(REVIEW_RULE.parent.parent)
        matching_rules = [
            item
            for item in registry
            if item.rule_id == "review_unit_conversion_seconds_to_hours_v1"
        ]
        if len(matching_rules) != 1:
            raise ReviewMemoryError("The authoritative accepted review rule is not unique.")
        rule = matching_rules[0]
    except ReviewMemoryError as exc:
        raise EndToEndDemoError(
            "REVIEW_MEMORY_INVALID", str(exc), stage="record_review_memory", artifact=str(REVIEW_RULE)
        ) from exc
    if (
        rule.approval_provenance["decision_sha256"] != _file_hash(REVIEW_DECISION)
        or rule.approval_provenance["decision_id"] != decision["decision_id"]
    ):
        raise EndToEndDemoError(
            "REVIEW_MEMORY_PROVENANCE_MISMATCH",
            "Review-memory provenance does not match the accepted decision.",
            stage="record_review_memory",
        )
    persisted_rule = reviews / "accepted" / REVIEW_RULE.name
    persisted_rule.parent.mkdir(parents=True, exist_ok=True)
    persisted_rule.write_bytes(REVIEW_RULE.read_bytes())
    context = {
        "finding_category": rule.finding_category,
        "source_pattern": dict(rule.source_pattern),
        "applicability": dict(rule.applicability),
        "fixture_source_signature": dict(rule.fixture_source_signature),
        "match_signature_sha256": rule.match_signature_sha256,
    }
    reuse = suggest_review_rule(context, (rule,))
    if reuse["status"] != "REVIEW_RULE_SUGGESTED" or reuse["human_confirmation"] != "REQUIRED":
        raise EndToEndDemoError(
            "SAFE_REUSE_FAILED",
            "Exact accepted review memory did not remain suggestion-only.",
            stage="reuse_review_memory",
            actual=reuse,
        )

    finalized = candidates / "m8-finalized"
    finalization = finalize_demo_bundle(
        demo_run=m8_demo,
        decisions=M8_DECISIONS,
        output_dir=finalized,
    )
    equivalence = json.loads(
        (finalized / "evidence" / "equivalence-results.json").read_text(encoding="utf-8")
    )
    if (
        finalization["status"] != "READY_FOR_GOVERNED_FINALIZATION"
        or equivalence["status"] != "PASSED"
        or equivalence["passed"] != equivalence["total"]
        or equivalence["total"] != 5
    ):
        raise EndToEndDemoError(
            "CORRECTED_EQUIVALENCE_FAILED",
            "Corrected candidates did not pass 5/5 equivalence checks.",
            stage="corrected_equivalence",
            actual=equivalence,
        )

    canonical_root = runtime / "canonical"
    canonical_root.mkdir(parents=True)
    baseline = canonical_root / "accepted.yml"
    current = canonical_root / "candidate.yml"
    baseline.write_bytes(DBT_SEMANTIC_YAML.read_bytes())
    canonical_value = yaml.safe_load(baseline.read_text(encoding="utf-8"))
    if not isinstance(canonical_value, Mapping):
        raise EndToEndDemoError(
            "ISOLATED_CANONICAL_INVALID",
            "The isolated canonical baseline is not a YAML object.",
            stage="canonical_change",
            artifact=str(baseline),
        )
    isolated_manifest = canonical_root / "semantic_manifest.json"
    isolated_manifest.write_bytes(
        _json_bytes(
            {
                "semantic_models": canonical_value.get("semantic_models", []),
                "metrics": canonical_value.get("metrics", []),
            }
        )
    )
    request = MetricChangeRequest.from_dict(
        json.loads(REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    )
    proposal = propose_change(
        request,
        semantic_yaml_path=baseline,
        manifest_path=isolated_manifest,
        output_dir=candidates / "proposal-source-output",
    )
    if proposal.status is not ProposalStatus.PROPOSED or not proposal.cross_target_valid:
        raise EndToEndDemoError(
            "CANONICAL_CHANGE_PROPOSAL_FAILED",
            "The committed canonical change did not remain a supported proposal.",
            stage="canonical_change",
            actual=proposal.status.value,
        )
    ChangeStore(changes).save_proposal(proposal)
    current.write_bytes(render_candidate(baseline.read_bytes(), proposal.canonical_patch))
    drift = check_canonical_drift(baseline, current)
    expected_drift = {
        "canonical_changes": 1,
        "expected_power_bi_changes": 1,
        "expected_snowflake_changes": 1,
        "unexpected_target_only_drift": 0,
        "unrelated_object_changes": 0,
        "cross_target_semantic_drift": 0,
        "synchronization_status": "ALIGNED",
    }
    if any(drift[key] != value for key, value in expected_drift.items()):
        raise EndToEndDemoError(
            "CANONICAL_SYNCHRONIZATION_FAILED",
            "Canonical candidate and generated targets are not aligned.",
            stage="canonical_change",
            expected=expected_drift,
            actual={key: drift[key] for key in expected_drift},
        )

    source_after = _protected_hashes()
    runtime_source_after = _protected_runtime_hashes()
    source_modified = source_before != source_after
    if source_modified or runtime_source_before != runtime_source_after:
        raise EndToEndDemoError(
            "PROTECTED_SOURCE_CHANGED",
            "A protected canonical or Power BI source changed during the demo.",
            stage="immutability",
            expected={"portable": source_before, "runtime": runtime_source_before},
            actual={"portable": source_after, "runtime": runtime_source_after},
        )

    reports = _markdown_reports(finding, decision, reuse, drift)
    reports["accepted-review-decision.yml"] = REVIEW_DECISION.read_bytes()
    reports["review-memory-entry.yml"] = persisted_rule.read_bytes()
    hashes = {
        "schema_version": 1,
        "source_before": source_before,
        "source_after": source_after,
        "corrected_candidates": {
            "power_bi": _file_hash(finalized / "generated" / "powerbi-copy-plan.json"),
            "snowflake": _file_hash(
                finalized / "generated" / "snowflake-semantic-view.candidate.yml"
            ),
        },
        "canonical_drift": {
            "baseline": drift["baseline_hashes"],
            "current": drift["current_hashes"],
        },
    }
    reports["hashes.json"] = _json_bytes(hashes)
    reports["immutability-report.md"] = (
        "# Immutability report\n\n"
        "- Source PBIP/TMDL modified: `NO`\n"
        "- Real canonical dbt YAML modified: `NO`\n"
        "- Deprecated contract modified: `NO`\n"
        "- Isolated runtime stores: `changes`, `imports`, `reviews`, `candidates`\n"
        "- Deployment performed: `NO`\n"
    ).encode("utf-8")
    output_hashes = {name: _sha_bytes(payload) for name, payload in sorted(reports.items())}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "managed_by": MANAGED_BY,
        "initial_conversion": "BLOCKED_PENDING_REVIEW",
        "blocking_finding": "UNIT_CONVERSION_MISMATCH",
        "blocking_finding_id": finding["finding_id"],
        "human_review": "ACCEPTED",
        "review_memory": "RECORDED",
        "review_rule_id": rule.rule_id,
        "safe_reuse": reuse["status"],
        "human_confirmation": "REQUIRED",
        "corrected_equivalence": {"status": "PASSED", "passed": 5, "total": 5},
        "canonical_change_detected": True,
        "changed_canonical_metrics": drift["changed_canonical_metrics"],
        "power_bi_target_changes": drift["expected_power_bi_changes"],
        "snowflake_target_changes": drift["expected_snowflake_changes"],
        "unexpected_target_only_drift": drift["unexpected_target_only_drift"],
        "unrelated_object_changes": drift["unrelated_object_changes"],
        "cross_target_semantic_drift": drift["cross_target_semantic_drift"],
        "synchronization_status": drift["synchronization_status"],
        "source_modified": False,
        "deployment_performed": False,
        "isolated_runtime_stores": ["changes", "imports", "reviews", "candidates"],
        "output_hashes": output_hashes,
        "verdict": "END_TO_END_POC_ACCEPTED",
    }
    reports["manifest.json"] = _json_bytes(manifest)
    _write_artifacts(bundle, reports)
    return {
        "summary": {key: value for key, value in manifest.items() if key != "output_hashes"},
        "artifact_hashes": _bundle_hashes(bundle),
        "manifest": manifest,
    }


def create_end_to_end_bundle(
    *,
    output_dir: str | Path = DEFAULT_OUTPUT,
    check: bool = False,
) -> dict[str, Any]:
    output = _safe_output(output_dir)
    if output.exists():
        raise EndToEndDemoError(
            "END_TO_END_OUTPUT_EXISTS",
            "Output already exists; use --clean only after reviewing it.",
            stage="prepare_output",
            artifact=str(output_dir),
        )
    run_root = PROJECT_ROOT / ".tmp" / "end-to-end-runs"
    run_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="semantic-task9-", dir=run_root))
    try:
        first = _run_once(temporary / "first-runtime", temporary / "first-bundle")
        if check:
            repeat = _run_once(temporary / "repeat-runtime", temporary / "repeat-bundle")
            if first["artifact_hashes"] != repeat["artifact_hashes"]:
                raise EndToEndDemoError(
                    "END_TO_END_NOT_DETERMINISTIC",
                    "Repeated end-to-end output hashes differ.",
                    stage="check_repeatability",
                    expected=first["artifact_hashes"],
                    actual=repeat["artifact_hashes"],
                )
            if not EXPECTED_SUMMARY.is_file():
                raise EndToEndDemoError(
                    "END_TO_END_EXPECTATION_MISSING",
                    "Committed end-to-end expectations are missing.",
                    stage="check_expected_output",
                    artifact=str(EXPECTED_SUMMARY),
                )
            expected = json.loads(EXPECTED_SUMMARY.read_text(encoding="utf-8"))
            actual = {
                "summary": first["summary"],
                "artifact_hashes": first["artifact_hashes"],
            }
            if expected != actual:
                raise EndToEndDemoError(
                    "END_TO_END_EXPECTATION_MISMATCH",
                    "End-to-end output differs from committed expectations.",
                    stage="check_expected_output",
                    artifact=str(EXPECTED_SUMMARY),
                    expected=expected,
                    actual=actual,
                )
        output.parent.mkdir(parents=True, exist_ok=True)
        (temporary / "first-bundle").replace(output)
        return {
            "output_dir": output.relative_to(PROJECT_ROOT).as_posix(),
            **first["summary"],
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def clean_end_to_end_output(output_dir: str | Path = DEFAULT_OUTPUT) -> bool:
    output = _safe_output(output_dir)
    if not output.exists():
        return False
    manifest_path = output / "manifest.json"
    if output.is_symlink() or not manifest_path.is_file():
        raise EndToEndDemoError(
            "END_TO_END_CLEANUP_REFUSED",
            "Output has no managed Task 9 manifest.",
            stage="clean_output",
            artifact=str(output_dir),
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_files = {item.relative_to(output).as_posix() for item in output.rglob("*") if item.is_file()}
    valid = (
        manifest.get("managed_by") == MANAGED_BY
        and actual_files == OUTPUT_FILES
        and all(
            (output / name).is_file() and _file_hash(output / name) == expected
            for name, expected in manifest.get("output_hashes", {}).items()
        )
    )
    if not valid:
        raise EndToEndDemoError(
            "END_TO_END_CLEANUP_REFUSED",
            "Managed output is incomplete or modified; cleanup was refused.",
            stage="clean_output",
            artifact=str(output_dir),
        )
    shutil.rmtree(output)
    return True


__all__ = [
    "DEFAULT_OUTPUT",
    "EndToEndDemoError",
    "MANAGED_BY",
    "OUTPUT_FILES",
    "SUMMARY_LINES",
    "clean_end_to_end_output",
    "create_end_to_end_bundle",
    "render_error",
]
