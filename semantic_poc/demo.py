"""Deterministic, offline Milestone 8 demonstration packaging.

The demo is an evidence orchestrator.  It does not approve, apply, roll back,
edit source Power BI, or connect to Snowflake.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping, Sequence

import yaml

from semantic_poc.agent.conversion_review import build_conversion_findings
from semantic_poc.agent.import_models import canonical_json_text
from semantic_poc.agent.import_store import ImportStore
from semantic_poc.agent.import_workflow import create_import_run
from semantic_poc.agent.powerbi_import import (
    PowerBIModelInventory,
    render_inventory_markdown,
    resolve_powerbi_model_dir,
)
from semantic_poc.benchmark import (
    FIXTURES,
    build_benchmark,
    run_candidate_golden_tests,
    run_golden_tests,
)
from semantic_poc.src.models import DBT_SEMANTIC_YAML, PROJECT_ROOT, load_yaml
from semantic_poc.src.semantic_ir import (
    SupportClassification,
    build_canonical_metric_ir_index,
    generate_dax_definition,
    generate_snowflake_definition,
)


DEMO_SCHEMA_VERSION = 1
DEMO_MANAGED_BY = "semantic-agent-m8-demo"
FIXTURE_NAME = "semantic-trap"
PACKAGE_NAME = "triathlon-semantic-contract-poc"
FIXTURE_ROOT = PROJECT_ROOT / "semantic_poc" / "tests" / "fixtures" / "conversion_benchmark"
FIXTURE_PBIP = FIXTURE_ROOT / "b_semantic_traps.pbip"
FIXTURE_MODEL = FIXTURE_ROOT / "b_semantic_traps.SemanticModel"
FIXTURE_CANONICAL = FIXTURE_ROOT / "b_expected_canonical.yml"
FIXTURE_SNOWFLAKE = FIXTURE_ROOT / "b_synthetic_autopilot.yml"
FIXTURE_DATASET = FIXTURE_ROOT / "b_semantic_traps.csv"
LEGACY_CONTRACT = PROJECT_ROOT / "semantic" / "triathlon_metric_contract.yml"
EXPECTED_SUMMARY = PROJECT_ROOT / "semantic_poc" / "demo" / "expected_summary.json"
EXPECTED_FINDINGS = PROJECT_ROOT / "semantic_poc" / "demo" / "expected_findings.json"
SOURCE_TEXT_SUFFIXES = frozenset({".csv", ".json", ".pbip", ".pbism", ".tmdl", ".yaml", ".yml"})
CHECK_ACCEPTED = "ACCEPTED"
CHECK_FAILED = "FAILED"
CHECK_NOT_ACCEPTED = "NOT_ACCEPTED"
CHECK_NOT_CHECKED = "NOT_CHECKED"
CHECK_PENDING = "PENDING"


class DemoError(RuntimeError):
    """Expected safe failure with a stable diagnostic code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str | None = None,
        artifact: str | None = None,
        expected: Any = None,
        actual: Any = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage or _error_stage(code)
        self.artifact = artifact or "N/A"
        self.expected = expected if expected is not None else _error_expected(code)
        self.actual = actual if actual is not None else message
        self.remediation = remediation or _error_remediation(code)
        self.diagnostics: str | None = None
        self.retained_bundle: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "code": self.code,
            "message": str(self),
            "stage": self.stage,
            "artifact": self.artifact,
            "expected": self.expected,
            "actual": self.actual,
            "remediation": self.remediation,
            "diagnostics": self.diagnostics,
            "retained_bundle": self.retained_bundle,
        }
        return value


def _error_stage(code: str) -> str:
    if code.startswith("DEMO_CLEANUP"):
        return "clean_output"
    if code.startswith("DEMO_EXPECTATION") or code in {"DEMO_SUMMARY_MISMATCH", "DEMO_FINDINGS_MISMATCH"}:
        return "check_expected_bundle"
    if code == "DEMO_NOT_DETERMINISTIC":
        return "check_repeatability"
    if code in {"DEMO_SOURCE_CHANGED", "DEMO_UNSAFE_SOURCE", "DEMO_SOURCE_ENCODING_INVALID"}:
        return "verify_source_immutability"
    if code == "DEMO_ARTIFACT_HASH_MISMATCH":
        return "write_staged_bundle"
    if code.startswith("DEMO_FINALIZE") or code.startswith("DEMO_DECISIONS"):
        return "finalize_bundle"
    if code.startswith("DEMO_OUTPUT"):
        return "resolve_output"
    if code.startswith("DEMO_INPUT") or code.startswith("DEMO_PATH") or code.startswith("DEMO_FIXTURE"):
        return "resolve_inputs"
    if code.startswith("DEMO_SNOWFLAKE"):
        return "load_snowflake_evidence"
    if code.startswith("DEMO_CANDIDATE"):
        return "build_candidate"
    return "run_demo"


def _error_expected(code: str) -> str:
    if code.startswith("DEMO_CLEANUP"):
        return "an unchanged managed Milestone 8 bundle or an absent output directory"
    if code.startswith("DEMO_FINALIZE"):
        return "a complete, accepted, hash-valid demo bundle"
    if code.startswith("DEMO_INPUT") or code.startswith("DEMO_PATH"):
        return "valid repository-contained input"
    return "stage completes successfully"


def _error_remediation(code: str) -> str:
    if code.startswith("DEMO_CLEANUP"):
        return "Inspect the existing directory and choose a new output path; unknown or modified content is never removed automatically."
    if code.startswith("DEMO_EXPECTATION") or code in {"DEMO_SUMMARY_MISMATCH", "DEMO_FINDINGS_MISMATCH"}:
        return "Compare the retained bundle with committed expectations and correct only evidence-backed representation drift."
    if code == "DEMO_NOT_DETERMINISTIC":
        return "Compare both staged runs and remove platform-dependent content before retrying."
    if code.startswith("DEMO_FINALIZE"):
        return "Run the checked demo successfully and finalize only its accepted, unchanged output bundle."
    return "Review the diagnostic artifact, correct the reported stage, and rerun from the repository root."


def _tool_version() -> str:
    return version(PACKAGE_NAME)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_hash(path: Path) -> str:
    return _sha256(path.read_bytes())


def _source_bytes(path: Path) -> bytes:
    payload = path.read_bytes()
    if path.suffix.casefold() not in SOURCE_TEXT_SUFFIXES:
        return payload
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DemoError(
            "DEMO_SOURCE_ENCODING_INVALID",
            f"Protected text source is not valid UTF-8: {_relative(path)}",
        ) from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _source_file_hash(path: Path) -> str:
    return _sha256(_source_bytes(path))


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for entry in path.rglob("*"):
        if entry.is_symlink():
            raise DemoError("DEMO_UNSAFE_SOURCE", f"Source tree contains a symbolic link: {entry}")
    for item in sorted(
        (entry for entry in path.rglob("*") if entry.is_file()),
        key=lambda value: value.relative_to(path).as_posix(),
    ):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        payload = item.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _source_tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for entry in path.rglob("*"):
        if entry.is_symlink():
            raise DemoError("DEMO_UNSAFE_SOURCE", f"Source tree contains a symbolic link: {entry}")
    for item in sorted(
        (entry for entry in path.rglob("*") if entry.is_file()),
        key=lambda value: value.relative_to(path).as_posix(),
    ):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        payload = _source_bytes(item)
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    resolved = path.resolve(strict=True)
    root = PROJECT_ROOT.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise DemoError("DEMO_PATH_OUTSIDE_REPOSITORY", f"Path must be repository-contained: {path}")
    return resolved.relative_to(root).as_posix()


def _argument_path(path: str | Path) -> Path:
    raw = str(path)
    if os.sep == "/" and "\\" in raw:
        raw = raw.replace("\\", "/")
    return Path(raw)


def _relative_display(path: str | Path) -> str:
    requested = _argument_path(path)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    try:
        return requested.resolve(strict=False).relative_to(
            PROJECT_ROOT.resolve(strict=True)
        ).as_posix()
    except ValueError:
        return str(path)


def _safe_input(path: str | Path, *, label: str, file: bool | None = None) -> Path:
    requested = _argument_path(path)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise DemoError("DEMO_INPUT_NOT_FOUND", f"{label} does not exist: {path}") from exc
    root = PROJECT_ROOT.resolve(strict=True)
    if not resolved.is_relative_to(root) or requested.is_symlink():
        raise DemoError("DEMO_PATH_OUTSIDE_REPOSITORY", f"{label} must be repository-contained.")
    if file is True and not resolved.is_file():
        raise DemoError("DEMO_INPUT_INVALID", f"{label} must be a regular file.")
    if file is False and not resolved.is_dir():
        raise DemoError("DEMO_INPUT_INVALID", f"{label} must be a directory.")
    return resolved


def _safe_output(path: str | Path, *, protected: Sequence[Path]) -> Path:
    requested = _argument_path(path)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    root = PROJECT_ROOT.resolve(strict=True)
    candidate = requested.resolve(strict=False)
    if not candidate.is_relative_to(root) or candidate == root:
        raise DemoError("DEMO_OUTPUT_UNSAFE", "Output directory must be a new repository-contained child directory.")
    if requested.exists() or candidate.exists():
        raise DemoError("DEMO_OUTPUT_EXISTS", f"Output directory already exists: {path}")
    for parent in [candidate.parent, *candidate.parents]:
        if parent == root.parent:
            break
        if parent.exists() and parent.is_symlink():
            raise DemoError("DEMO_OUTPUT_UNSAFE", "Output directory cannot traverse a symbolic link.")
        if parent == root:
            break
    for item in protected:
        resolved = item.resolve(strict=True)
        if candidate == resolved or candidate.is_relative_to(resolved) or resolved.is_relative_to(candidate):
            raise DemoError("DEMO_OUTPUT_OVERLAP", f"Output directory overlaps protected source: {_relative(resolved)}")
    return candidate


def _json_bytes(value: Any) -> bytes:
    return canonical_json_text(value, pretty=True).encode("utf-8")


def _text_bytes(value: str) -> bytes:
    return value.rstrip().encode("utf-8") + b"\n"


def _yaml_bytes(value: Any) -> bytes:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode("utf-8")


def _artifact_hashes(artifacts: Mapping[str, bytes]) -> dict[str, str]:
    return {name: _sha256(payload) for name, payload in sorted(artifacts.items())}


def _write_artifacts(root: Path, artifacts: Mapping[str, bytes]) -> None:
    for name, payload in sorted(artifacts.items()):
        target = root / Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def bundle_hashes(root: Path) -> dict[str, str]:
    return {
        item.relative_to(root).as_posix(): _file_hash(item)
        for item in sorted((entry for entry in root.rglob("*") if entry.is_file()), key=lambda entry: entry.as_posix())
    }


def _autopilot_data(path: Path | None) -> tuple[Mapping[str, Any] | None, str]:
    if path is None:
        return None, "NOT_AVAILABLE"
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DemoError("DEMO_SNOWFLAKE_YAML_INVALID", f"Snowflake comparison YAML is invalid: {exc}") from exc
    if not isinstance(value, Mapping):
        raise DemoError("DEMO_SNOWFLAKE_YAML_INVALID", "Snowflake comparison YAML must contain a mapping.")
    metadata = value.get("benchmark_metadata")
    status = (
        "SYNTHETIC_TEST_FIXTURE"
        if isinstance(metadata, Mapping) and metadata.get("evidence_kind") == "SYNTHETIC_TEST_FIXTURE"
        else "IMPORTED_YAML"
    )
    return value, status


def _demo_run_payload(run: Any, model_path: Path) -> tuple[dict[str, Any], str]:
    payload = run.to_dict()
    definition = resolve_powerbi_model_dir(model_path, PROJECT_ROOT)
    source_snapshot_hash = _source_tree_hash(definition)
    inventory = payload.get("inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("model"), dict):
        raise DemoError(
            "DEMO_INVENTORY_INVALID",
            "Power BI import inventory is missing its model provenance.",
            stage="build_inventory",
            artifact="model-inventory.json#model",
            expected="model provenance object",
            actual=type(inventory).__name__,
        )
    inventory["model"]["source_tree_hash"] = source_snapshot_hash
    return payload, source_snapshot_hash


def _fixture_result(model_path: Path, snowflake_path: Path | None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="semantic-m8-fixture-") as temporary:
        store = ImportStore(Path(temporary) / "imports")
        run = create_import_run(
            model_path,
            store=store,
            canonical_yaml_path=FIXTURE_CANONICAL,
            now=datetime(2026, 7, 18, 18, 0, tzinfo=timezone.utc),
            entropy="00000002",
        )
        autopilot, autopilot_status = _autopilot_data(snowflake_path)
        findings = build_conversion_findings(run, autopilot=autopilot)
        golden = run_golden_tests(run, FIXTURE_DATASET)
        run_payload, source_snapshot_hash = _demo_run_payload(run, model_path)
        return {
            "fixture_id": "B_SEMANTIC_TRAPS",
            "source_snapshot_sha256": source_snapshot_hash,
            "semantic_content_sha256": run.semantic_content_hash,
            "autopilot_status": autopilot_status,
            "inventory": run_payload["inventory"],
            "recognized_patterns": [
                {
                    "source_object": f"{item.get('source_table')}[{item.get('source_measure')}]",
                    "pattern": item.get("recognized_pattern"),
                    "classification": item.get("classification"),
                }
                for item in run_payload["classifications"]
            ],
            "candidate_ir": [item.get("candidate_ir") for item in run_payload["classifications"]],
            "repository_snowflake": [item.get("regenerated_snowflake") for item in run_payload["classifications"]],
            "classifications": run_payload["classifications"],
            "findings": [item.to_dict() for item in findings],
            "golden_results": golden,
            "actual_metric_failures": sorted(
                item["source_measure"] for item in golden if item["status"] == "FAIL"
            ),
        }


def _project_result(model_path: Path, snowflake_path: Path | None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="semantic-m8-project-") as temporary:
        store = ImportStore(Path(temporary) / "imports")
        run = create_import_run(
            model_path,
            store=store,
            canonical_yaml_path=DBT_SEMANTIC_YAML,
            now=datetime(2000, 1, 1, tzinfo=timezone.utc),
            entropy="00000008",
        )
        autopilot, autopilot_status = _autopilot_data(snowflake_path)
        findings = build_conversion_findings(run, autopilot=autopilot)
        run_payload, source_snapshot_hash = _demo_run_payload(run, model_path)
        return {
            "fixture_id": None,
            "source_snapshot_sha256": source_snapshot_hash,
            "semantic_content_sha256": run.semantic_content_hash,
            "autopilot_status": autopilot_status,
            "inventory": run_payload["inventory"],
            "recognized_patterns": [
                {
                    "source_object": f"{item.get('source_table')}[{item.get('source_measure')}]",
                    "pattern": item.get("recognized_pattern"),
                    "classification": item.get("classification"),
                }
                for item in run_payload["classifications"]
            ],
            "candidate_ir": [item.get("candidate_ir") for item in run_payload["classifications"]],
            "repository_snowflake": [item.get("regenerated_snowflake") for item in run_payload["classifications"]],
            "classifications": run_payload["classifications"],
            "findings": [item.to_dict() for item in findings],
            "golden_results": [],
            "actual_metric_failures": [],
        }


def _counts(result: Mapping[str, Any]) -> dict[str, int]:
    findings = list(result["findings"])
    recognized = list(result["recognized_patterns"])
    support_records = list(result["inventory"].get("object_support_records", []))
    unsupported_constructs = {
        construct
        for record in support_records
        for construct in record.get("unsupported_constructs", [])
    }
    return {
        "power_bi_objects_analyzed": len(support_records),
        "measures_analyzed": len(result["inventory"].get("measures", [])),
        "exact_conversions": sum(item["classification"] == "SUPPORTED_EXACT" for item in recognized),
        "supported_with_explicit_mapping": sum(item["classification"] == "SUPPORTED_WITH_MAPPING" for item in recognized),
        "manual_review": sum(item["classification"] == "MANUAL_REVIEW_REQUIRED" for item in recognized),
        "unsupported_constructs": len(unsupported_constructs),
        "blocking_findings": sum(item["severity"] == "BLOCKING" for item in findings),
        "warnings": sum(item["severity"] == "WARNING" for item in findings),
        "informational_findings": sum(item["severity"] == "INFO" for item in findings),
        "equivalence_tests_passed": sum(item["status"] == "PASS" for item in result["golden_results"]),
        "equivalence_tests_failed": sum(item["status"] == "FAIL" for item in result["golden_results"]),
    }


def _assurance(result: Mapping[str, Any], counts: Mapping[str, int]) -> dict[str, str]:
    return {
        "syntactic_validity": "PASSED",
        "structural_compatibility": "MANUAL_REVIEW_REQUIRED" if any(
            item["category"] == "RELATIONSHIP_MISMATCH" for item in result["findings"]
        ) else "PASSED",
        "semantic_equivalence": "FAILED" if counts["equivalence_tests_failed"] else (
            "PASSED" if result["golden_results"] else "NOT_AVAILABLE"
        ),
        "human_approval": "PENDING",
    }


def _status(counts: Mapping[str, int]) -> str:
    return "BLOCKED_PENDING_REVIEW" if counts["blocking_findings"] else "READY_FOR_REVIEW"


def _support_matrix_md(records: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Support matrix",
        "",
        "| Object | Kind | Classification | Rule | Confidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in records:
        lines.append(
            f"| {item.get('object_name')} | `{item.get('object_kind')}` | `{item.get('classification')}` | "
            f"`{item.get('classifier_rule_id')}` | `{item.get('confidence')}` |"
        )
    return "\n".join(lines) + "\n"


def _comparison_md(result: Mapping[str, Any]) -> str:
    lines = [
        "# Conversion comparison",
        "",
        f"Snowflake comparison evidence: `{result['autopilot_status']}`",
        "",
        "| Finding | Severity | Category | Source | Generated |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in result["findings"]:
        lines.append(
            f"| `{item['finding_id']}` | `{item['severity']}` | `{item['category']}` | "
            f"{item['source_object']} | {item.get('generated_object') or '-'} |"
        )
    lines.extend(["", "Syntactic generation is not semantic acceptance.", ""])
    return "\n".join(lines)


def _lint_md(findings: Sequence[Mapping[str, Any]]) -> str:
    lines = ["# Semantic lint report", "", f"Findings: {len(findings)}", ""]
    for item in findings:
        lines.extend(
            [
                f"## {item['finding_id']}",
                "",
                f"- Rule: `{item['rule_id']}`",
                f"- Severity: `{item['severity']}`",
                f"- Category: `{item['category']}`",
                f"- Source: {item['source_object']}",
                f"- Generated: {item.get('generated_object') or 'NOT_AVAILABLE'}",
                f"- Evidence: {'; '.join(item.get('evidence') or [])}",
                f"- Recommendation: {item['recommended_correction']}",
                f"- Automatic correction safe: `{str(item['automatic_correction_safe']).upper()}`",
                "",
            ]
        )
    return "\n".join(lines)


def _executive_summary(
    counts: Mapping[str, int],
    assurance: Mapping[str, str],
    status: str,
    blockers: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# Semantic migration POC executive summary",
        "",
        "> Automated conversion can move semantic metadata between platforms. This POC adds deterministic semantic assurance, explicit human review, and synchronized Power BI/Snowflake maintenance around the conversion.",
        "",
        f"- Power BI objects analyzed: **{counts['power_bi_objects_analyzed']}**",
        f"- Measures analyzed: **{counts['measures_analyzed']}**",
        f"- Exact conversions: **{counts['exact_conversions']}**",
        f"- Supported with explicit mapping: **{counts['supported_with_explicit_mapping']}**",
        f"- Blocking semantic findings: **{counts['blocking_findings']}**",
        f"- Warnings: **{counts['warnings']}**",
        f"- Unsupported constructs: **{counts['unsupported_constructs']}**",
        f"- Equivalence tests passed: **{counts['equivalence_tests_passed']}**",
        "- Source Power BI modified: **NO**",
        "- Deployment performed: **NO**",
        f"- Overall status: **{status}**",
        "",
        "## Assurance boundaries",
        "",
        f"- Syntactic validity: `{assurance['syntactic_validity']}`",
        f"- Structural compatibility: `{assurance['structural_compatibility']}`",
        f"- Semantic equivalence: `{assurance['semantic_equivalence']}`",
        f"- Human approval: `{assurance['human_approval']}`",
        "",
        "A parsed Snowflake YAML is not semantically accepted without equivalence evidence and explicit review.",
        "",
        "## Blocking findings",
        "",
    ]
    for item in blockers:
        lines.extend(
            [
                f"### {item['finding_id']}",
                "",
                f"- Source object: {item['source_object']}",
                f"- Generated object: {item.get('generated_object') or 'NOT_AVAILABLE'}",
                f"- Category: `{item['category']}`",
                f"- Severity: `{item['severity']}`",
                f"- Evidence: {'; '.join(item.get('evidence') or [])}",
                f"- Why it matters: {item.get('why_it_matters') or item['recommended_correction']}",
                f"- Deterministic recommendation: {item['recommended_correction']}",
                f"- Automatic correction safety: `{str(item['automatic_correction_safe']).upper()}`",
                "- Required reviewer decision: `ACCEPT`, `CORRECT`, or `REJECT` as permitted by the finding.",
                "",
            ]
        )
    return "\n".join(lines)


def _proposals_md(classifications: Sequence[Mapping[str, Any]]) -> str:
    lines = ["# Canonicalization proposals", ""]
    for item in classifications:
        lines.extend(
            [
                f"## {item.get('source_table')}[{item.get('source_measure')}]",
                "",
                f"- Classification: `{item.get('classification')}`",
                f"- Canonical metric: `{(item.get('mapping') or {}).get('canonical_metric') or 'UNRESOLVED'}`",
                f"- Authority: `POWER_BI_EVIDENCE_ONLY`",
                "",
            ]
        )
    return "\n".join(lines)


def _candidate_outputs(registered_fixture: bool) -> dict[str, bytes]:
    if not registered_fixture:
        envelope = {
            "schema_version": 1,
            "status": "MANUAL_REVIEW_REQUIRED",
            "reason": "A complete candidate is not deterministically available for this unregistered project.",
        }
        return {
            "generated/canonical-contract.candidate.yml": _yaml_bytes(envelope),
            "generated/snowflake-semantic-view.candidate.yml": _yaml_bytes(envelope),
            "generated/snowflake-verification.sql": _text_bytes(
                "-- MANUAL_REVIEW_REQUIRED: no deterministic Snowflake candidate is available."
            ),
            "generated/powerbi-copy-plan.json": _json_bytes(envelope),
        }

    canonical = load_yaml(FIXTURE_CANONICAL)
    index = build_canonical_metric_ir_index(
        canonical, canonical_source="models/semantic/triathlon_semantic.yml"
    )
    powerbi_operations = []
    simple_metrics = []
    top_metrics = []
    for name, metric in sorted(index.items()):
        dax = generate_dax_definition(metric, index)
        snowflake = generate_snowflake_definition(metric, index)
        if (
            dax.support is not SupportClassification.SUPPORTED_PATTERN
            or snowflake.support is not SupportClassification.SUPPORTED_PATTERN
            or dax.definition is None
            or snowflake.definition is None
        ):
            raise DemoError("DEMO_CANDIDATE_UNSUPPORTED", f"Registered fixture metric is not supported: {name}")
        powerbi_operations.append(
            {
                "canonical_metric": name,
                "table": metric.power_bi.table,
                "measure": metric.power_bi.measure,
                "candidate_dax": dax.definition,
                "action": "PLAN_ONLY",
            }
        )
        target = dict(snowflake.definition)
        if name == "valid_rate":
            top_metrics.append(target)
        else:
            simple_metrics.append(target)
    snowflake_view = {
        "name": "semantic_traps_candidate",
        "description": "Governed benchmark candidate; not deployed.",
        "tables": [
            {
                "name": "traps",
                "base_table": {"database": "BENCHMARK", "schema": "PUBLIC", "table": "FACT_TRAPS"},
                "dimensions": [
                    {"name": "row_id", "expr": "row_id", "data_type": "NUMBER"},
                    {"name": "dimension_id", "expr": "dimension_id", "data_type": "NUMBER"},
                    {"name": "is_valid", "expr": "is_valid", "data_type": "BOOLEAN"},
                ],
                "facts": [
                    {"name": "duration_seconds", "expr": "duration_seconds", "data_type": "NUMBER"},
                    {"name": "numeric_id", "expr": "numeric_id", "data_type": "NUMBER"},
                ],
                "metrics": simple_metrics,
            },
            {
                "name": "dimensions",
                "base_table": {"database": "BENCHMARK", "schema": "PUBLIC", "table": "DIM_TRAP"},
                "dimensions": [
                    {"name": "dimension_id", "expr": "dimension_id", "data_type": "NUMBER"}
                ],
            },
        ],
        "relationships": [
            {
                "name": "active_dimension",
                "left_table": "traps",
                "right_table": "dimensions",
                "relationship_columns": [
                    {"left_column": "dimension_id", "right_column": "dimension_id"}
                ],
            }
        ],
        "metrics": top_metrics,
    }
    snowflake_bytes = _yaml_bytes(snowflake_view)
    verification = (
        "-- Review-only Snowflake verification candidate. This file is never executed by the demo.\n"
        "-- TRUE requests verification without deployment.\n"
        "CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('BENCHMARK.SEMANTIC', $$\n"
        + snowflake_bytes.decode("utf-8").rstrip()
        + "\n$$, TRUE);\n"
    )
    canonical_bytes = (
        b"# BENCHMARK REFERENCE CANDIDATE ONLY. NOT REPOSITORY AUTHORITY.\n"
        b"# Canonical source remains models/semantic/triathlon_semantic.yml.\n"
        + FIXTURE_CANONICAL.read_bytes()
    )
    return {
        "generated/canonical-contract.candidate.yml": canonical_bytes,
        "generated/snowflake-semantic-view.candidate.yml": snowflake_bytes,
        "generated/snowflake-verification.sql": verification.encode("utf-8"),
        "generated/powerbi-copy-plan.json": _json_bytes(
            {
                "schema_version": 1,
                "status": "PLAN_ONLY",
                "source_power_bi_modified": False,
                "operations": powerbi_operations,
            }
        ),
    }


def _maintenance_artifacts(proof: Mapping[str, Any]) -> dict[str, bytes]:
    synchronized = "\n".join(
        [
            "# Synchronized semantic maintenance example",
            "",
            f"- Change ID: `{proof['change_id']}`",
            f"- Lifecycle: `{' -> '.join(proof['states'])}`",
            f"- Common IR equality: `{str(proof['targets_share_changed_signature']).upper()}`",
            f"- Unrelated objects unchanged: `{str(proof['unrelated_objects_unchanged']).upper()}`",
            f"- Equivalence checks: `{str(proof['validation_passed']).upper()}`",
            "- Power BI target: copied definition only",
            "- Snowflake target: deterministic candidate only",
            "- Long-term value: the common contract prevents cross-platform drift after migration.",
            "",
        ]
    )
    rollback = "\n".join(
        [
            "# Rollback proof",
            "",
            f"- Hash guarded: `{str(proof['rollback_hash_guarded']).upper()}`",
            f"- Previous targets restored: `{str(proof['rollback_restored_previous_targets']).upper()}`",
            f"- Source Power BI mutated: `{str(proof['source_power_bi_mutated']).upper()}`",
            f"- Deployment: `{proof['deployment_state']}`",
            "",
        ]
    )
    return {
        "maintenance/synchronized-change-example.md": synchronized.encode("utf-8"),
        "maintenance/rollback-proof.md": rollback.encode("utf-8"),
    }


def _review_template(result: Mapping[str, Any]) -> bytes:
    required = [
        item
        for item in result["findings"]
        if item["severity"] == "BLOCKING" or item["category"] == "EXACT_MATCH"
    ]
    return _yaml_bytes(
        {
            "schema_version": 1,
            "fixture_id": result["fixture_id"],
            "source_snapshot_sha256": result["source_snapshot_sha256"],
            "decisions": [
                {
                    "finding_id": item["finding_id"],
                    "action": None,
                    "canonical_metric": item.get("canonical_metric"),
                    "rationale": None,
                }
                for item in required
            ],
        }
    )


def _source_snapshot(
    definition: Path,
    project_path: Path,
    canonical_path: Path,
    snowflake_path: Path | None,
    *,
    registered_fixture: bool,
) -> dict[str, str]:
    values = {
        _relative(project_path): _source_file_hash(project_path) if project_path.is_file() else _source_tree_hash(project_path),
        _relative(definition): _source_tree_hash(definition),
        _relative(DBT_SEMANTIC_YAML): _source_file_hash(DBT_SEMANTIC_YAML),
        _relative(LEGACY_CONTRACT): _source_file_hash(LEGACY_CONTRACT),
        _relative(canonical_path): _source_file_hash(canonical_path),
    }
    if snowflake_path is not None:
        values[_relative(snowflake_path)] = _source_file_hash(snowflake_path)
    if registered_fixture:
        values[_relative(FIXTURE_DATASET)] = _source_file_hash(FIXTURE_DATASET)
    return dict(sorted(values.items()))


def _build_artifacts(
    *,
    result: Mapping[str, Any],
    registered_fixture: bool,
    project_path: Path,
    definition: Path,
    snowflake_path: Path | None,
    canonical_path: Path,
    benchmark: Mapping[str, Any],
    check_status: str,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    counts = _counts(result)
    assurance = _assurance(result, counts)
    status = _status(counts)
    blockers = [item for item in result["findings"] if item["severity"] == "BLOCKING"]
    lint = [item for item in result["findings"] if str(item["rule_id"]).startswith("LINT_")]
    inventory = PowerBIModelInventory.from_dict(result["inventory"])
    support_records = list(result["inventory"].get("object_support_records", []))
    source_before = _source_snapshot(
        definition,
        project_path,
        canonical_path,
        snowflake_path,
        registered_fixture=registered_fixture,
    )
    candidate_artifacts = _candidate_outputs(registered_fixture)
    generated_hashes = _artifact_hashes(candidate_artifacts)
    artifacts: dict[str, bytes] = {
        "executive-summary.md": _text_bytes(
            _executive_summary(counts, assurance, status, blockers)
        ),
        "model-inventory.json": _json_bytes(result["inventory"]),
        "model-inventory.md": _text_bytes(render_inventory_markdown(inventory)),
        "support-matrix.json": _json_bytes(
            {
                "schema_version": 1,
                "counts": dict(Counter(item["classification"] for item in support_records)),
                "records": support_records,
            }
        ),
        "support-matrix.md": _text_bytes(_support_matrix_md(support_records)),
        "conversion-comparison.json": _json_bytes(
            {
                "schema_version": 1,
                "snowflake_evidence_status": result["autopilot_status"],
                "findings": result["findings"],
                "equivalence_results": result["golden_results"],
            }
        ),
        "conversion-comparison.md": _text_bytes(_comparison_md(result)),
        "semantic-lint-report.json": _json_bytes({"schema_version": 1, "findings": lint}),
        "semantic-lint-report.md": _text_bytes(_lint_md(lint)),
        "review-decisions.template.yml": _review_template(result),
        "canonicalization-proposals.json": _json_bytes(
            {"schema_version": 1, "authority": "POWER_BI_EVIDENCE_ONLY", "proposals": result["classifications"]}
        ),
        "canonicalization-proposals.md": _text_bytes(_proposals_md(result["classifications"])),
        "evidence/equivalence-results.json": _json_bytes(
            {
                "schema_version": 1,
                "status": assurance["semantic_equivalence"],
                "results": result["golden_results"],
            }
        ),
        "evidence/equivalence-report.md": _text_bytes(
            "# Semantic equivalence evidence\n\n"
            f"- Status: `{assurance['semantic_equivalence']}`\n"
            f"- Passed: `{counts['equivalence_tests_passed']}`\n"
            f"- Failed: `{counts['equivalence_tests_failed']}`\n\n"
            "Syntactic validity is not used as semantic-equivalence evidence.\n"
        ),
        "evidence/source-hashes.json": _json_bytes(
            {"schema_version": 1, "before": source_before, "after": source_before, "status": "UNCHANGED"}
        ),
        "evidence/generated-hashes.json": _json_bytes(
            {"schema_version": 1, "hashes": generated_hashes}
        ),
        "evidence/immutability-report.md": _text_bytes(
            "# Source immutability\n\n"
            "- Source Power BI modified: `NO`\n"
            "- Repository canonical modified: `NO`\n"
            "- Deprecated contract modified: `NO`\n"
            "- Deployment performed: `NO`\n"
            "- Status: `PASSED`\n"
        ),
        **candidate_artifacts,
        **_maintenance_artifacts(benchmark["maintenance_equivalence"]),
    }
    output_hashes = _artifact_hashes(artifacts)
    source_after = _source_snapshot(
        definition,
        project_path,
        canonical_path,
        snowflake_path,
        registered_fixture=registered_fixture,
    )
    if source_before != source_after:
        artifact = next(
            name
            for name in sorted(set(source_before) | set(source_after))
            if source_before.get(name) != source_after.get(name)
        )
        raise DemoError(
            "DEMO_SOURCE_CHANGED",
            "A protected source changed during demo generation.",
            stage="verify_source_immutability",
            artifact=artifact,
            expected=source_before.get(artifact),
            actual=source_after.get(artifact),
        )
    manifest = {
        "schema_version": DEMO_SCHEMA_VERSION,
        "managed_by": DEMO_MANAGED_BY,
        "tool_version": _tool_version(),
        "fixture": FIXTURE_NAME if registered_fixture else None,
        "source_identifier": f"fixture:{FIXTURE_NAME}" if registered_fixture else _relative(project_path),
        "inputs": {
            "project": _relative(project_path),
            "power_bi_definition": _relative(definition),
            "snowflake_yaml": _relative(snowflake_path) if snowflake_path else None,
            "canonical_evidence": _relative(canonical_path),
        },
        "source_hashes": source_before,
        "output_hashes": output_hashes,
        "benchmark_status": benchmark["status"],
        "counts": counts,
        "assurance": assurance,
        "overall_status": status,
        "deployment_status": "NOT_REQUESTED",
        "source_immutability_status": "PASSED",
        "check_status": check_status,
        "reproducibility_command": "python semantic_poc/run_demo.py --clean --check",
    }
    artifacts["manifest.json"] = _json_bytes(manifest)
    summary = {
        "fixture": manifest["fixture"],
        "counts": counts,
        "assurance": assurance,
        "overall_status": status,
        "benchmark_status": benchmark["status"],
        "deployment_status": "NOT_REQUESTED",
        "source_immutability_status": "PASSED",
    }
    return artifacts, summary


def _write_once(
    *,
    project_path: Path,
    snowflake_path: Path | None,
    output: Path,
    registered_fixture: bool,
    check_status: str,
) -> dict[str, Any]:
    definition = resolve_powerbi_model_dir(project_path, PROJECT_ROOT)
    canonical_path = FIXTURE_CANONICAL if registered_fixture else DBT_SEMANTIC_YAML
    benchmark = build_benchmark()
    result = (
        _fixture_result(project_path, snowflake_path)
        if registered_fixture
        else _project_result(project_path, snowflake_path)
    )
    protected = [definition, DBT_SEMANTIC_YAML, LEGACY_CONTRACT, canonical_path]
    if snowflake_path:
        protected.append(snowflake_path)
    output = _safe_output(output, protected=protected)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        artifacts, summary = _build_artifacts(
            result=result,
            registered_fixture=registered_fixture,
            project_path=project_path,
            definition=definition,
            snowflake_path=snowflake_path,
            canonical_path=canonical_path,
            benchmark=benchmark,
            check_status=check_status,
        )
        _write_artifacts(staging, artifacts)
        expected_hashes = _artifact_hashes(artifacts)
        actual_hashes = bundle_hashes(staging)
        if expected_hashes != actual_hashes:
            artifact = next(
                name
                for name in sorted(set(expected_hashes) | set(actual_hashes))
                if expected_hashes.get(name) != actual_hashes.get(name)
            )
            raise DemoError(
                "DEMO_ARTIFACT_HASH_MISMATCH",
                "Staged artifact hashes do not match written bytes.",
                stage="write_staged_bundle",
                artifact=artifact,
                expected=expected_hashes.get(artifact),
                actual=actual_hashes.get(artifact),
            )
        staging.replace(output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {
        "schema_version": 1,
        "output_dir": output.relative_to(PROJECT_ROOT).as_posix(),
        "semantic_status": summary["overall_status"],
        "summary": summary,
        "findings": result["findings"],
        "artifact_hashes": bundle_hashes(output),
        "deployment_status": "NOT_REQUESTED",
        "source_power_bi_modified": False,
    }


def _load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DemoError(
            "DEMO_EXPECTATION_INVALID",
            f"{label} is missing or invalid: {_relative_display(path)}",
            artifact=_relative_display(path),
            expected="valid UTF-8 JSON",
            actual=type(exc).__name__,
        ) from exc


def _diagnostic_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return rendered if len(rendered) <= 500 else rendered[:497] + "..."


def render_demo_error(error: DemoError) -> str:
    lines = [
        f"ERROR_CODE: {error.code}",
        f"STAGE: {error.stage}",
        f"ARTIFACT: {error.artifact}",
        f"EXPECTED: {_diagnostic_value(error.expected)}",
        f"ACTUAL: {_diagnostic_value(error.actual)}",
        f"DIAGNOSTICS: {error.diagnostics or 'NOT_RETAINED'}",
        f"REMEDIATION: {error.remediation}",
    ]
    if error.retained_bundle:
        lines.append(f"RETAINED_BUNDLE: {error.retained_bundle}")
    return "\n".join(lines)


def persist_demo_failure(
    error: DemoError,
    output_dir: str | Path,
    *,
    failure_dir: Path | None = None,
    retained_bundle: Path | None = None,
) -> Path:
    if error.diagnostics:
        return PROJECT_ROOT / error.diagnostics
    if failure_dir is None:
        failure_root = PROJECT_ROOT / ".tmp" / "demo-failures"
        failure_root.mkdir(parents=True, exist_ok=True)
        output_name = Path(output_dir).name or "demo-output"
        safe_name = "".join(character if character.isalnum() or character in "-_" else "-" for character in output_name)
        failure_dir = Path(tempfile.mkdtemp(prefix=f"{safe_name[:40]}-", dir=failure_root))
    diagnostic_path = (
        retained_bundle / "failure-diagnostics.json"
        if retained_bundle is not None
        else failure_dir / "failure-diagnostics.json"
    )
    if retained_bundle is not None:
        error.retained_bundle = _relative(retained_bundle)
    error.diagnostics = _relative(diagnostic_path.parent) + "/" + diagnostic_path.name
    diagnostic_path.write_bytes(
        _json_bytes(
            {
                "schema_version": 1,
                "verdict": "POC_DEMO_NOT_ACCEPTED",
                "requested_output": _relative_display(output_dir),
                "error": error.to_dict(),
            }
        )
    )
    if retained_bundle is not None:
        manifest_path = retained_bundle / "manifest.json"
        manifest = _load_json(manifest_path, label="Retained demo manifest")
        manifest["check_status"] = CHECK_FAILED
        manifest["failure_code"] = error.code
        manifest["output_hashes"]["failure-diagnostics.json"] = _file_hash(diagnostic_path)
        manifest_path.write_bytes(_json_bytes(manifest))
    return diagnostic_path


def _retain_staged_failure(
    error: DemoError,
    *,
    output_dir: str | Path,
    staged: Path,
) -> None:
    failure_root = PROJECT_ROOT / ".tmp" / "demo-failures"
    failure_root.mkdir(parents=True, exist_ok=True)
    output_name = Path(output_dir).name or "demo-output"
    safe_name = "".join(character if character.isalnum() or character in "-_" else "-" for character in output_name)
    failure_dir = Path(tempfile.mkdtemp(prefix=f"{safe_name[:40]}-", dir=failure_root))
    retained_bundle: Path | None = None
    if staged.is_dir():
        retained_bundle = failure_dir / "bundle"
        staged.replace(retained_bundle)
    persist_demo_failure(
        error,
        output_dir,
        failure_dir=failure_dir,
        retained_bundle=retained_bundle,
    )


def _finding_projection(findings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "finding_id": item["finding_id"],
            "rule_id": item["rule_id"],
            "severity": item["severity"],
            "category": item["category"],
            "source_object": item["source_object"],
            "generated_object": item.get("generated_object"),
            "canonical_metric": item.get("canonical_metric"),
        }
        for item in findings
    ]


def _first_mapping_mismatch(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> tuple[str, Any, Any] | None:
    for name in sorted(set(expected) | set(actual)):
        if expected.get(name) != actual.get(name):
            return name, expected.get(name), actual.get(name)
    return None


def create_demo_bundle(
    *,
    project: str | Path | None = None,
    fixture: str | None = None,
    snowflake_yaml: str | Path | None = None,
    output_dir: str | Path,
    check: bool = False,
) -> dict[str, Any]:
    if bool(project) == bool(fixture):
        raise DemoError("DEMO_INPUT_REQUIRED", "Select exactly one of --project or --fixture.")
    registered_fixture = fixture is not None
    if fixture is not None and fixture != FIXTURE_NAME:
        raise DemoError("DEMO_FIXTURE_UNKNOWN", f"Unknown fixture: {fixture}")
    project_path = _safe_input(FIXTURE_PBIP if registered_fixture else project, label="Power BI project")
    default_snowflake = FIXTURE_SNOWFLAKE if registered_fixture else None
    snowflake_path = _safe_input(
        snowflake_yaml or default_snowflake, label="Snowflake YAML", file=True
    ) if (snowflake_yaml or default_snowflake) else None
    output = _argument_path(output_dir)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    definition = resolve_powerbi_model_dir(project_path, PROJECT_ROOT)
    canonical_path = FIXTURE_CANONICAL if registered_fixture else DBT_SEMANTIC_YAML
    protected = [definition, DBT_SEMANTIC_YAML, LEGACY_CONTRACT, canonical_path]
    if snowflake_path:
        protected.append(snowflake_path)
    output = _safe_output(output, protected=protected)
    run_root = PROJECT_ROOT / ".tmp" / "demo-runs"
    run_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="semantic-m8-run-", dir=run_root))
    staged = temporary / "bundle"
    try:
        initial_status = CHECK_PENDING if check else CHECK_NOT_CHECKED
        result = _write_once(
            project_path=project_path,
            snowflake_path=snowflake_path,
            output=staged,
            registered_fixture=registered_fixture,
            check_status=initial_status,
        )
        if check:
            repeat = _write_once(
                project_path=project_path,
                snowflake_path=snowflake_path,
                output=temporary / "repeat",
                registered_fixture=registered_fixture,
                check_status=initial_status,
            )
            if result["artifact_hashes"] != repeat["artifact_hashes"]:
                mismatch = _first_mapping_mismatch(
                    result["artifact_hashes"], repeat["artifact_hashes"]
                )
                assert mismatch is not None
                artifact, expected, actual = mismatch
                raise DemoError(
                    "DEMO_NOT_DETERMINISTIC",
                    "Repeated demo artifact hashes differ.",
                    stage="check_repeatability",
                    artifact=artifact,
                    expected=expected,
                    actual=actual,
                )
            if registered_fixture:
                manifest_path = staged / "manifest.json"
                manifest = _load_json(manifest_path, label="Staged demo manifest")
                manifest["check_status"] = CHECK_ACCEPTED
                manifest_path.write_bytes(_json_bytes(manifest))
                result["artifact_hashes"] = bundle_hashes(staged)
                expected_summary = _load_json(EXPECTED_SUMMARY, label="Expected summary")
                expected_findings = _load_json(EXPECTED_FINDINGS, label="Expected findings")
                if not isinstance(expected_summary, Mapping):
                    raise DemoError(
                        "DEMO_EXPECTATION_INVALID",
                        "Expected summary must contain a JSON object.",
                        stage="check_expected_bundle",
                        artifact=_relative(EXPECTED_SUMMARY),
                        expected="JSON object",
                        actual=type(expected_summary).__name__,
                    )
                expected_values = {
                    name: value
                    for name, value in expected_summary.items()
                    if name != "artifact_hashes"
                }
                value_mismatch = _first_mapping_mismatch(
                    expected_values, result["summary"]
                )
                if value_mismatch is not None:
                    field, expected, actual = value_mismatch
                    raise DemoError(
                        "DEMO_EXPECTATION_VALUE_MISMATCH",
                        "Demo summary values differ from committed expectations.",
                        stage="check_expected_summary",
                        artifact=f"{_relative(EXPECTED_SUMMARY)}#{field}",
                        expected=expected,
                        actual=actual,
                    )
                expected_hashes = expected_summary.get("artifact_hashes")
                if not isinstance(expected_hashes, Mapping):
                    raise DemoError(
                        "DEMO_EXPECTATION_INVALID",
                        "Expected summary artifact_hashes must contain a JSON object.",
                        stage="check_expected_bundle",
                        artifact=f"{_relative(EXPECTED_SUMMARY)}#artifact_hashes",
                        expected="JSON object",
                        actual=type(expected_hashes).__name__,
                    )
                hash_mismatch = _first_mapping_mismatch(
                    expected_hashes, result["artifact_hashes"]
                )
                if hash_mismatch is not None:
                    artifact, expected, actual = hash_mismatch
                    raise DemoError(
                        "DEMO_EXPECTATION_HASH_MISMATCH",
                        "Demo artifact hash differs from the committed expectation.",
                        stage="check_expected_bundle",
                        artifact=artifact,
                        expected=expected,
                        actual=actual,
                    )
                finding_projection = _finding_projection(result["findings"])
                expected_projection = (
                    expected_findings.get("findings")
                    if isinstance(expected_findings, Mapping)
                    else None
                )
                if finding_projection != expected_projection:
                    raise DemoError(
                        "DEMO_FINDINGS_MISMATCH",
                        "Demo findings differ from committed expectations.",
                        stage="check_expected_findings",
                        artifact=_relative(EXPECTED_FINDINGS),
                        expected=expected_projection,
                        actual=finding_projection,
                    )
                result["verdict"] = "POC_DEMO_ACCEPTED"
            else:
                accepted = result["semantic_status"] != "BLOCKED_PENDING_REVIEW"
                manifest_path = staged / "manifest.json"
                manifest = _load_json(manifest_path, label="Staged demo manifest")
                manifest["check_status"] = CHECK_ACCEPTED if accepted else CHECK_NOT_ACCEPTED
                manifest_path.write_bytes(_json_bytes(manifest))
                result["artifact_hashes"] = bundle_hashes(staged)
                result["verdict"] = "POC_DEMO_ACCEPTED" if accepted else "POC_DEMO_NOT_ACCEPTED"
        staged.replace(output)
        result["output_dir"] = output.relative_to(PROJECT_ROOT).as_posix()
        result["artifact_hashes"] = bundle_hashes(output)
        return result
    except DemoError as error:
        _retain_staged_failure(
            error,
            output_dir=output_dir,
            staged=staged,
        )
        raise
    except OSError as exc:
        error = DemoError(
            "DEMO_OUTPUT_PROMOTION_FAILED",
            f"The staged demo bundle could not be promoted: {exc}",
            stage="promote_output",
            artifact=str(output_dir),
            expected="atomic promotion succeeds",
            actual=type(exc).__name__,
        )
        _retain_staged_failure(
            error,
            output_dir=output_dir,
            staged=staged,
        )
        raise error from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def clean_demo_output(output_dir: str | Path) -> bool:
    requested = Path(output_dir)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    root = PROJECT_ROOT.resolve(strict=True)
    target = requested.resolve(strict=False)
    if not target.is_relative_to(root) or target == root:
        raise DemoError("DEMO_CLEANUP_UNSAFE", "Cleanup target must be a repository-contained child directory.")
    if not target.exists():
        return False
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        raise DemoError("DEMO_CLEANUP_REFUSED", "Existing output has no managed M8 manifest.")
    manifest = _load_json(manifest_path, label="Managed output manifest")
    if manifest.get("managed_by") != DEMO_MANAGED_BY or manifest.get("schema_version") != DEMO_SCHEMA_VERSION:
        raise DemoError("DEMO_CLEANUP_REFUSED", "Existing output is not a managed M8 bundle.")
    expected = {"manifest.json", *manifest.get("output_hashes", {}).keys()}
    actual = {
        item.relative_to(target).as_posix()
        for item in target.rglob("*")
        if item.is_file()
    }
    if actual != expected:
        raise DemoError("DEMO_CLEANUP_REFUSED", "Managed output contains unexpected or missing files.")
    for name, expected_hash in manifest["output_hashes"].items():
        if _file_hash(target / name) != expected_hash:
            raise DemoError("DEMO_CLEANUP_REFUSED", f"Managed artifact changed: {name}")
    shutil.rmtree(target)
    return True


def _validate_demo_bundle(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = path / "manifest.json"
    manifest = _load_json(manifest_path, label="Demo manifest")
    if manifest.get("managed_by") != DEMO_MANAGED_BY or manifest.get("fixture") != FIXTURE_NAME:
        raise DemoError("DEMO_FINALIZE_INPUT_INVALID", "Finalization requires a registered semantic-trap demo bundle.")
    if manifest.get("check_status") != CHECK_ACCEPTED:
        raise DemoError(
            "DEMO_FINALIZE_INPUT_NOT_ACCEPTED",
            "Finalization requires a successfully checked and accepted demo bundle.",
            artifact="manifest.json#check_status",
            expected=CHECK_ACCEPTED,
            actual=manifest.get("check_status"),
        )
    for name, expected in manifest.get("output_hashes", {}).items():
        artifact = path / name
        if not artifact.is_file() or _file_hash(artifact) != expected:
            raise DemoError("DEMO_FINALIZE_HASH_MISMATCH", f"Demo artifact is stale or missing: {name}")
    comparison = _load_json(path / "conversion-comparison.json", label="Conversion comparison")
    return manifest, comparison


def _validate_decisions(
    value: Any,
    *,
    manifest: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "fixture_id", "source_snapshot_sha256", "decisions"
    }:
        raise DemoError("DEMO_DECISIONS_INVALID", "Decision file must match the strict schema-v1 envelope.")
    expected_source = manifest["source_hashes"][_relative(FIXTURE_MODEL / "definition")]
    if (
        value.get("schema_version") != 1
        or value.get("fixture_id") != "B_SEMANTIC_TRAPS"
        or value.get("source_snapshot_sha256") != expected_source
    ):
        raise DemoError("DEMO_DECISIONS_STALE", "Decision file is not bound to the current fixture source hash.")
    decisions = value.get("decisions")
    if not isinstance(decisions, list):
        raise DemoError("DEMO_DECISIONS_INVALID", "decisions must be an array.")
    required = {
        item["finding_id"]: item
        for item in findings
        if item["severity"] == "BLOCKING" or item["category"] == "EXACT_MATCH"
    }
    by_id: dict[str, Mapping[str, Any]] = {}
    index = build_canonical_metric_ir_index(
        load_yaml(FIXTURE_CANONICAL), canonical_source="models/semantic/triathlon_semantic.yml"
    )
    for decision in decisions:
        if not isinstance(decision, Mapping) or set(decision) != {
            "finding_id", "action", "canonical_metric", "rationale"
        }:
            raise DemoError("DEMO_DECISIONS_INVALID", "Each decision must use the strict four-field shape.")
        finding_id = str(decision["finding_id"])
        if finding_id in by_id or finding_id not in required:
            raise DemoError("DEMO_DECISIONS_INVALID", f"Duplicate or unexpected decision: {finding_id}")
        finding = required[finding_id]
        action = decision["action"]
        rationale = decision["rationale"]
        if not isinstance(rationale, str) or not rationale.strip():
            raise DemoError("DEMO_DECISIONS_INVALID", f"Decision rationale is required: {finding_id}")
        if finding["category"] == "EXACT_MATCH":
            if action != "ACCEPT" or decision["canonical_metric"] != finding["canonical_metric"]:
                raise DemoError("DEMO_DECISIONS_INVALID", f"Exact mapping must be explicitly accepted: {finding_id}")
        elif action == "CORRECT":
            canonical_metric = decision["canonical_metric"]
            if canonical_metric not in index:
                raise DemoError("DEMO_DECISIONS_INVALID", f"Correction metric is not in the reviewed candidate: {finding_id}")
        elif action == "REJECT":
            if decision["canonical_metric"] is not None:
                raise DemoError("DEMO_DECISIONS_INVALID", f"Rejected findings cannot name a canonical metric: {finding_id}")
        else:
            raise DemoError("DEMO_DECISIONS_INVALID", f"Blocking finding requires CORRECT or REJECT: {finding_id}")
        by_id[finding_id] = decision
    missing = sorted(set(required) - set(by_id))
    if missing:
        raise DemoError("DEMO_DECISIONS_INCOMPLETE", "Blocking or exact findings lack decisions: " + ", ".join(missing))
    return [by_id[name] for name in sorted(by_id)]


def finalize_demo_bundle(
    *,
    demo_run: str | Path,
    decisions: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    demo_path = _safe_input(demo_run, label="Demo run", file=False)
    decision_path = _safe_input(decisions, label="Review decisions", file=True)
    manifest, comparison = _validate_demo_bundle(demo_path)
    try:
        decision_value = yaml.safe_load(decision_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DemoError("DEMO_DECISIONS_INVALID", f"Decision YAML is invalid: {exc}") from exc
    validated = _validate_decisions(
        decision_value,
        manifest=manifest,
        findings=comparison["findings"],
    )
    for path, expected in manifest["source_hashes"].items():
        source = PROJECT_ROOT / path
        actual = _source_tree_hash(source) if source.is_dir() else _source_file_hash(source)
        if actual != expected:
            raise DemoError("DEMO_FINALIZE_SOURCE_CHANGED", f"Protected source changed after review: {path}")
    candidate = _candidate_outputs(True)
    equivalence = run_candidate_golden_tests(FIXTURE_CANONICAL, FIXTURE_DATASET)
    if len(equivalence) != 5 or any(item["status"] != "PASS" for item in equivalence):
        raise DemoError("DEMO_FINALIZE_EQUIVALENCE_FAILED", "Governed candidate equivalence did not pass 5/5 metrics.")
    artifacts = {
        **candidate,
        "review-decisions.json": _json_bytes(
            {
                "schema_version": 1,
                "source_snapshot_sha256": decision_value["source_snapshot_sha256"],
                "decisions": validated,
            }
        ),
        "evidence/equivalence-results.json": _json_bytes(
            {"schema_version": 1, "status": "PASSED", "passed": 5, "total": 5, "results": equivalence}
        ),
        "evidence/equivalence-report.md": _text_bytes(
            "# Governed candidate equivalence\n\n- Status: `PASSED`\n- Metrics passed: `5/5`\n\n"
            "The candidate still requires final human approval before any real application.\n"
        ),
        "evidence/source-hashes.json": (demo_path / "evidence" / "source-hashes.json").read_bytes(),
        "executive-summary.md": _text_bytes(
            "# Reviewed finalization candidate\n\n"
            "- Overall status: `READY_FOR_GOVERNED_FINALIZATION`\n"
            "- Blocking findings with explicit decisions: `16/16`\n"
            "- Exact mappings explicitly accepted: `3/3`\n"
            "- Candidate equivalence: `5/5 PASSED`\n"
            "- Source Power BI modified: `NO`\n"
            "- Repository canonical modified: `NO`\n"
            "- Deployment performed: `NO`\n"
            "- Final human approval before real application: `REQUIRED`\n"
        ),
    }
    final_manifest = {
        "schema_version": 1,
        "managed_by": DEMO_MANAGED_BY,
        "tool_version": _tool_version(),
        "source_demo_manifest_sha256": _file_hash(demo_path / "manifest.json"),
        "source_snapshot_sha256": decision_value["source_snapshot_sha256"],
        "output_hashes": _artifact_hashes(artifacts),
        "overall_status": "READY_FOR_GOVERNED_FINALIZATION",
        "human_approval": "REQUIRED",
        "source_power_bi_modified": False,
        "canonical_modified": False,
        "deployment_status": "NOT_REQUESTED",
    }
    artifacts["manifest.json"] = _json_bytes(final_manifest)
    output = _safe_output(
        output_dir,
        protected=[demo_path, decision_path, FIXTURE_MODEL / "definition", DBT_SEMANTIC_YAML, LEGACY_CONTRACT],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        _write_artifacts(staging, artifacts)
        staging.replace(output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {
        "schema_version": 1,
        "output_dir": output.relative_to(PROJECT_ROOT).as_posix(),
        "status": "READY_FOR_GOVERNED_FINALIZATION",
        "equivalence_passed": 5,
        "equivalence_total": 5,
        "deployment_status": "NOT_REQUESTED",
        "source_power_bi_modified": False,
        "canonical_modified": False,
    }


__all__ = [
    "DEMO_MANAGED_BY",
    "DEMO_SCHEMA_VERSION",
    "DemoError",
    "FIXTURE_NAME",
    "bundle_hashes",
    "clean_demo_output",
    "create_demo_bundle",
    "finalize_demo_bundle",
    "persist_demo_failure",
    "render_demo_error",
]
