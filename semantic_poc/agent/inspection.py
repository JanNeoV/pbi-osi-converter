from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from semantic_poc.src.models import (
    DBT_SEMANTIC_MANIFEST,
    DBT_SEMANTIC_YAML,
    PBI_DEFINITION_DIR,
    PROJECT_ROOT,
    SNOWFLAKE_ENVIRONMENT,
    STATUS_MANUAL_REVIEW_REQUIRED,
    STATUS_UNSUPPORTED_IN_SNOWFLAKE,
    all_snowflake_metric_names,
    build_snowflake_semantic_view,
    compare_semantics,
    find_powerbi_measure,
    load_json,
    load_yaml,
    metric_ref_name,
    metric_translation_pattern,
    normalize_dbt_semantics,
    normalize_snowflake_environment,
    parse_tmdl_definition,
    relative_posix,
)


class MetricInspectionError(RuntimeError):
    code = "INSPECTION_FAILED"

    def __init__(self, message: str, *, requested_metric: str, candidates: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.requested_metric = requested_metric
        self.candidates = candidates

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": str(self),
                "requested_metric": self.requested_metric,
            }
        }
        if self.candidates:
            result["error"]["candidates"] = list(self.candidates)
        return result


class MetricNotFoundError(MetricInspectionError):
    code = "INVALID_METRIC_NAME"


class MetricAmbiguousError(MetricInspectionError):
    code = STATUS_MANUAL_REVIEW_REQUIRED


@dataclass(frozen=True)
class ResolvedMetric:
    metric: dict[str, Any]
    resolved_from: str


def _metric_meta(metric: dict[str, Any]) -> dict[str, Any]:
    return metric.get("config", {}).get("meta", {}) or {}


def resolve_metric(dbt_yaml: dict[str, Any], requested_metric: str) -> ResolvedMetric:
    if not isinstance(requested_metric, str) or not requested_metric.strip():
        raise MetricNotFoundError("Metric name must not be empty.", requested_metric=str(requested_metric))
    metrics = [item for item in dbt_yaml.get("metrics", []) if isinstance(item, dict) and item.get("name")]
    canonical = [item for item in metrics if item["name"] == requested_metric]
    if canonical:
        return ResolvedMetric(canonical[0], "CANONICAL")

    requested_key = requested_metric.casefold()
    matches: dict[str, tuple[dict[str, Any], set[str]]] = {}
    for metric in metrics:
        meta = _metric_meta(metric)
        aliases = (
            (meta.get("power_bi", {}) or {}).get("measure"),
            (meta.get("snowflake", {}) or {}).get("metric_name"),
        )
        sources = ("POWER_BI", "SNOWFLAKE")
        for alias, source in zip(aliases, sources):
            if isinstance(alias, str) and alias.casefold() == requested_key:
                existing = matches.setdefault(metric["name"], (metric, set()))
                existing[1].add(source)
    if not matches:
        raise MetricNotFoundError(
            f"No canonical metric or exact mapped target name matches {requested_metric!r}.",
            requested_metric=requested_metric,
        )
    if len(matches) > 1:
        candidates = tuple(sorted(matches))
        raise MetricAmbiguousError(
            f"Mapped target name {requested_metric!r} resolves to multiple canonical metrics.",
            requested_metric=requested_metric,
            candidates=candidates,
        )
    metric, sources = next(iter(matches.values()))
    return ResolvedMetric(metric, "+".join(sorted(sources)))


def _raw_pattern(dbt_yaml: dict[str, Any], metric: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    measures: dict[str, dict[str, Any]] = {}
    for semantic_model in dbt_yaml.get("semantic_models", []) or []:
        for measure in semantic_model.get("measures", []) or []:
            if isinstance(measure, dict) and measure.get("name"):
                measures[measure["name"]] = measure
    type_params = metric.get("type_params", {}) or {}
    measure_name = metric_ref_name(type_params.get("measure"))
    numerator = metric_ref_name(type_params.get("numerator"))
    denominator = metric_ref_name(type_params.get("denominator"))
    measure = measures.get(measure_name or "")
    return metric_translation_pattern(metric.get("type"), measure, numerator, denominator), measure


def _canonical_summary(
    metric: dict[str, Any],
    semantic_yaml_path: Path,
    dbt_yaml: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    meta = _metric_meta(metric)
    semantic_meta = meta.get("semantic_contract", {}) or {}
    type_params = metric.get("type_params", {}) or {}
    pattern, _ = _raw_pattern(dbt_yaml, metric)
    return (
        {
            "name": metric["name"],
            "file": relative_posix(semantic_yaml_path),
            "label": metric.get("label") or metric["name"],
            "description": metric.get("description") or "",
            "type": metric.get("type"),
            "measure": metric_ref_name(type_params.get("measure")),
            "numerator": metric_ref_name(type_params.get("numerator")),
            "denominator": metric_ref_name(type_params.get("denominator")),
            "public": semantic_meta.get("public") is True,
        },
        pattern,
    )


def inspect_metric(
    requested_metric: str,
    *,
    semantic_yaml_path: Path = DBT_SEMANTIC_YAML,
    manifest_path: Path = DBT_SEMANTIC_MANIFEST,
    powerbi_definition_dir: Path = PBI_DEFINITION_DIR,
) -> dict[str, Any]:
    semantic_yaml_path = semantic_yaml_path.resolve()
    manifest_path = manifest_path.resolve()
    powerbi_definition_dir = powerbi_definition_dir.resolve()
    if not semantic_yaml_path.is_file():
        raise MetricInspectionError(
            f"Canonical semantic YAML does not exist: {semantic_yaml_path}",
            requested_metric=requested_metric,
        )
    dbt_yaml = load_yaml(semantic_yaml_path) or {}
    resolved = resolve_metric(dbt_yaml, requested_metric)
    metric = resolved.metric
    canonical, raw_pattern = _canonical_summary(metric, semantic_yaml_path, dbt_yaml)
    meta = _metric_meta(metric)
    power_bi_mapping = meta.get("power_bi", {}) or {}
    snowflake_mapping = meta.get("snowflake", {}) or {}
    diagnostics: list[str] = []

    powerbi = None
    actual_measure = None
    if powerbi_definition_dir.is_dir():
        powerbi = parse_tmdl_definition(powerbi_definition_dir, PROJECT_ROOT)
        if power_bi_mapping.get("table") and power_bi_mapping.get("measure"):
            actual_measure = find_powerbi_measure(
                powerbi,
                power_bi_mapping["table"],
                power_bi_mapping["measure"],
            )
    else:
        diagnostics.append(f"Power BI definition directory does not exist: {powerbi_definition_dir}")

    result: dict[str, Any] = {
        "requested_metric": requested_metric,
        "resolved_from": resolved.resolved_from,
        "canonical": canonical,
        "translation_pattern": raw_pattern,
        "mappings": {
            "power_bi": {
                "table": power_bi_mapping.get("table"),
                "measure": power_bi_mapping.get("measure"),
                "exists": actual_measure is not None,
                "actual_dax": actual_measure.get("expression") if actual_measure else None,
            },
            "snowflake": {
                "logical_table": snowflake_mapping.get("logical_table"),
                "metric_name": snowflake_mapping.get("metric_name"),
                "generated": None,
            },
        },
        "compatibility_status": STATUS_MANUAL_REVIEW_REQUIRED,
        "diagnostics": diagnostics,
    }

    if not manifest_path.is_file():
        diagnostics.append(
            "Compiled dbt semantic manifest is missing; run `dbt --no-version-check parse` before requesting compatibility status."
        )
        return result

    try:
        dbt_semantics = normalize_dbt_semantics(
            load_json(manifest_path),
            dbt_yaml,
            semantic_yaml_path=semantic_yaml_path,
            semantic_manifest_path=manifest_path,
            project_root=PROJECT_ROOT,
        )
    except (KeyError, TypeError, ValueError) as exc:
        diagnostics.append(f"Compiled dbt semantics could not be normalized: {exc}")
        return result

    normalized_metric = dbt_semantics.get("metrics", {}).get(metric["name"])
    if normalized_metric is None:
        diagnostics.append("Metric is not included in the normalized public/support metric set.")
        return result

    result["translation_pattern"] = normalized_metric.get("translation_pattern")
    if powerbi is None:
        diagnostics.append("Power BI compatibility could not be calculated without a definition directory.")
        return result

    environment = normalize_snowflake_environment(load_yaml(SNOWFLAKE_ENVIRONMENT) or {})
    snowflake = build_snowflake_semantic_view(dbt_semantics, environment)
    snowflake_name = snowflake_mapping.get("metric_name") or metric["name"]
    result["mappings"]["snowflake"]["generated"] = snowflake_name in all_snowflake_metric_names(snowflake)
    comparison = compare_semantics(dbt_semantics, powerbi, snowflake)
    row = next((item for item in comparison.get("rows", []) if item.get("metric") == metric["name"]), None)
    if row is None:
        diagnostics.append("Metric has no public compatibility row.")
        return result

    compatibility_status = row["status"]
    if compatibility_status == STATUS_UNSUPPORTED_IN_SNOWFLAKE or raw_pattern == STATUS_MANUAL_REVIEW_REQUIRED:
        diagnostics.append(f"Underlying compatibility status: {compatibility_status}")
        compatibility_status = STATUS_MANUAL_REVIEW_REQUIRED
    result["compatibility_status"] = compatibility_status
    return result
