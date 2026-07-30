from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import yaml

from semantic_poc.src.models import PROJECT_ROOT
from semantic_poc.src.semantic_ir import (
    SupportClassification,
    build_canonical_metric_ir_index,
    generate_dax_definition,
    generate_snowflake_definition,
    validate_cross_target,
)


CANONICAL_SOURCE = "models/semantic/triathlon_semantic.yml"


class CanonicalDriftError(ValueError):
    pass


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def safe_canonical_input(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    configured = candidate.absolute()
    resolved = configured.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise CanonicalDriftError("Canonical drift inputs must be repository-contained.") from exc
    if configured.is_symlink() or not resolved.is_file():
        raise CanonicalDriftError(f"Canonical drift input is missing or unsafe: {path}")
    return resolved


def _load_index(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CanonicalDriftError(f"Canonical YAML could not be read: {exc}") from exc
    if not isinstance(value, Mapping):
        raise CanonicalDriftError("Canonical YAML must contain an object.")
    return build_canonical_metric_ir_index(value, canonical_source=CANONICAL_SOURCE)


def _metric_snapshot(metric: Any) -> dict[str, Any]:
    value = _json_value(metric)
    value.pop("trace_id", None)
    source = value.get("source")
    if isinstance(source, dict):
        source.pop("line", None)
    return value


def compile_target_snapshots(path: str | Path) -> dict[str, Any]:
    canonical_path = safe_canonical_input(path)
    index = _load_index(canonical_path)
    metrics: dict[str, Any] = {}
    unsupported: list[str] = []
    for name, metric in sorted(index.items()):
        metric_snapshot = _metric_snapshot(metric)
        if not (
            metric.power_bi.table
            and metric.power_bi.measure
            and metric.snowflake.logical_table
            and metric.snowflake.metric_name
        ):
            continue
        dax = generate_dax_definition(metric, index)
        snowflake = generate_snowflake_definition(metric, index)
        cross_target = validate_cross_target(metric, dax, snowflake)
        if (
            metric.support is not SupportClassification.SUPPORTED_PATTERN
            or dax.definition is None
            or snowflake.definition is None
            or not cross_target.valid
        ):
            unsupported.append(name)
            continue
        signature = _json_value(metric.signature)
        signature.pop("trace_id", None)
        metrics[name] = {
            "canonical_metric": name,
            "canonical_source": CANONICAL_SOURCE,
            "canonical_ir": metric_snapshot,
            "canonical_ir_sha256": _sha(metric_snapshot),
            "semantic_signature_sha256": _sha(signature),
            "power_bi": {
                "table": metric.power_bi.table,
                "measure": metric.power_bi.measure,
                "description": metric.description,
                "format_string": metric.power_bi.format_string,
                "display_folder": metric.power_bi.display_folder,
                "dax": dax.definition,
            },
            "snowflake": dict(snowflake.definition),
        }
    return {
        "canonical_path": canonical_path,
        "canonical_file_sha256": hashlib.sha256(canonical_path.read_bytes()).hexdigest(),
        "all_metric_ir": {name: _metric_snapshot(metric) for name, metric in sorted(index.items())},
        "metrics": metrics,
        "unsupported_mapped_metrics": unsupported,
    }


def _target_map(compiled: Mapping[str, Any], target: str) -> dict[str, Any]:
    return {
        name: value[target]
        for name, value in compiled["metrics"].items()
    }


def _changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> set[str]:
    names = set(before) | set(after)
    return {name for name in names if before.get(name) != after.get(name)}


def check_canonical_drift(
    baseline: str | Path,
    current: str | Path,
    *,
    observed_power_bi: Mapping[str, Any] | None = None,
    observed_snowflake: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_compiled = compile_target_snapshots(baseline)
    current_compiled = compile_target_snapshots(current)
    baseline_ir = baseline_compiled["all_metric_ir"]
    current_ir = current_compiled["all_metric_ir"]
    canonical_changes = _changed(baseline_ir, current_ir)

    baseline_power_bi = _target_map(baseline_compiled, "power_bi")
    expected_power_bi = _target_map(current_compiled, "power_bi")
    baseline_snowflake = _target_map(baseline_compiled, "snowflake")
    expected_snowflake = _target_map(current_compiled, "snowflake")
    actual_power_bi = dict(observed_power_bi) if observed_power_bi is not None else expected_power_bi
    actual_snowflake = dict(observed_snowflake) if observed_snowflake is not None else expected_snowflake

    expected_power_bi_changes = _changed(baseline_power_bi, expected_power_bi)
    expected_snowflake_changes = _changed(baseline_snowflake, expected_snowflake)
    actual_power_bi_changes = _changed(baseline_power_bi, actual_power_bi)
    actual_snowflake_changes = _changed(baseline_snowflake, actual_snowflake)
    power_bi_misaligned = _changed(expected_power_bi, actual_power_bi)
    snowflake_misaligned = _changed(expected_snowflake, actual_snowflake)
    unrelated = (
        (actual_power_bi_changes | actual_snowflake_changes) - canonical_changes
    )
    unexpected = unrelated | power_bi_misaligned | snowflake_misaligned
    unsupported = sorted(
        set(baseline_compiled["unsupported_mapped_metrics"])
        | set(current_compiled["unsupported_mapped_metrics"])
    )
    aligned = (
        not unsupported
        and actual_power_bi_changes == expected_power_bi_changes
        and actual_snowflake_changes == expected_snowflake_changes
        and not unexpected
    )
    details = []
    for name in sorted(canonical_changes):
        details.append(
            {
                "canonical_metric": name,
                "before_ir_sha256": _sha(baseline_ir.get(name)),
                "current_ir_sha256": _sha(current_ir.get(name)),
                "power_bi_changed": name in expected_power_bi_changes,
                "snowflake_changed": name in expected_snowflake_changes,
            }
        )
    return {
        "schema_version": 1,
        "canonical_source": CANONICAL_SOURCE,
        "canonical_changes": len(canonical_changes),
        "changed_canonical_metrics": sorted(canonical_changes),
        "canonical_change_details": details,
        "expected_power_bi_changes": len(expected_power_bi_changes),
        "expected_power_bi_metrics": sorted(expected_power_bi_changes),
        "expected_snowflake_changes": len(expected_snowflake_changes),
        "expected_snowflake_metrics": sorted(expected_snowflake_changes),
        "actual_power_bi_changes": len(actual_power_bi_changes),
        "actual_power_bi_metrics": sorted(actual_power_bi_changes),
        "actual_snowflake_changes": len(actual_snowflake_changes),
        "actual_snowflake_metrics": sorted(actual_snowflake_changes),
        "unexpected_target_only_drift": len(unexpected),
        "unexpected_target_metrics": sorted(unexpected),
        "unrelated_object_changes": len(unrelated),
        "cross_target_semantic_drift": len(power_bi_misaligned | snowflake_misaligned),
        "unsupported_mapped_metrics": unsupported,
        "synchronization_status": "ALIGNED" if aligned else "MANUAL_REVIEW_REQUIRED",
        "deployment_performed": False,
        "baseline_hashes": {
            "canonical": baseline_compiled["canonical_file_sha256"],
            "power_bi": _sha(baseline_power_bi),
            "snowflake": _sha(baseline_snowflake),
        },
        "current_hashes": {
            "canonical": current_compiled["canonical_file_sha256"],
            "power_bi": _sha(actual_power_bi),
            "snowflake": _sha(actual_snowflake),
        },
        "expected_targets": {
            "power_bi": expected_power_bi,
            "snowflake": expected_snowflake,
        },
        "baseline_targets": {
            "power_bi": baseline_power_bi,
            "snowflake": baseline_snowflake,
        },
    }


__all__ = [
    "CanonicalDriftError",
    "check_canonical_drift",
    "compile_target_snapshots",
    "safe_canonical_input",
]
