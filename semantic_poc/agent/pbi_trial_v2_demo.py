from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .pbi_trial_v2_fixture import FixtureError, validate_committed_fixture
from .powerbi_snowflake_audit import AuditStateError, PowerBISnowflakeAudit


VERDICT = "CONVERSION_GAP_DEMO_ACCEPTED"
CLAIM_SCOPE = "CAPTURED_PBI_TRIAL_V2_ONLY"
CONFIRMED = {
    "NC - Split-Multiplied Bike Seconds",
    "Nominal Bike KM Across Timed Results",
    "Split Coverage Rate",
    "Weighted Bike Speed KM/H",
}
POTENTIAL = {
    "# Events",
    "Results With Splits",
    "Total Recorded Seconds",
    "Total Transition Seconds",
}
PRESENTER_RELATIVE_PATH = (
    "docs/codex/pbi-snowflake-poc/demo/CONVERSION_GAP_PRESENTER.md"
)


def _finding(audit: PowerBISnowflakeAudit, name: str):
    try:
        return next(item for item in audit.measures if item.source["name"] == name)
    except StopIteration as exc:
        raise AuditStateError(f"Required presenter example is missing: {name}") from exc


def _primary_finding_id(audit: PowerBISnowflakeAudit, name: str) -> str:
    finding = _finding(audit, name)
    if not finding.finding_ids:
        raise AuditStateError(f"Required presenter example has no finding ID: {name}")
    return finding.finding_ids[0]


def validate_demo_assertions(audit: PowerBISnowflakeAudit) -> None:
    summary = audit.summary
    expected_scalars = {
        "matched_measure_count": 21,
        "omitted_measure_count": 25,
        "proven_caught_count": 0,
        "source_measure_count": 46,
        "target_metric_count": 21,
    }
    for key, expected in expected_scalars.items():
        if summary.get(key) != expected:
            raise AuditStateError(
                f"Task 01 demo expectation changed: {key}={summary.get(key)!r}; expected {expected}."
            )
    confirmed = {
        item.source["name"]
        for item in audit.measures
        if item.fidelity_status == "CONFIRMED_INCORRECT"
    }
    potential = {
        item.source["name"]
        for item in audit.measures
        if item.fidelity_status == "POTENTIALLY_INCORRECT"
    }
    if confirmed != CONFIRMED or potential != POTENTIAL:
        raise AuditStateError("Task 01 confirmed or potential mismatch sets changed.")
    detection = summary.get("negative_control_detection_counts")
    if detection != {"NOT_PROVEN": 2, "PROVEN_NOT_CAUGHT": 4}:
        raise AuditStateError("Task 01 negative-control detection counts changed.")
    relationships = audit.relationship_comparison
    if (
        relationships.get("endpoint_match_count") != 7
        or relationships.get("source_relationship_count") != 7
        or relationships.get("target_relationship_count") != 7
    ):
        raise AuditStateError("Task 01 relationship endpoint evidence changed.")
    reviewed = audit.snowflake_inventory["reviewed_profile_structure"]
    if reviewed.get("status") != "MATCH" or summary.get("model_blocker_count") != 0:
        raise AuditStateError("Task 01 reviewed model-structure evidence changed.")
    if audit.runtime_evidence.get("status") != "NOT_AVAILABLE":
        raise AuditStateError("Task 01 runtime evidence is no longer NOT_AVAILABLE.")


def render_presenter(audit: PowerBISnowflakeAudit) -> str:
    result_rows_id = _primary_finding_id(audit, "Result Rows")
    coverage_id = _primary_finding_id(audit, "Split Coverage Rate")
    divisor_id = _primary_finding_id(audit, "NC - Bike Time Hours Divisor 60")
    lines = [
        "# Captured Power BI-to-Snowflake conversion gap",
        "",
        "## 1. Reproduce the evidence",
        "",
        "[PROVEN] This walkthrough is scoped only to the captured `pbi_trial` v2 inputs and shows that this captured output is unsafe to accept blindly.",
        "",
        "```powershell",
        "python semantic_poc/run_pbi_trial_v2_audit.py --check --json",
        "```",
        "",
        "## 2. Coverage",
        "",
        "[OBSERVED] The source inventory contains 46 Power BI measures; the captured Snowflake YAML emits 21 mapped metrics and omits 25 measures.",
        "",
        "## 3. Three representative findings",
        "",
        f"[OBSERVED] `Result Rows` is a trivial omission with no emitted target metric (finding `{result_rows_id}`).",
        "",
        f"[PROVEN] `Split Coverage Rate` changes denominator and grain by dividing distinct split result IDs by split rows instead of using result-grain `Result Rows` (finding `{coverage_id}`).",
        "",
        f"[PROVEN] `NC - Bike Time Hours Divisor 60` is an intentional unit-conversion defect that remained active with only non-blocking caution (finding `{divisor_id}`).",
        "",
        "## 4. What the static evidence proves",
        "",
        "[PROVEN] Four measures are confirmed mistranslations: `Split Coverage Rate`, `Nominal Bike KM Across Timed Results`, `Weighted Bike Speed KM/H`, and `NC - Split-Multiplied Bike Seconds`.",
        "",
        "[PROVEN] Zero of six intentional negative controls were proven caught; four were proven not caught.",
        "",
        "[NOT_PROVEN] The two silently omitted intentional controls do not establish detection and remain `NOT_PROVEN`.",
        "",
        "## 5. Model structure and runtime boundary",
        "",
        "[OBSERVED] All seven Power BI relationship endpoints matched the captured target representation, and the reviewed model structure has no blocker.",
        "",
        "[NOT_PROVEN] Runtime equivalence and runtime mismatch are both unproven because sanitized comparable result exports were not supplied.",
        "",
        "[NOT_PROVEN] This captured case does not prove that Snowflake conversion always fails.",
        "",
        "## 6. Follow-up POC",
        "",
        "[OBSERVED] The next POC stage is deterministic conversion plus governed review and incremental reconciliation—not an AI model writing executable SQL.",
        "",
    ]
    return "\n".join(lines)


def _validate_schema_files(repository_root: Path) -> None:
    paths = (
        repository_root / "semantic_poc" / "agent" / "result_evidence.schema.json",
        repository_root
        / "semantic_poc"
        / "benchmark"
        / "pbi_trial_v2"
        / "result-evidence.schema.json",
    )
    for path in paths:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator.check_schema(schema)
        except (OSError, UnicodeError, json.JSONDecodeError, jsonschema.SchemaError) as exc:
            raise AuditStateError(f"Result-evidence schema is invalid: {path}") from exc


def write_or_check_presenter(
    audit: PowerBISnowflakeAudit, *, repository_root: Path, check: bool
) -> str:
    path = repository_root / Path(PRESENTER_RELATIVE_PATH)
    expected = render_presenter(audit).encode("utf-8")
    if check:
        if not path.is_file() or path.read_bytes() != expected:
            raise AuditStateError("The conversion-gap presenter is missing or stale.")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(expected)
    return PRESENTER_RELATIVE_PATH


def prepare_demo(
    audit: PowerBISnowflakeAudit, *, repository_root: Path, check: bool
) -> dict[str, Any]:
    fixture_root = (
        repository_root
        / "semantic_poc"
        / "benchmark"
        / "pbi_trial_v2"
        / "fixtures"
    )
    try:
        fixture_manifest = validate_committed_fixture(
            fixture_model=fixture_root / "pbi_trial.SemanticModel",
            manifest_path=fixture_root / "fixture-manifest.json",
            repository_root=repository_root,
        )
    except FixtureError as exc:
        raise AuditStateError(str(exc)) from exc
    validate_demo_assertions(audit)
    _validate_schema_files(repository_root)
    presenter_path = write_or_check_presenter(
        audit, repository_root=repository_root, check=check
    )
    return {
        "claim_scope": CLAIM_SCOPE,
        "confirmed_incorrect": 4,
        "emitted_metrics": 21,
        "input_hashes": {
            "behavioral_baseline_sha256": audit.inputs[
                "behavioral_baseline_sha256"
            ],
            "benchmark_spec_sha256": audit.inputs["benchmark_spec_sha256"],
            "canonical_contract_sha256": audit.inputs[
                "canonical_contract_sha256"
            ],
            "powerbi_model_tree_sha256": audit.inputs[
                "powerbi_source_tree_sha256"
            ],
            "snowflake_query_pack_sha256": audit.inputs[
                "snowflake_query_pack_sha256"
            ],
            "snowflake_yaml_sha256": audit.inputs["snowflake_yaml_sha256"],
        },
        "machine_findings_path": "semantic_poc/benchmark/pbi_trial_v2/conversion-findings.json",
        "matched_metrics": 21,
        "model_structure_status": "MATCH",
        "not_proven": 2,
        "omitted_measures": 25,
        "potentially_incorrect": 4,
        "presenter_path": presenter_path,
        "proven_caught": 0,
        "proven_not_caught": 4,
        "relationship_endpoints_matched": 7,
        "relationship_endpoints_total": 7,
        "runtime_status": "NOT_AVAILABLE",
        "source_measures": 46,
        "target_metrics": 21,
        "verdict": VERDICT,
        "fixture_model_tree_sha256": fixture_manifest[
            "fixture_model_tree_sha256"
        ],
    }
