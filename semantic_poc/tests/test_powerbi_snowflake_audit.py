from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest
import yaml

from semantic_poc.agent.cli import main as semantic_agent_main
from semantic_poc.agent.powerbi_snowflake_audit import (
    AuditInputError,
    AuditStateError,
    AuditStaleEvidenceError,
    audit_artifact_contents,
    audit_powerbi_snowflake,
    normalize_snowflake_identifier,
    render_audit_markdown,
    write_audit_output_directory,
)
from semantic_poc.src.models import PROJECT_ROOT


MODEL = (
    PROJECT_ROOT
    / "semantic_poc"
    / "benchmark"
    / "pbi_trial_v2"
    / "fixtures"
    / "pbi_trial.SemanticModel"
)
SNOWFLAKE = PROJECT_ROOT / "pbit" / "snowflake_semantic_view" / "pbi_trial.yaml"
SPEC = (
    PROJECT_ROOT
    / "semantic_poc"
    / "benchmark"
    / "pbi_trial_v2"
    / "measure-cases.yml"
)
BASELINE = SPEC.parent / "powerbi-baseline.dax"
SLICE_DIMENSION_SETS = {
    "OVERALL": ((),),
    "DISTANCE": (("DIM_DISTANCE.DISTANCE",),),
    "GENDER": (("DIM_GENDER.GENDER",),),
    "EVENT": (
        (
            "DIM_COUNTRY.CONTINENT",
            "DIM_COUNTRY.REGION",
            "DIM_COUNTRY.COUNTRY_NAME",
            "DIM_EVENT.EVENT_ID",
            "DIM_EVENT.EVENT_NAME",
        ),
    ),
    "COUNTRY": (
        (
            "DIM_COUNTRY.CONTINENT",
            "DIM_COUNTRY.REGION",
            "DIM_COUNTRY.COUNTRY_NAME",
        ),
        ("DIM_COUNTRY.CONTINENT", "DIM_COUNTRY.REGION"),
        ("DIM_COUNTRY.CONTINENT",),
        (),
    ),
    "DIVISION": (("DIM_DIVSION.DIVISION", "DIM_DIVSION.IS_PRO"),),
    "AGE_GROUP": (("DIM_AGE_GROUP.AGE_GROUP_NAME",),),
    "LEG": (("FCT_SPLIT.LEG",),),
}


def golden_audit():
    return audit_powerbi_snowflake(
        model_dir=MODEL,
        snowflake_yaml=SNOWFLAKE,
        benchmark_spec=SPEC,
        repository_root=PROJECT_ROOT,
    )


def copied_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "repository"
    root.mkdir(parents=True)
    model = root / "model.SemanticModel"
    shutil.copytree(MODEL, model)
    snowflake = root / "snowflake.yaml"
    shutil.copy2(SNOWFLAKE, snowflake)
    spec = root / "measure-cases.yml"
    shutil.copy2(SPEC, spec)
    shutil.copy2(BASELINE, root / "powerbi-baseline.dax")
    shutil.copy2(SPEC.parent / "snowflake-query-pack.sql", root / "snowflake-query-pack.sql")
    return root, model, snowflake, spec


def write_json(path: Path, value: object) -> None:
    if isinstance(value, dict) and value.get("profile") == "PBI_TRIAL_V2_AUDIT":
        _refresh_evidence_hashes(value)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_golden_audit_covers_all_measures_and_negative_controls() -> None:
    audit = golden_audit()
    summary = audit.summary

    assert audit.audit_id.startswith("audit_")
    assert len(audit.audit_id) == 22
    assert summary["source_measure_count"] == 46
    assert summary["matched_measure_count"] == 21
    assert summary["omitted_measure_count"] == 25
    assert summary["extra_target_metric_count"] == 0
    assert summary["fidelity_status_counts"] == {
        "CONFIRMED_INCORRECT": 4,
        "OMITTED": 25,
        "POTENTIALLY_INCORRECT": 4,
        "STRUCTURALLY_EQUIVALENT": 13,
    }
    assert summary["negative_control_count"] == 6
    assert summary["negative_control_detection_counts"] == {
        "NOT_PROVEN": 2,
        "PROVEN_NOT_CAUGHT": 4,
    }
    assert summary["negative_control_handling_counts"] == {
        "CHANGED": 1,
        "EMITTED_WITH_CAUTION": 3,
        "OMITTED": 2,
    }
    assert summary["proven_caught_count"] == 0
    assert summary["automation_disposition_counts"] == {
        "FLAG_SOURCE_DEFECT": 6,
        "MANUAL_REVIEW_REQUIRED": 40,
    }
    assert audit.powerbi_inventory["counts"] == {
        "tables": 9,
        "columns": 61,
        "measures": 46,
        "relationships": 7,
        "partitions": 9,
        "hierarchies": 0,
        "calculation_groups": 0,
    }
    assert len(audit.snowflake_inventory["tables"]) == 8
    assert audit.snowflake_inventory["metric_count"] == 21
    assert audit.snowflake_inventory["time_dimension_count"] == 1
    assert audit.snowflake_inventory["reviewed_profile_structure"]["status"] == "MATCH"
    assert audit.inputs["powerbi_structure_sha256"]
    assert audit.inputs["snowflake_structure_sha256"]
    assert all(
        item["source_location"]
        for table in audit.snowflake_inventory["tables"]
        for role in ("dimensions", "time_dimensions", "facts")
        for item in table[role]
    )

    confirmed = {
        item.source["name"]
        for item in audit.measures
        if item.fidelity_status == "CONFIRMED_INCORRECT"
    }
    assert confirmed == {
        "Split Coverage Rate",
        "Nominal Bike KM Across Timed Results",
        "Weighted Bike Speed KM/H",
        "NC - Split-Multiplied Bike Seconds",
    }
    potential = {
        item.source["name"]
        for item in audit.measures
        if item.fidelity_status == "POTENTIALLY_INCORRECT"
    }
    assert potential == {
        "# Events",
        "Results With Splits",
        "Total Transition Seconds",
        "Total Recorded Seconds",
    }


def test_audit_hashes_inputs_and_uses_stable_finding_ids() -> None:
    first = golden_audit()
    second = golden_audit()

    assert first.to_dict() == second.to_dict()
    assert first.inputs["powerbi_source_tree_sha256"]
    assert first.inputs["powerbi_inventory_semantic_sha256"]
    assert first.inputs["snowflake_yaml_sha256"]
    assert first.inputs["benchmark_spec_sha256"]
    assert first.inputs["canonical_contract_sha256"]
    finding_ids = [
        finding_id for item in first.measures for finding_id in item.finding_ids
    ]
    assert len(finding_ids) == 46
    assert len(finding_ids) == len(set(finding_ids))
    assert all(item.startswith("fnd_") and len(item) == 20 for item in finding_ids)


def test_normalized_mapping_collision_requires_manual_review(tmp_path: Path) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    split_table = next(item for item in data["tables"] if item["name"] == "FCT_SPLIT")
    split_table["metrics"].append(
        {
            "name": "SPLIT-TIME-SECONDS",
            "expr": "SUM(FCT_SPLIT.SPLIT_SECONDS)",
        }
    )
    snowflake.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )

    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    finding = next(
        item for item in audit.measures if item.source["name"] == "Split Time Seconds"
    )
    assert finding.target is None
    assert finding.mapping_method == "AMBIGUOUS"
    assert finding.fidelity_status == "MANUAL_REVIEW_REQUIRED"
    assert finding.reason_codes == ("TARGET_NORMALIZATION_COLLISION",)
    assert audit.summary["extra_target_metric_count"] == 2


def test_explicit_target_metadata_precedes_identifier_normalization(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    split_table = next(item for item in data["tables"] if item["name"] == "FCT_SPLIT")
    metric = next(
        item for item in split_table["metrics"] if item["name"] == "SPLIT_TIME_SECONDS"
    )
    metric["name"] = "EXPLICITLY_MAPPED_SPLIT_TOTAL"
    metric["source_measure"] = "Split Time Seconds"
    snowflake.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )

    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    finding = next(
        item for item in audit.measures if item.source["name"] == "Split Time Seconds"
    )
    assert finding.mapping_method == "EXPLICIT_MAPPING"
    assert finding.target["name"] == "EXPLICITLY_MAPPED_SPLIT_TOTAL"
    assert finding.fidelity_status == "MANUAL_REVIEW_REQUIRED"
    assert finding.reason_codes == ("TARGET_IDENTITY_DRIFT",)


def test_one_target_reused_by_multiple_sources_is_a_blocking_collision(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    result_table = next(item for item in data["tables"] if item["name"] == "FCT_RESULT")
    bike_metric = next(
        item for item in result_table["metrics"] if item["name"] == "BIKE_TIME_SECONDS"
    )
    bike_metric["source_measure"] = "Swim Time Seconds"
    snowflake.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )

    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    reused = [
        item
        for item in audit.measures
        if item.source["name"] in {"Bike Time Seconds", "Swim Time Seconds"}
    ]
    assert {item.mapping_method for item in reused} == {"AMBIGUOUS"}
    assert {item.fidelity_status for item in reused} == {
        "MANUAL_REVIEW_REQUIRED"
    }
    assert all("DUPLICATE_TARGET_MAPPING" in item.reason_codes for item in reused)
    assert audit.summary["matched_measure_count"] == 19
    assert any(
        item["code"] == "DUPLICATE_TARGET_MAPPING"
        for item in audit.model_findings
    )


def test_benchmark_allowlist_is_bound_to_reviewed_target_expression(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    result_table = next(item for item in data["tables"] if item["name"] == "FCT_RESULT")
    metric = next(
        item for item in result_table["metrics"] if item["name"] == "BIKE_TIME_SECONDS"
    )
    metric["expr"] = "SUM(FCT_RESULT.SUM_OF_BIKE_SECONDS) * 2"
    snowflake.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )

    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    finding = next(
        item for item in audit.measures if item.source["name"] == "Bike Time Seconds"
    )
    assert finding.fidelity_status == "MANUAL_REVIEW_REQUIRED"
    assert finding.reason_codes == ("TARGET_EXPRESSION_DRIFT",)
    assert finding.automation_disposition == "MANUAL_REVIEW_REQUIRED"


def test_declared_target_metadata_values_are_compared(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    result_table = next(item for item in data["tables"] if item["name"] == "FCT_RESULT")
    metric = next(
        item for item in result_table["metrics"] if item["name"] == "BIKE_TIME_SECONDS"
    )
    metric.update(
        {
            "description": "Wrong description",
            "format_string": "wrong-format",
            "display_folder": "Wrong folder",
            "lineage_tag": "wrong-lineage",
            "unit": "meters",
        }
    )
    snowflake.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )

    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    finding = next(
        item for item in audit.measures if item.source["name"] == "Bike Time Seconds"
    )
    assert {
        "DESCRIPTION_MISMATCH",
        "DISPLAY_FOLDER_MISMATCH",
        "FORMAT_MISMATCH",
        "LINEAGE_MISMATCH",
        "TARGET_UNIT_MISMATCH",
    }.issubset(finding.metadata_findings)
    assert "FORMAT_NOT_PRESERVED" not in finding.metadata_findings

    canonical = root / "canonical.yml"
    canonical.write_text(
        yaml.safe_dump(
            {
                "metrics": [
                    {
                        "name": "bike_time_seconds",
                        "config": {
                            "meta": {
                                "power_bi": {
                                    "measure": "Bike Time Seconds",
                                },
                                "snowflake": {
                                    "logical_table": "FCT_RESULT",
                                    "metric_name": "BIKE_TIME_SECONDS",
                                },
                            }
                        },
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    canonical_audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=canonical,
    )
    canonical_finding = next(
        item
        for item in canonical_audit.measures
        if item.source["name"] == "Bike Time Seconds"
    )
    assert canonical_finding.automation_disposition == "MANUAL_REVIEW_REQUIRED"
    assert "TARGET_METADATA_DRIFT" in canonical_finding.reason_codes


def test_canonical_resolution_gates_automation_and_collisions_are_invalid(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    source_audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    source = next(
        item.source
        for item in source_audit.measures
        if item.source["name"] == "Bike Time Seconds"
    )
    snowflake_data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    result_table = next(
        item for item in snowflake_data["tables"] if item["name"] == "FCT_RESULT"
    )
    target_metric = next(
        item
        for item in result_table["metrics"]
        if item["name"] == "BIKE_TIME_SECONDS"
    )
    target_metric.update(
        {
            "description": source["description"],
            "format_string": source["format_string"],
            "display_folder": source["display_folder"],
            "lineage_tag": source["lineage_tag"],
            "unit": source["target_unit"],
        }
    )
    snowflake.write_text(
        yaml.safe_dump(snowflake_data, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    canonical = root / "canonical.yml"
    canonical_data = {
        "metrics": [
            {
                "name": "bike_time_seconds",
                "config": {
                    "meta": {
                        "power_bi": {"measure": "Bike Time Seconds"},
                        "snowflake": {
                            "logical_table": "FCT_RESULT",
                            "metric_name": "BIKE_TIME_SECONDS",
                        },
                    }
                },
            }
        ]
    }
    canonical.write_text(
        yaml.safe_dump(canonical_data, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    without_canonical = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    with_canonical = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=canonical,
    )
    finding = next(
        item
        for item in with_canonical.measures
        if item.source["name"] == "Bike Time Seconds"
    )
    assert finding.canonical["resolution_status"] == "EXACT_MAPPING"
    assert finding.automation_disposition == "AUTO_CONVERT"
    assert with_canonical.audit_id != without_canonical.audit_id

    canonical_data["metrics"].append(
        {
            "name": "duplicate_bike_time_seconds",
            "config": {
                "meta": {
                    "power_bi": {"measure": "Bike Time Seconds"},
                    "snowflake": {
                        "logical_table": "FCT_RESULT",
                        "metric_name": "BIKE_TIME_SECONDS",
                    },
                }
            },
        }
    )
    canonical.write_text(
        yaml.safe_dump(canonical_data, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(AuditInputError, match="more than once"):
        audit_powerbi_snowflake(
            model_dir=model,
            snowflake_yaml=snowflake,
            benchmark_spec=spec,
            repository_root=root,
            canonical_yaml=canonical,
        )


def test_reviewed_structure_drift_invalidates_static_equivalence(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    distance = next(item for item in data["tables"] if item["name"] == "DIM_DISTANCE")
    dimension = next(
        item for item in distance["dimensions"] if item["name"] == "DISTANCE"
    )
    dimension["expr"] = "UPPER(DISTANCE)"
    snowflake.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )

    target_drift = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    finding = next(
        item
        for item in target_drift.measures
        if item.source["name"] == "Bike Time Seconds"
    )
    assert finding.fidelity_status == "MANUAL_REVIEW_REQUIRED"
    assert "SNOWFLAKE_REVIEWED_STRUCTURE_DRIFT" in finding.reason_codes
    assert target_drift.snowflake_inventory["reviewed_profile_structure"] == {
        "status": "DRIFT",
        "profile": "pbi_trial_measure_conversion_v2",
        "powerbi_matches": True,
        "snowflake_matches": False,
    }
    assert any(
        item["code"] == "REVIEWED_MODEL_STRUCTURE_DRIFT"
        for item in target_drift.model_findings
    )

    root, model, snowflake, spec = copied_inputs(tmp_path / "source-drift")
    table_path = model / "definition" / "tables" / "DIM_DISTANCE.tmdl"
    text = table_path.read_text(encoding="utf-8")
    table_path.write_text(
        text.replace("dataType: string", "dataType: int64", 1),
        encoding="utf-8",
        newline="\n",
    )
    source_drift = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    finding = next(
        item
        for item in source_drift.measures
        if item.source["name"] == "Bike Time Seconds"
    )
    assert finding.fidelity_status == "MANUAL_REVIEW_REQUIRED"
    assert "POWERBI_REVIEWED_STRUCTURE_DRIFT" in finding.reason_codes


def test_dependency_canonical_and_automation_risk_propagates(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    baseline = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    source = next(
        item.source
        for item in baseline.measures
        if item.source["name"] == "Run Time Hours"
    )
    data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    result_table = next(item for item in data["tables"] if item["name"] == "FCT_RESULT")
    metric = next(
        item for item in result_table["metrics"] if item["name"] == "RUN_TIME_HOURS"
    )
    metric.update(
        {
            "description": source["description"],
            "format_string": source["format_string"],
            "display_folder": source["display_folder"],
            "lineage_tag": source["lineage_tag"],
            "unit": source["target_unit"],
        }
    )
    snowflake.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )
    canonical = root / "canonical.yml"
    canonical.write_text(
        yaml.safe_dump(
            {
                "metrics": [
                    {
                        "name": "run_time_hours",
                        "config": {
                            "meta": {
                                "power_bi": {"measure": "Run Time Hours"},
                                "snowflake": {
                                    "logical_table": "FCT_RESULT",
                                    "metric_name": "RUN_TIME_HOURS",
                                },
                            }
                        },
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=canonical,
    )
    finding = next(
        item for item in audit.measures if item.source["name"] == "Run Time Hours"
    )
    assert finding.fidelity_status == "STRUCTURALLY_EQUIVALENT"
    assert finding.automation_disposition == "MANUAL_REVIEW_REQUIRED"
    assert "TRANSITIVE_DEPENDENCY_AUTOMATION_RISK" in finding.reason_codes
    assert any(
        risk.startswith("Run Time Seconds:UNRESOLVED_BENCHMARK_EVIDENCE")
        for risk in finding.dependency_risks
    )


def test_relationship_drift_is_reported_without_changing_measure_count(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    data["relationships"] = data["relationships"][:-1]
    snowflake.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )

    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    comparison = audit.relationship_comparison
    assert comparison["source_relationship_count"] == 7
    assert comparison["target_relationship_count"] == 6
    assert comparison["endpoint_match_count"] == 6
    assert len(comparison["source_only"]) == 1
    assert any(
        item["code"] == "SOURCE_RELATIONSHIPS_OMITTED"
        for item in audit.model_findings
    )


def test_cardinality_and_nondefault_relationship_properties_are_blockers(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    data = yaml.safe_load(snowflake.read_text(encoding="utf-8"))
    data["relationships"][0]["relationship_type"] = "one_to_many"
    snowflake.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
    )
    cardinality = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    assert cardinality.relationship_comparison["cardinality_mismatch_count"] == 1
    assert cardinality.summary["model_blocker_count"] >= 1
    assert any(
        item["code"] == "RELATIONSHIP_OR_CARDINALITY_DRIFT"
        for item in cardinality.model_findings
    )

    root, model, snowflake, spec = copied_inputs(tmp_path / "inactive")
    relationships = model / "definition" / "relationships.tmdl"
    text = relationships.read_text(encoding="utf-8")
    relationships.write_text(
        text.replace(
            "\ttoColumn: DIM_AGE_GROUP.AGE_GROUP_ID\n",
            "\ttoColumn: DIM_AGE_GROUP.AGE_GROUP_ID\n\tisActive: false\n",
            1,
        ),
        encoding="utf-8",
        newline="\n",
    )
    inactive = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    assert inactive.relationship_comparison["property_mismatch_count"] == 1
    assert any(
        item["status"] == "RELATIONSHIP_PROPERTY_MISMATCH"
        for item in inactive.relationship_comparison["target_records"]
    )


def _evidence_inputs(audit) -> dict[str, str | None]:
    return {
        "powerbi_model_tree_sha256": audit.inputs[
            "powerbi_source_tree_sha256"
        ],
        "snowflake_yaml_sha256": audit.inputs["snowflake_yaml_sha256"],
        "benchmark_spec_sha256": audit.inputs["benchmark_spec_sha256"],
        "behavioral_baseline_sha256": audit.inputs[
            "behavioral_baseline_sha256"
        ],
        "snowflake_query_pack_sha256": audit.inputs[
            "snowflake_query_pack_sha256"
        ],
    }


def _diagnostic_inputs(audit) -> dict[str, str | None]:
    return {
        "behavioral_baseline_sha256": audit.inputs[
            "behavioral_baseline_sha256"
        ],
        "benchmark_spec_sha256": audit.inputs["benchmark_spec_sha256"],
        "powerbi_source_tree_sha256": audit.inputs[
            "powerbi_source_tree_sha256"
        ],
        "snowflake_yaml_sha256": audit.inputs["snowflake_yaml_sha256"],
    }


def _refresh_evidence_hashes(value: dict[str, object]) -> None:
    for result in value.get("results", []):
        for side in ("power_bi", "snowflake"):
            endpoint = result[side]
            payload = {
                "complete": endpoint["complete"],
                "rows": endpoint["rows"],
                "status": endpoint["status"],
            }
            endpoint["result_sha256"] = hashlib.sha256(
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest()


def _rows_for_slice(slice_name: str, value: int | float) -> list[dict[str, object]]:
    return [
        {
            "coordinates": {
                dimension: f"value-{grouping_index}-{dimension}"
                for dimension in dimensions
            },
            "value": value,
        }
        for grouping_index, dimensions in enumerate(
            SLICE_DIMENSION_SETS[slice_name],
            start=1,
        )
    ]


def _runtime_evidence(audit, *, mismatch: str | None = None) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for item in audit.measures:
        if item.target is None:
            continue
        for slice_name in audit.runtime_evidence["required_slices_by_measure"][
            item.source["name"]
        ]:
            snowflake_value = 2 if item.source["name"] == mismatch else 1
            rows = _rows_for_slice(slice_name, 1)
            results.append(
                {
                    "source_measure": item.source["name"],
                    "slice": slice_name,
                    "value_type": "INTEGER",
                    "power_bi": {
                        "status": "AVAILABLE",
                        "complete": True,
                        "rows": rows,
                    },
                    "snowflake": {
                        "status": "AVAILABLE",
                        "complete": True,
                        "rows": [
                            {
                                "coordinates": dict(row["coordinates"]),
                                "value": snowflake_value,
                            }
                            for row in rows
                        ],
                    },
                }
            )
    return {
        "schema_version": 1,
        "profile": "PBI_TRIAL_V2_AUDIT",
        "subject_hashes": _evidence_inputs(audit),
        "comparison": {
            "integer": "EXACT",
            "decimal_absolute_tolerance": 1e-9,
            "decimal_relative_tolerance": 1e-9,
            "blank_null_representation": "JSON_NULL",
        },
        "results": results,
    }


def test_runtime_evidence_requires_all_applicable_slices_and_detects_mismatch(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    baseline = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    evidence = root / "results.json"
    write_json(
        evidence,
        _runtime_evidence(baseline, mismatch="Bike Time Seconds"),
    )

    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        result_evidence=evidence,
        repository_root=root,
        canonical_yaml=None,
    )
    statuses = Counter(item.behavioral_status for item in audit.measures)
    assert statuses == {"NOT_AVAILABLE": 25, "PASSED": 20, "FAILED": 1}
    failed = next(
        item for item in audit.measures if item.source["name"] == "Bike Time Seconds"
    )
    assert failed.behavioral_status == "FAILED"

    partial = _runtime_evidence(baseline)
    partial["results"] = [
        item
        for item in partial["results"]
        if not (
            item["source_measure"] == "Split Time Seconds"
            and item["slice"] == "LEG"
        )
    ]
    write_json(evidence, partial)
    partial_audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        result_evidence=evidence,
        repository_root=root,
        canonical_yaml=None,
    )
    split = next(
        item
        for item in partial_audit.measures
        if item.source["name"] == "Split Time Seconds"
    )
    assert split.behavioral_status == "NOT_AVAILABLE"

    coordinate_mismatch = _runtime_evidence(baseline)
    split_leg = next(
        item
        for item in coordinate_mismatch["results"]
        if item["source_measure"] == "Split Time Seconds"
        and item["slice"] == "LEG"
    )
    split_leg["snowflake"]["rows"][0]["coordinates"] = {
        "FCT_SPLIT.LEG": "OTHER"
    }
    write_json(evidence, coordinate_mismatch)
    coordinate_audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        result_evidence=evidence,
        repository_root=root,
        canonical_yaml=None,
    )
    coordinate_finding = next(
        item
        for item in coordinate_audit.measures
        if item.source["name"] == "Split Time Seconds"
    )
    assert coordinate_finding.behavioral_status == "FAILED"


def test_runtime_coordinate_contract_rejects_empty_alias_and_missing_groupings(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    baseline = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    evidence_path = root / "coordinate-results.json"

    all_group_shapes = _runtime_evidence(baseline)
    for slice_name in ("GENDER", "EVENT", "COUNTRY", "DIVISION"):
        all_group_shapes["results"].append(
            {
                "source_measure": "Result Rows",
                "slice": slice_name,
                "value_type": "INTEGER",
                "power_bi": {
                    "status": "AVAILABLE",
                    "complete": True,
                    "rows": _rows_for_slice(slice_name, 1),
                },
                "snowflake": {
                    "status": "AVAILABLE",
                    "complete": True,
                    "rows": _rows_for_slice(slice_name, 1),
                },
            }
        )
    write_json(evidence_path, all_group_shapes)
    audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        result_evidence=evidence_path,
        repository_root=root,
        canonical_yaml=None,
    )

    empty = _runtime_evidence(baseline)
    split_leg = next(
        item
        for item in empty["results"]
        if item["source_measure"] == "Split Time Seconds"
        and item["slice"] == "LEG"
    )
    split_leg["power_bi"]["rows"] = []
    write_json(evidence_path, empty)
    with pytest.raises(AuditInputError, match="at least one row"):
        audit_powerbi_snowflake(
            model_dir=model,
            snowflake_yaml=snowflake,
            benchmark_spec=spec,
            result_evidence=evidence_path,
            repository_root=root,
            canonical_yaml=None,
        )

    aliased = _runtime_evidence(baseline)
    split_leg = next(
        item
        for item in aliased["results"]
        if item["source_measure"] == "Split Time Seconds"
        and item["slice"] == "LEG"
    )
    split_leg["power_bi"]["rows"][0]["coordinates"] = {"leg": "BIKE"}
    write_json(evidence_path, aliased)
    with pytest.raises(AuditInputError, match="schema validation"):
        audit_powerbi_snowflake(
            model_dir=model,
            snowflake_yaml=snowflake,
            benchmark_spec=spec,
            result_evidence=evidence_path,
            repository_root=root,
            canonical_yaml=None,
        )

    missing_country_groups = _runtime_evidence(baseline)
    country_coordinates = {
        dimension: f"value-{dimension}"
        for dimension in SLICE_DIMENSION_SETS["COUNTRY"][0]
    }
    missing_country_groups["results"].append(
        {
            "source_measure": "Result Rows",
            "slice": "COUNTRY",
            "value_type": "INTEGER",
            "power_bi": {
                "status": "AVAILABLE",
                "complete": True,
                "rows": [{"coordinates": country_coordinates, "value": 1}],
            },
            "snowflake": {
                "status": "AVAILABLE",
                "complete": True,
                "rows": [{"coordinates": country_coordinates, "value": 1}],
            },
        }
    )
    write_json(evidence_path, missing_country_groups)
    with pytest.raises(AuditInputError, match="missing grouping sets"):
        audit_powerbi_snowflake(
            model_dir=model,
            snowflake_yaml=snowflake,
            benchmark_spec=spec,
            result_evidence=evidence_path,
            repository_root=root,
            canonical_yaml=None,
        )


def test_integer_values_compare_exactly_even_when_declared_decimal(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    baseline = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    evidence = _runtime_evidence(baseline)
    result = next(
        item
        for item in evidence["results"]
        if item["source_measure"] == "Bike Time Seconds"
        and item["slice"] == "OVERALL"
    )
    result["value_type"] = "DECIMAL"
    result["power_bi"]["rows"][0]["value"] = 1_000_000_000
    result["snowflake"]["rows"][0]["value"] = 1_000_000_001
    evidence_path = root / "large-integers.json"
    write_json(evidence_path, evidence)

    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        result_evidence=evidence_path,
        repository_root=root,
        canonical_yaml=None,
    )
    finding = next(
        item for item in audit.measures if item.source["name"] == "Bike Time Seconds"
    )
    assert finding.behavioral_status == "FAILED"


def test_stale_runtime_hash_is_exit_state_evidence(tmp_path: Path) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    baseline = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    value = _runtime_evidence(baseline)
    value["subject_hashes"]["snowflake_yaml_sha256"] = "0" * 64
    evidence = root / "results.json"
    write_json(evidence, value)

    with pytest.raises(AuditStaleEvidenceError, match="stale"):
        audit_powerbi_snowflake(
            model_dir=model,
            snowflake_yaml=snowflake,
            benchmark_spec=spec,
            result_evidence=evidence,
            repository_root=root,
            canonical_yaml=None,
        )


def test_decimal_runtime_results_use_absolute_and_relative_one_e_minus_nine(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    baseline = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    evidence_path = root / "decimal-results.json"

    def evidence(delta: float) -> dict[str, object]:
        return {
            "schema_version": 1,
            "profile": "PBI_TRIAL_V2_AUDIT",
            "subject_hashes": _evidence_inputs(baseline),
            "comparison": {
                "integer": "EXACT",
                "decimal_absolute_tolerance": 1e-9,
                "decimal_relative_tolerance": 1e-9,
                "blank_null_representation": "JSON_NULL",
            },
            "results": [
                {
                    "source_measure": "Bike Time Seconds",
                    "slice": slice_name,
                    "value_type": "DECIMAL",
                    "power_bi": {
                        "status": "AVAILABLE",
                        "complete": True,
                        "rows": [
                            {
                                "coordinates": {
                                    dimension: f"value-{grouping_index}-{dimension}"
                                    for dimension in dimensions
                                },
                                "value": 1.0,
                            }
                            for grouping_index, dimensions in enumerate(
                                SLICE_DIMENSION_SETS[slice_name],
                                start=1,
                            )
                        ],
                    },
                    "snowflake": {
                        "status": "AVAILABLE",
                        "complete": True,
                        "rows": [
                            {
                                "coordinates": {
                                    dimension: f"value-{grouping_index}-{dimension}"
                                    for dimension in dimensions
                                },
                                "value": 1.0 + delta,
                            }
                            for grouping_index, dimensions in enumerate(
                                SLICE_DIMENSION_SETS[slice_name],
                                start=1,
                            )
                        ],
                    },
                }
                for slice_name in baseline.runtime_evidence[
                    "required_slices_by_measure"
                ]["Bike Time Seconds"]
            ],
        }

    write_json(evidence_path, evidence(5e-10))
    within = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        result_evidence=evidence_path,
        repository_root=root,
        canonical_yaml=None,
    )
    within_finding = next(
        item for item in within.measures if item.source["name"] == "Bike Time Seconds"
    )
    assert within_finding.behavioral_status == "PASSED"

    write_json(evidence_path, evidence(2e-9))
    outside = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        result_evidence=evidence_path,
        repository_root=root,
        canonical_yaml=None,
    )
    outside_finding = next(
        item for item in outside.measures if item.source["name"] == "Bike Time Seconds"
    )
    assert outside_finding.behavioral_status == "FAILED"


def test_defect_specific_rejections_are_the_only_proven_caught_signal(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    baseline = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    defects = [
        item
        for item in baseline.measures
        if item.source["semantic_status"] == "INTENTIONAL_DEFECT"
    ]
    omitted_defects = [item for item in defects if item.target is None]
    emitted_defect = next(item for item in defects if item.target is not None)
    diagnostics = {
        "schema_version": 1,
        "inputs": _diagnostic_inputs(baseline),
        "diagnostics": [
            {
                "source_measure": item.source["name"],
                "defect_code": item.source["intentional_defects"][0],
                "disposition": "REJECTED",
            }
            for item in omitted_defects
        ],
    }
    diagnostic_path = root / "diagnostics.yaml"
    diagnostic_path.write_text(
        yaml.safe_dump(diagnostics, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )

    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        snowflake_diagnostics=diagnostic_path,
        repository_root=root,
        canonical_yaml=None,
    )
    updated = [
        item
        for item in audit.measures
        if item.source["semantic_status"] == "INTENTIONAL_DEFECT"
    ]
    caught = [item for item in updated if item.detection_status == "PROVEN_CAUGHT"]
    assert {item.source["name"] for item in caught} == {
        item.source["name"] for item in omitted_defects
    }
    assert {item.observed_handling for item in caught} == {"REJECTED"}
    assert audit.summary["proven_caught_count"] == 2

    diagnostics["diagnostics"].append(
        {
            "source_measure": emitted_defect.source["name"],
            "defect_code": emitted_defect.source["intentional_defects"][0],
            "disposition": "REJECTED",
        }
    )
    diagnostic_path.write_text(
        yaml.safe_dump(diagnostics, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(AuditStateError, match="still emits an active target"):
        audit_powerbi_snowflake(
            model_dir=model,
            snowflake_yaml=snowflake,
            benchmark_spec=spec,
            snowflake_diagnostics=diagnostic_path,
            repository_root=root,
            canonical_yaml=None,
        )


def test_malformed_yaml_and_paths_outside_repository_are_rejected(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    snowflake.write_text("tables: [", encoding="utf-8")
    with pytest.raises(AuditInputError, match="could not be read or parsed"):
        audit_powerbi_snowflake(
            model_dir=model,
            snowflake_yaml=snowflake,
            benchmark_spec=spec,
            repository_root=root,
            canonical_yaml=None,
        )

    with pytest.raises(AuditInputError, match="inside"):
        audit_powerbi_snowflake(
            model_dir=model,
            snowflake_yaml=SNOWFLAKE,
            benchmark_spec=spec,
            repository_root=root,
            canonical_yaml=None,
        )


def test_invalid_tmdl_is_reported_as_invalid_audit_input(tmp_path: Path) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    table = model / "definition" / "tables" / "DIM_DISTANCE.tmdl"
    table.write_text(
        table.read_text(encoding="utf-8") + "\ntable DUPLICATE_DECLARATION\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(AuditInputError, match="inventory failed"):
        audit_powerbi_snowflake(
            model_dir=model,
            snowflake_yaml=snowflake,
            benchmark_spec=spec,
            repository_root=root,
            canonical_yaml=None,
        )


def test_generic_audit_is_not_labeled_v2_or_auto_approved(
    tmp_path: Path,
) -> None:
    root, model, snowflake, _spec = copied_inputs(tmp_path)
    canonical = root / "canonical.yml"
    canonical.write_text(
        yaml.safe_dump(
            {
                "metrics": [
                    {
                        "name": "bike_time_seconds",
                        "config": {
                            "meta": {
                                "power_bi": {
                                    "measure": "Bike Time Seconds",
                                },
                                "snowflake": {
                                    "logical_table": "FCT_RESULT",
                                    "metric_name": "BIKE_TIME_SECONDS",
                                },
                            }
                        },
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        repository_root=root,
        canonical_yaml=canonical,
    )
    finding = next(
        item for item in audit.measures if item.source["name"] == "Bike Time Seconds"
    )
    assert finding.fidelity_status == "MANUAL_REVIEW_REQUIRED"
    assert finding.reason_codes in {
        ("GENERIC_STRUCTURE_PROOF_UNAVAILABLE",),
        ("UNPROVEN_EXPRESSION_EQUIVALENCE",),
    }
    assert finding.automation_disposition == "MANUAL_REVIEW_REQUIRED"
    report = render_audit_markdown(audit)
    assert report.splitlines()[0] == "# Power BI to Snowflake Conversion Audit Findings"
    assert "v2 benchmark oracle" not in report
    assert "run_pbi_trial_v2_audit.py" not in report


def test_artifact_output_is_deterministic_and_check_is_read_only(
    tmp_path: Path,
) -> None:
    root, model, snowflake, spec = copied_inputs(tmp_path)
    audit = audit_powerbi_snowflake(
        model_dir=model,
        snowflake_yaml=snowflake,
        benchmark_spec=spec,
        repository_root=root,
        canonical_yaml=None,
    )
    output = root / "audit-output"
    paths = write_audit_output_directory(
        audit, output_dir=output, repository_root=root
    )
    before = {name: (root / path).read_bytes() for name, path in paths.items()}
    checked = write_audit_output_directory(
        audit, output_dir=output, repository_root=root, check=True
    )
    assert checked == paths
    assert before == {name: (root / path).read_bytes() for name, path in paths.items()}
    assert before == audit_artifact_contents(audit)

    (output / "conversion-findings.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(AuditStateError, match="stale"):
        write_audit_output_directory(
            audit, output_dir=output, repository_root=root, check=True
        )


def test_normalization_is_deterministic_and_never_fuzzy() -> None:
    assert normalize_snowflake_identifier("# Events") == "EVENTS"
    assert (
        normalize_snowflake_identifier("Weighted Bike Speed KM/H")
        == "WEIGHTED_BIKE_SPEED_KM_H"
    )
    assert normalize_snowflake_identifier("eventz") != normalize_snowflake_identifier(
        "# Events"
    )


def test_cli_writes_then_checks_controlled_output_and_returns_blocker_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_root = PROJECT_ROOT / ".tmp"
    runtime_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="powerbi-snowflake-audit-", dir=runtime_root
    ) as temporary:
        output = Path(temporary) / "artifacts"
        arguments = [
            "audit-powerbi-snowflake",
            "--model-dir",
            str(MODEL),
            "--snowflake-yaml",
            str(SNOWFLAKE),
            "--benchmark-spec",
            str(SPEC),
            "--output-dir",
            str(output),
            "--json",
        ]
        assert semantic_agent_main(arguments) == 3
        result = json.loads(capsys.readouterr().out)
        assert result["summary"]["matched_measure_count"] == 21
        assert len(result["artifacts"]) == 4

        assert semantic_agent_main([*arguments, "--check"]) == 3
        checked = json.loads(capsys.readouterr().out)
        assert checked["check"] is True

        (output / "conversion-findings.json").write_text("{}\n", encoding="utf-8")
        assert semantic_agent_main([*arguments, "--check"]) == 4
        error = json.loads(capsys.readouterr().out)
        assert error["error"]["code"] == "AUDIT_STATE_CONFLICT"


def test_cli_module_entrypoint_supports_skill_wrapper_fallback() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "semantic_poc.agent.cli",
            "audit-powerbi-snowflake",
            "--help",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--snowflake-diagnostics" in result.stdout
    assert "--result-evidence" in result.stdout


def test_cli_invalid_tmdl_returns_input_exit_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_root = PROJECT_ROOT / ".tmp"
    runtime_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="invalid-audit-tmdl-", dir=runtime_root
    ) as temporary:
        model = Path(temporary) / "invalid.SemanticModel"
        shutil.copytree(MODEL, model)
        table = model / "definition" / "tables" / "DIM_DISTANCE.tmdl"
        table.write_text(
            table.read_text(encoding="utf-8")
            + "\ntable DUPLICATE_DECLARATION\n",
            encoding="utf-8",
            newline="\n",
        )
        exit_code = semantic_agent_main(
            [
                "audit-powerbi-snowflake",
                "--model-dir",
                str(model),
                "--snowflake-yaml",
                str(SNOWFLAKE),
                "--benchmark-spec",
                str(SPEC),
                "--output-dir",
                str(Path(temporary) / "output"),
                "--json",
            ]
        )
        assert exit_code == 2
        error = json.loads(capsys.readouterr().out)
        assert error["error"]["code"] == "AUDIT_INPUT_INVALID"
