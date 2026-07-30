from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .semantic_ir import (
    MetricPattern,
    SemanticMetricIR,
    SupportClassification,
    build_metric_ir_index,
    generate_dax_definition,
    generate_snowflake_definition,
    validate_cross_target,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DBT_SEMANTIC_YAML = PROJECT_ROOT / "models" / "semantic" / "triathlon_semantic.yml"
DBT_SEMANTIC_MANIFEST = PROJECT_ROOT / "target" / "semantic_manifest.json"
LEGACY_PBI_FIXTURE_ROOT = PROJECT_ROOT / "semantic_poc" / "fixtures" / "legacy_triathlon_pbi_model"
PBI_PBIP = LEGACY_PBI_FIXTURE_ROOT / "legacy_triathlon_pbi_model.pbip"
PBI_DEFINITION_DIR = LEGACY_PBI_FIXTURE_ROOT / "legacy_triathlon_pbi_model.SemanticModel" / "definition"
# Generated POC evidence preserves this historical logical source identifier.
# The runtime reads the tracked portable fixture above; the identifier is not a
# filesystem dependency and keeps the protected Task 01--04 outputs stable.
LEGACY_PBI_LOGICAL_DEFINITION_DIR = "pbi/triathlon_pbi_model.SemanticModel/definition"
POC_DIR = PROJECT_ROOT / "semantic_poc"
OUTPUT_DIR = POC_DIR / "output"
SNOWFLAKE_ENVIRONMENT = POC_DIR / "config" / "snowflake_environment.yml"

DBT_OUTPUT = OUTPUT_DIR / "dbt_semantics.json"
POWERBI_OUTPUT = OUTPUT_DIR / "powerbi_semantics.json"
POWERBI_PATCH_OUTPUT = OUTPUT_DIR / "proposed_powerbi_patch.json"
SNOWFLAKE_OUTPUT = OUTPUT_DIR / "snowflake_semantic_view.yml"
COMPATIBILITY_OUTPUT = OUTPUT_DIR / "semantic_compatibility.md"
SNOWFLAKE_VERIFICATION_JSON = OUTPUT_DIR / "snowflake_verification.json"
SNOWFLAKE_VERIFICATION_MD = OUTPUT_DIR / "snowflake_verification.md"

GENERATED_NOTICE = "Generated file. Do not edit manually."
CANONICAL_SOURCE = "models/semantic/triathlon_semantic.yml"

REQUIRED_PUBLIC_METRICS = [
    "valid_sbr_finishers",
    "event_context_rows",
    "event_context_rate",
    "record_integrity_rate",
    "individual_profile_rate",
    "model_residual_rate",
    "individual_hard_flag_rate",
]

STATUS_MATCH = "MATCH"
STATUS_METADATA_DRIFT = "METADATA_DRIFT"
STATUS_DEFINITION_DRIFT = "DEFINITION_DRIFT"
STATUS_MISSING_IN_DBT = "MISSING_IN_DBT"
STATUS_MISSING_IN_POWER_BI = "MISSING_IN_POWER_BI"
STATUS_MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
STATUS_UNSUPPORTED_IN_SNOWFLAKE = "UNSUPPORTED_IN_SNOWFLAKE_GENERATOR"

CORE_DIMENSION_HINTS = {
    "race_name": {"table": "events", "expr": "race_name", "data_type": "VARCHAR"},
    "event_year": {"table": "events", "expr": "year", "data_type": "NUMBER", "kind": "time"},
    "distance": {"table": "distances", "expr": "distance", "data_type": "VARCHAR"},
    "gender": {"table": "genders", "expr": "gender", "data_type": "VARCHAR"},
    "division": {"table": "divisions", "expr": "division", "data_type": "VARCHAR"},
    "is_pro": {"table": "divisions", "expr": "is_pro", "data_type": "BOOLEAN"},
}

KEY_DIMENSIONS = {
    "results": ["result_id", "event_id", "division_id", "gender_id", "distance_id"],
    "events": ["event_id"],
    "divisions": ["division_id"],
    "genders": ["gender_id"],
    "distances": ["distance_id"],
}

BOOLEAN_FACT_TYPES = {
    "is_valid_sbr_finisher": "BOOLEAN",
    "event_context_flag": "BOOLEAN",
    "record_integrity_flag": "BOOLEAN",
    "individual_profile_flag": "BOOLEAN",
    "model_residual_flag": "BOOLEAN",
    "individual_hard_flag": "BOOLEAN",
    "any_review_flag": "BOOLEAN",
}


def relative_posix(path: Path, root: Path = PROJECT_ROOT) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return path.resolve().as_posix()


def generated_metadata(canonical_source: str = CANONICAL_SOURCE) -> dict[str, str]:
    return {
        "notice": GENERATED_NOTICE,
        "canonical_source": canonical_source,
    }


def with_generated_metadata(data: Any, canonical_source: str = CANONICAL_SOURCE) -> Any:
    if not isinstance(data, dict):
        return data
    body = dict(data)
    body.pop("_generated", None)
    return {"_generated": generated_metadata(canonical_source), **body}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any, *, generated: bool = False, canonical_source: str = CANONICAL_SOURCE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = with_generated_metadata(data, canonical_source) if generated else data
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_yaml(path: Path, data: Any, *, generated: bool = False, canonical_source: str = CANONICAL_SOURCE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        if generated:
            handle.write(f"# {GENERATED_NOTICE}\n")
            handle.write(f"# Canonical source: {canonical_source}\n")
        yaml.safe_dump(data, handle, sort_keys=False)


def normalize_snowflake_environment(environment: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(environment or {})
    if normalized.get("mart_schema") is None and normalized.get("schema") is not None:
        normalized["mart_schema"] = normalized["schema"]
    if normalized.get("semantic_schema") is None and normalized.get("semantic_view_schema") is not None:
        normalized["semantic_schema"] = normalized["semantic_view_schema"]
    if normalized.get("schema") is None and normalized.get("mart_schema") is not None:
        normalized["schema"] = normalized["mart_schema"]
    if normalized.get("semantic_view_schema") is None and normalized.get("semantic_schema") is not None:
        normalized["semantic_view_schema"] = normalized["semantic_schema"]
    return normalized


def metric_ref_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("name")
    return None


def clean_tmdl_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def clean_tmdl_identifier(value: str) -> str:
    value = value.strip()
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_expression(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def split_table_column(value: str) -> dict[str, str]:
    table, column = value.split(".", 1)
    return {"table": clean_tmdl_identifier(table), "column": clean_tmdl_identifier(column)}


def get_metric_meta_map(dbt_yaml: dict[str, Any]) -> dict[str, dict[str, Any]]:
    meta_by_metric: dict[str, dict[str, Any]] = {}
    for metric in dbt_yaml.get("metrics", []):
        meta = metric.get("config", {}).get("meta", {}) or {}
        meta_by_metric[metric["name"]] = meta
    return meta_by_metric


def get_metric_yaml_order(dbt_yaml: dict[str, Any]) -> list[str]:
    return [metric["name"] for metric in dbt_yaml.get("metrics", [])]


def get_semantic_model_contract(dbt_yaml: dict[str, Any]) -> dict[str, Any]:
    semantic_models = dbt_yaml.get("semantic_models", [])
    if not semantic_models:
        return {}
    return semantic_models[0].get("config", {}).get("meta", {}).get("semantic_contract", {}) or {}


def get_semantic_model_dimensions(semantic_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    semantic_models = semantic_manifest.get("semantic_models", [])
    if not semantic_models:
        return []
    return semantic_models[0].get("dimensions", [])


def get_measures_by_name(semantic_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    measures: dict[str, dict[str, Any]] = {}
    for semantic_model in semantic_manifest.get("semantic_models", []):
        for measure in semantic_model.get("measures", []):
            enriched = dict(measure)
            enriched["source_model"] = semantic_model.get("name")
            measures[measure["name"]] = enriched
    return measures


def public_metric_names(dbt_yaml: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for metric in dbt_yaml.get("metrics", []):
        semantic_meta = (
            metric.get("config", {})
            .get("meta", {})
            .get("semantic_contract", {})
            or {}
        )
        if semantic_meta.get("public") is True:
            names.append(metric["name"])
    return names


def parse_boolean_filter_column(expr: str | None) -> str | None:
    if not expr:
        return None
    match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(1|true|TRUE\(\))\s*$", expr)
    if match:
        return match.group(1)
    return None


def metric_translation_pattern(metric_type: str, measure: dict[str, Any] | None, numerator: str | None, denominator: str | None) -> str:
    if metric_type == "ratio" and numerator and denominator:
        return "ratio"
    if metric_type == "simple" and measure:
        agg = measure.get("agg")
        expr = normalize_expression(measure.get("expr"))
        if agg == "sum_boolean" and parse_boolean_filter_column(measure.get("expr")):
            return "filtered_count"
        if agg in {"sum", "count"} and expr in {"1", "*"}:
            return "basic_count"
    return STATUS_MANUAL_REVIEW_REQUIRED


def normalize_metric(
    metric: dict[str, Any],
    meta: dict[str, Any],
    measures_by_name: dict[str, dict[str, Any]],
    dimensions: list[dict[str, Any]],
    is_public: bool,
    metric_ir: SemanticMetricIR | None = None,
) -> dict[str, Any]:
    type_params = metric.get("type_params", {}) or {}
    measure_name = metric_ref_name(type_params.get("measure"))
    numerator = metric_ref_name(type_params.get("numerator"))
    denominator = metric_ref_name(type_params.get("denominator"))
    measure = measures_by_name.get(measure_name or "")
    pattern = metric_translation_pattern(metric.get("type"), measure, numerator, denominator)
    if metric_ir is not None:
        pattern = {
            MetricPattern.COUNT: "basic_count",
            MetricPattern.FILTERED_COUNT: "filtered_count",
            MetricPattern.RATIO: "ratio",
        }.get(metric_ir.pattern, STATUS_MANUAL_REVIEW_REQUIRED)
    semantic_meta = meta.get("semantic_contract", {}) or {}

    return {
        "name": metric["name"],
        "label": metric.get("label") or metric["name"],
        "description": metric.get("description") or "",
        "type": metric.get("type"),
        "measure": measure_name,
        "numerator": numerator,
        "denominator": denominator,
        "measure_agg": measure.get("agg") if measure else None,
        "measure_expression": measure.get("expr") if measure else None,
        "filter_column": (
            metric_ir.filters[0].field
            if metric_ir is not None and len(metric_ir.filters) == 1 and metric_ir.filters[0].value is True
            else parse_boolean_filter_column(measure.get("expr") if measure else None)
        ),
        "source_model": (
            metric_ir.source_semantic_model
            if metric_ir is not None and metric.get("type") == "simple"
            else measure.get("source_model") if measure else None
        ),
        "dimensions": [dimension.get("name") for dimension in dimensions],
        "format": semantic_meta.get("format"),
        "caveat": semantic_meta.get("caveat"),
        "public": is_public,
        "translation_pattern": pattern,
        "snowflake_supported": (
            metric_ir.support is SupportClassification.SUPPORTED_PATTERN
            if metric_ir is not None
            else pattern in {"filtered_count", "basic_count", "ratio"}
        ),
        "power_bi": meta.get("power_bi", {}) or {},
        "snowflake": meta.get("snowflake", {}) or {},
        "meta": meta,
    }


def normalize_dbt_semantics(
    semantic_manifest: dict[str, Any],
    dbt_yaml: dict[str, Any],
    required_metrics: list[str] | None = None,
    semantic_yaml_path: Path = DBT_SEMANTIC_YAML,
    semantic_manifest_path: Path = DBT_SEMANTIC_MANIFEST,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    required_metrics = required_metrics or REQUIRED_PUBLIC_METRICS
    metrics_by_name = {metric["name"]: metric for metric in semantic_manifest.get("metrics", [])}
    meta_by_metric = get_metric_meta_map(dbt_yaml)
    yaml_order = get_metric_yaml_order(dbt_yaml)
    dimensions = get_semantic_model_dimensions(semantic_manifest)
    measures_by_name = get_measures_by_name(semantic_manifest)
    public_names = public_metric_names(dbt_yaml)
    canonical_source = relative_posix(semantic_yaml_path, project_root)
    metric_ir_index = build_metric_ir_index(
        semantic_manifest,
        dbt_yaml,
        canonical_source=canonical_source,
    )

    missing_after_parse = [name for name in required_metrics if name not in metrics_by_name]
    if missing_after_parse:
        raise ValueError(f"Missing expected metrics after dbt parse: {', '.join(missing_after_parse)}")

    missing_public_meta = [name for name in required_metrics if name not in public_names]
    if missing_public_meta:
        raise ValueError(f"Expected MVP metrics are not marked public in dbt meta: {', '.join(missing_public_meta)}")

    missing_public_after_parse = [name for name in public_names if name not in metrics_by_name]
    if missing_public_after_parse:
        raise ValueError(f"Public dbt metrics are missing after parse: {', '.join(missing_public_after_parse)}")

    support_names: set[str] = set()
    for name in public_names:
        metric = metrics_by_name[name]
        type_params = metric.get("type_params", {}) or {}
        for ref in (metric_ref_name(type_params.get("numerator")), metric_ref_name(type_params.get("denominator"))):
            if ref and ref in metrics_by_name and ref not in public_names:
                support_names.add(ref)

    metric_names = [name for name in yaml_order if name in public_names or name in support_names]
    metrics = {
        name: normalize_metric(
            metrics_by_name[name],
            meta_by_metric.get(name, {}),
            measures_by_name,
            dimensions,
            name in public_names,
            metric_ir_index.get(name),
        )
        for name in metric_names
    }

    semantic_model = semantic_manifest.get("semantic_models", [{}])[0] if semantic_manifest.get("semantic_models") else {}
    contract_meta = get_semantic_model_contract(dbt_yaml)
    return {
        "canonical_source": canonical_source,
        "compiled_source": relative_posix(semantic_manifest_path, project_root),
        "semantic_model": {
            "name": semantic_model.get("name"),
            "description": semantic_model.get("description"),
            "dimensions": dimensions,
            "logical_tables": contract_meta.get("snowflake_logical_tables", []),
            "relationships": contract_meta.get("relationships", []),
        },
        "public_metrics": public_names,
        "required_mvp_metrics": required_metrics,
        "metrics": metrics,
    }


def load_normalized_dbt_semantics(
    manifest_path: Path = DBT_SEMANTIC_MANIFEST,
    yaml_path: Path = DBT_SEMANTIC_YAML,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} does not exist. Run dbt parse first.")
    return normalize_dbt_semantics(
        load_json(manifest_path),
        load_yaml(yaml_path),
        semantic_yaml_path=yaml_path,
        semantic_manifest_path=manifest_path,
        project_root=project_root,
    )


def validate_dbt_semantics(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    metrics = data.get("metrics", {})
    for name in data.get("public_metrics", []):
        metric = metrics.get(name)
        if not metric:
            errors.append(f"Public metric {name} is missing from normalized metrics.")
            continue
        if not metric.get("power_bi", {}).get("table") or not metric.get("power_bi", {}).get("measure"):
            errors.append(f"Public metric {name} is missing Power BI table or measure mapping.")
        if not metric.get("snowflake", {}).get("logical_table") or not metric.get("snowflake", {}).get("metric_name"):
            errors.append(f"Public metric {name} is missing Snowflake logical_table or metric_name.")
        if metric.get("type") == "ratio":
            if not metric.get("numerator") or not metric.get("denominator"):
                errors.append(f"Rate metric {name} is missing numerator or denominator.")
            if "percentage" not in str(metric.get("format") or ""):
                errors.append(f"Rate metric {name} is not marked with a percentage format.")
    return errors


def parse_tmdl_definition(definition_dir: Path = PBI_DEFINITION_DIR, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    table_dir = definition_dir / "tables"
    for path in sorted(table_dir.glob("*.tmdl")):
        parsed = parse_tmdl_table(path)
        tables[parsed["name"]] = parsed
    return {
        "definition_dir": (
            LEGACY_PBI_LOGICAL_DEFINITION_DIR
            if definition_dir.resolve() == PBI_DEFINITION_DIR.resolve()
            else relative_posix(definition_dir, project_root)
        ),
        "tables": tables,
        "relationships": parse_tmdl_relationships(definition_dir / "relationships.tmdl"),
    }


def parse_tmdl_table(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    table: dict[str, Any] = {"name": path.stem, "description": "", "lineage_tag": None, "measures": {}, "columns": {}}
    pending_comments: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if stripped.startswith("///"):
            pending_comments.append(stripped[3:].strip())
            i += 1
            continue
        table_match = re.match(r"^table\s+(.+)$", line)
        if table_match:
            table["name"] = clean_tmdl_identifier(table_match.group(1))
            table["description"] = normalize_text(" ".join(pending_comments))
            pending_comments = []
            i += 1
            continue
        if stripped.startswith("lineageTag:") and line.startswith("\t"):
            table["lineage_tag"] = stripped.split(":", 1)[1].strip()
            i += 1
            continue
        measure_match = re.match(r"^\tmeasure\s+(.+?)\s*=\s*(.*)$", line)
        if measure_match:
            measure, i = parse_measure_block(lines, i, normalize_text(" ".join(pending_comments)))
            table["measures"][measure["name"]] = measure
            pending_comments = []
            continue
        column_match = re.match(r"^\tcolumn\s+(.+)$", line)
        if column_match:
            column, i = parse_column_block(lines, i, normalize_text(" ".join(pending_comments)))
            table["columns"][column["name"]] = column
            pending_comments = []
            continue
        if stripped:
            pending_comments = []
        i += 1
    return table


def parse_measure_block(lines: list[str], start: int, description: str) -> tuple[dict[str, Any], int]:
    first = lines[start].rstrip()
    match = re.match(r"^\tmeasure\s+(.+?)\s*=\s*(.*)$", first)
    if not match:
        raise ValueError(f"Invalid measure line: {first}")
    expression_parts = [match.group(2).strip()] if match.group(2).strip() else []
    measure = {
        "name": clean_tmdl_identifier(match.group(1)),
        "description": description,
        "expression": "",
        "format_string": None,
        "display_folder": None,
        "lineage_tag": None,
    }
    i = start + 1
    while i < len(lines):
        line = lines[i].rstrip()
        if line.strip().startswith("///"):
            break
        if re.match(r"^\t(measure|column|partition)\s+", line):
            break
        stripped = line.strip()
        if line.startswith("\t\t\t") and stripped:
            expression_parts.append(stripped)
        elif stripped.startswith("formatString:"):
            measure["format_string"] = clean_tmdl_value(stripped.split(":", 1)[1])
        elif stripped.startswith("displayFolder:"):
            measure["display_folder"] = clean_tmdl_value(stripped.split(":", 1)[1])
        elif stripped.startswith("lineageTag:"):
            measure["lineage_tag"] = stripped.split(":", 1)[1].strip()
        i += 1
    measure["expression"] = normalize_text(" ".join(expression_parts))
    return measure, i


def parse_column_block(lines: list[str], start: int, description: str) -> tuple[dict[str, Any], int]:
    first = lines[start].rstrip()
    match = re.match(r"^\tcolumn\s+(.+)$", first)
    if not match:
        raise ValueError(f"Invalid column line: {first}")
    column = {
        "name": clean_tmdl_identifier(match.group(1)),
        "description": description,
        "data_type": None,
        "format_string": None,
        "is_hidden": False,
        "lineage_tag": None,
        "source_column": None,
    }
    i = start + 1
    while i < len(lines):
        line = lines[i].rstrip()
        if line.strip().startswith("///"):
            break
        if re.match(r"^\t(measure|column|partition)\s+", line):
            break
        stripped = line.strip()
        if stripped.startswith("dataType:"):
            column["data_type"] = clean_tmdl_value(stripped.split(":", 1)[1])
        elif stripped == "isHidden":
            column["is_hidden"] = True
        elif stripped.startswith("formatString:"):
            column["format_string"] = clean_tmdl_value(stripped.split(":", 1)[1])
        elif stripped.startswith("lineageTag:"):
            column["lineage_tag"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("sourceColumn:"):
            column["source_column"] = clean_tmdl_value(stripped.split(":", 1)[1])
        i += 1
    return column, i


def parse_tmdl_relationships(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    relationships: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        match = re.match(r"^relationship\s+(.+)$", stripped)
        if match:
            if current:
                relationships.append(current)
            current = {"name": match.group(1), "from": None, "to": None}
        elif current and stripped.startswith("fromColumn:"):
            current["from"] = split_table_column(stripped.split(":", 1)[1].strip())
        elif current and stripped.startswith("toColumn:"):
            current["to"] = split_table_column(stripped.split(":", 1)[1].strip())
    if current:
        relationships.append(current)
    return relationships


def find_powerbi_measure(powerbi: dict[str, Any], table_name: str, measure_name: str) -> dict[str, Any] | None:
    return powerbi.get("tables", {}).get(table_name, {}).get("measures", {}).get(measure_name)


def load_default_metric_ir_index(dbt_semantics: dict[str, Any]) -> dict[str, SemanticMetricIR]:
    if dbt_semantics.get("canonical_source") != CANONICAL_SOURCE:
        return {}
    if not DBT_SEMANTIC_MANIFEST.is_file() or not DBT_SEMANTIC_YAML.is_file():
        return {}
    try:
        return build_metric_ir_index(
            load_json(DBT_SEMANTIC_MANIFEST),
            load_yaml(DBT_SEMANTIC_YAML),
            canonical_source=CANONICAL_SOURCE,
        )
    except (KeyError, TypeError, ValueError):
        return {}


def expected_dax(metric: dict[str, Any], all_metrics: dict[str, dict[str, Any]]) -> str | None:
    ir_index = load_default_metric_ir_index(
        {"canonical_source": CANONICAL_SOURCE, "metrics": all_metrics}
    )
    metric_ir = ir_index.get(metric.get("name") or "")
    if metric_ir is not None:
        generated = generate_dax_definition(metric_ir, ir_index)
        if generated.support is SupportClassification.SUPPORTED_PATTERN:
            return generated.definition
    pattern = metric.get("translation_pattern")
    if pattern == "filtered_count" and metric.get("filter_column"):
        return f"CALCULATE( COUNTROWS(fct_result), fct_result[{metric['filter_column']}] = TRUE() )"
    if pattern == "basic_count":
        return "COUNTROWS(fct_result)"
    if pattern == "ratio":
        numerator = all_metrics.get(metric.get("numerator") or "", {})
        denominator = all_metrics.get(metric.get("denominator") or "", {})
        if numerator and denominator:
            return f"DIVIDE( [{numerator.get('label')}], [{denominator.get('label')}] )"
    return None


def powerbi_formula_matches(metric: dict[str, Any], measure: dict[str, Any] | None, all_metrics: dict[str, dict[str, Any]]) -> bool | None:
    if not measure:
        return None
    expected = expected_dax(metric, all_metrics)
    if expected is None:
        return None
    return normalize_expression(expected) == normalize_expression(measure.get("expression"))


def generate_powerbi_patch(dbt_semantics: dict[str, Any], powerbi: dict[str, Any]) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    for name in dbt_semantics.get("public_metrics", []):
        metric = dbt_semantics["metrics"][name]
        mapping = metric.get("power_bi", {})
        table = mapping.get("table")
        measure_name = mapping.get("measure")
        measure = find_powerbi_measure(powerbi, table, measure_name) if table and measure_name else None
        if not measure:
            continue
        expected_description = metric.get("description")
        if expected_description and not normalize_text(measure.get("description")):
            operations.append(
                {
                    "operation": "set_measure_description",
                    "table": table,
                    "measure": measure_name,
                    "current": measure.get("description") or "",
                    "proposed": expected_description,
                    "source": name,
                }
            )
        expected_format = mapping.get("format_string")
        if expected_format is not None and measure.get("format_string") != expected_format:
            operations.append(
                {
                    "operation": "set_measure_format",
                    "table": table,
                    "measure": measure_name,
                    "current": measure.get("format_string"),
                    "proposed": expected_format,
                    "source": name,
                }
            )
        expected_folder = mapping.get("display_folder")
        if expected_folder is not None and measure.get("display_folder") != expected_folder:
            operations.append(
                {
                    "operation": "set_measure_display_folder",
                    "table": table,
                    "measure": measure_name,
                    "current": measure.get("display_folder"),
                    "proposed": expected_folder,
                    "source": name,
                }
            )

    tables = powerbi.get("tables", {})
    for logical_table in dbt_semantics.get("semantic_model", {}).get("logical_tables", []):
        table_name = logical_table.get("base_table")
        expected_description = logical_table.get("description")
        table = tables.get(table_name or "")
        if table and expected_description and not normalize_text(table.get("description")):
            operations.append(
                {
                    "operation": "set_table_description",
                    "table": table_name,
                    "current": table.get("description") or "",
                    "proposed": expected_description,
                    "source": logical_table.get("name"),
                }
            )

    column_locations: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for table_name, table in tables.items():
        for column_name, column in table.get("columns", {}).items():
            column_locations.setdefault(column_name, []).append((table_name, column))
    for dimension in dbt_semantics.get("semantic_model", {}).get("dimensions", []):
        expected_description = dimension.get("description")
        column_name = dimension.get("expr") or dimension.get("name")
        locations = column_locations.get(column_name or "", [])
        if expected_description and len(locations) == 1:
            table_name, column = locations[0]
            if not normalize_text(column.get("description")):
                operations.append(
                    {
                        "operation": "set_column_description",
                        "table": table_name,
                        "column": column_name,
                        "current": column.get("description") or "",
                        "proposed": expected_description,
                        "source": dimension.get("name"),
                    }
                )

    pbi_relationships = {
        item
        for item in (relationship_signature_from_powerbi(rel) for rel in powerbi.get("relationships", []))
        if item is not None
    }
    skipped = []
    for relationship in dbt_semantics.get("semantic_model", {}).get("relationships", []):
        signature = relationship_signature_from_dbt(relationship)
        if signature not in pbi_relationships:
            skipped.append(
                {
                    "item": f"{signature[0]}.{signature[1]} -> {signature[2]}.{signature[3]}",
                    "reason": "structural changes are outside the safe patch scope",
                    "source": relationship.get("name"),
                }
            )

    return {
        "source": dbt_semantics.get("canonical_source"),
        "target": powerbi.get("definition_dir"),
        "read_only": True,
        "allowed_operations": [
            "set_measure_description",
            "set_measure_format",
            "set_measure_display_folder",
            "set_table_description",
            "set_column_description",
            "set_column_hidden",
        ],
        "operations": operations,
        "skipped": skipped,
    }


def build_snowflake_semantic_view(
    dbt_semantics: dict[str, Any],
    environment: dict[str, Any],
    *,
    metric_ir_index: dict[str, SemanticMetricIR] | None = None,
) -> dict[str, Any]:
    environment = normalize_snowflake_environment(environment)
    logical_tables = dbt_semantics.get("semantic_model", {}).get("logical_tables", [])
    table_by_name = {table["name"]: table for table in logical_tables}
    base_to_logical = {table["base_table"]: table["name"] for table in logical_tables}
    tables: list[dict[str, Any]] = []

    for table in logical_tables:
        logical_name = table["name"]
        table_entry: dict[str, Any] = {
            "name": logical_name,
            "description": table.get("description"),
            "base_table": {
                "database": environment["database"],
                "schema": environment["mart_schema"],
                "table": table["base_table"].upper(),
            },
            "dimensions": [],
        }
        for key in KEY_DIMENSIONS.get(logical_name, []):
            table_entry["dimensions"].append({"name": key, "expr": key, "data_type": "VARCHAR"})
        for dimension_name, hint in CORE_DIMENSION_HINTS.items():
            if hint["table"] != logical_name:
                continue
            target_collection = "time_dimensions" if hint.get("kind") == "time" else "dimensions"
            table_entry.setdefault(target_collection, []).append(
                {
                    "name": dimension_name,
                    "expr": hint["expr"],
                    "data_type": hint["data_type"],
                }
            )
        if logical_name == "results":
            table_entry["facts"] = [
                {"name": name, "expr": name, "data_type": data_type, "access_modifier": "private_access"}
                for name, data_type in BOOLEAN_FACT_TYPES.items()
            ]
            table_entry["metrics"] = []
        tables.append(table_entry)

    relationships = []
    for relationship in dbt_semantics.get("semantic_model", {}).get("relationships", []):
        relationships.append(
            {
                "name": relationship["name"],
                "left_table": base_to_logical.get(relationship["from_table"], relationship["from_table"]),
                "right_table": base_to_logical.get(relationship["to_table"], relationship["to_table"]),
                "relationship_columns": [
                    {
                        "left_column": relationship["from_column"],
                        "right_column": relationship["to_column"],
                    }
                ],
            }
        )

    metrics = dbt_semantics.get("metrics", {})
    metric_ir_index = (
        load_default_metric_ir_index(dbt_semantics)
        if metric_ir_index is None
        else dict(metric_ir_index)
    )
    public_names = set(dbt_semantics.get("public_metrics", []))
    derived_metrics: list[dict[str, Any]] = []
    unsupported: list[dict[str, str]] = []

    def add_table_metric(metric: dict[str, Any], public: bool) -> None:
        logical_table = metric.get("snowflake", {}).get("logical_table") or "results"
        table_entry = next((item for item in tables if item["name"] == logical_table), None)
        if table_entry is None:
            return
        metric_name = metric.get("snowflake", {}).get("metric_name") or metric["name"]
        if any(existing["name"] == metric_name for existing in table_entry.setdefault("metrics", [])):
            return
        metric_ir = metric_ir_index.get(metric["name"])
        generated = generate_snowflake_definition(metric_ir, metric_ir_index) if metric_ir else None
        if generated and generated.support is SupportClassification.SUPPORTED_PATTERN and generated.definition:
            dax = generate_dax_definition(metric_ir, metric_ir_index)
            consistency = validate_cross_target(metric_ir, dax, generated)
            if consistency.valid:
                table_entry["metrics"].append(generated.definition)
                return
            unsupported.append(
                {"metric": metric["name"], "reason": SupportClassification.MANUAL_REVIEW_REQUIRED.value}
            )
            return
        if metric["translation_pattern"] == "filtered_count":
            expr = f"COUNT_IF({metric['filter_column']})"
        elif metric["translation_pattern"] == "basic_count":
            expr = "COUNT(*)"
        else:
            unsupported.append({"metric": metric["name"], "reason": metric["translation_pattern"]})
            return
        table_entry["metrics"].append(
            {
                "name": metric_name,
                "expr": expr,
                "description": metric.get("description"),
                "synonyms": metric.get("snowflake", {}).get("synonyms", []),
                "access_modifier": "public_access" if public else "private_access",
            }
        )

    for name, metric in metrics.items():
        if metric["translation_pattern"] in {"filtered_count", "basic_count"}:
            add_table_metric(metric, name in public_names)

    for name in dbt_semantics.get("public_metrics", []):
        metric = metrics[name]
        if metric["translation_pattern"] != "ratio":
            if not metric.get("snowflake_supported"):
                unsupported.append({"metric": name, "reason": metric["translation_pattern"]})
            continue
        numerator = metrics.get(metric.get("numerator") or "")
        denominator = metrics.get(metric.get("denominator") or "")
        if not numerator or not denominator:
            unsupported.append({"metric": name, "reason": "missing ratio support metric"})
            continue
        metric_ir = metric_ir_index.get(name)
        generated = generate_snowflake_definition(metric_ir, metric_ir_index) if metric_ir else None
        if generated and generated.support is SupportClassification.SUPPORTED_PATTERN and generated.definition:
            dax = generate_dax_definition(metric_ir, metric_ir_index)
            consistency = validate_cross_target(metric_ir, dax, generated)
            if consistency.valid:
                derived_metrics.append(generated.definition)
                continue
            unsupported.append({"metric": name, "reason": SupportClassification.MANUAL_REVIEW_REQUIRED.value})
            continue
        logical_table = metric.get("snowflake", {}).get("logical_table") or "results"
        numerator_name = numerator.get("snowflake", {}).get("metric_name") or numerator["name"]
        denominator_name = denominator.get("snowflake", {}).get("metric_name") or denominator["name"]
        derived_metrics.append(
            {
                "name": metric.get("snowflake", {}).get("metric_name") or name,
                "expr": f"{logical_table}.{numerator_name} / NULLIF({logical_table}.{denominator_name}, 0)",
                "description": metric.get("description"),
                "synonyms": metric.get("snowflake", {}).get("synonyms", []),
                "access_modifier": "public_access",
            }
        )

    return {
        "name": environment["semantic_view_name"],
        "description": "Generated Snowflake Semantic View YAML from dbt semantic definitions. Review before deployment.",
        "tables": tables,
        "relationships": relationships,
        "metrics": derived_metrics,
        "unsupported_metrics": unsupported,
    }


def all_snowflake_metric_names(view: dict[str, Any]) -> set[str]:
    names = {metric["name"] for metric in view.get("metrics", [])}
    for table in view.get("tables", []):
        names.update(metric["name"] for metric in table.get("metrics", []))
    return names


def relationship_signature_from_dbt(relationship: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        relationship["from_table"],
        relationship["from_column"],
        relationship["to_table"],
        relationship["to_column"],
    )


def relationship_signature_from_powerbi(relationship: dict[str, Any]) -> tuple[str, str, str, str] | None:
    if not relationship.get("from") or not relationship.get("to"):
        return None
    return (
        relationship["from"]["table"],
        relationship["from"]["column"],
        relationship["to"]["table"],
        relationship["to"]["column"],
    )


def compare_semantics(
    dbt_semantics: dict[str, Any],
    powerbi: dict[str, Any],
    snowflake_view: dict[str, Any],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    findings = {
        "power_bi_metadata_drift": [],
        "power_bi_definition_drift": [],
        "relationship_drift": [],
        "unsupported_translation": [],
    }
    snowflake_metric_names = all_snowflake_metric_names(snowflake_view)

    for name in dbt_semantics.get("public_metrics", []):
        metric = dbt_semantics["metrics"].get(name)
        if not metric:
            rows.append({"metric": name, "status": STATUS_MISSING_IN_DBT})
            continue
        mapping = metric.get("power_bi", {})
        pbi_measure = find_powerbi_measure(powerbi, mapping.get("table"), mapping.get("measure"))
        status = STATUS_MATCH
        pbi_summary = "missing"
        if not pbi_measure:
            status = STATUS_MISSING_IN_POWER_BI
        else:
            pbi_summary = pbi_measure.get("expression")
            formula_match = powerbi_formula_matches(metric, pbi_measure, dbt_semantics["metrics"])
            if formula_match is False:
                status = STATUS_DEFINITION_DRIFT
                findings["power_bi_definition_drift"].append(f"{name}: DAX expression differs from the supported dbt pattern.")
            metadata_drifts = []
            if mapping.get("format_string") is not None and pbi_measure.get("format_string") != mapping.get("format_string"):
                metadata_drifts.append(f"format {pbi_measure.get('format_string')!r} -> {mapping.get('format_string')!r}")
            if mapping.get("display_folder") is not None and pbi_measure.get("display_folder") != mapping.get("display_folder"):
                metadata_drifts.append(f"display folder {pbi_measure.get('display_folder')!r} -> {mapping.get('display_folder')!r}")
            if metadata_drifts and status == STATUS_MATCH:
                status = STATUS_METADATA_DRIFT
            for drift in metadata_drifts:
                findings["power_bi_metadata_drift"].append(f"{name}: {drift}.")

        snowflake_name = metric.get("snowflake", {}).get("metric_name") or name
        if not metric.get("snowflake_supported"):
            status = STATUS_UNSUPPORTED_IN_SNOWFLAKE
            findings["unsupported_translation"].append(f"{name}: unsupported dbt metric type or expression.")
        elif snowflake_name not in snowflake_metric_names:
            status = STATUS_UNSUPPORTED_IN_SNOWFLAKE
            findings["unsupported_translation"].append(f"{name}: not generated in Snowflake YAML.")

        rows.append(
            {
                "metric": name,
                "canonical": canonical_definition(metric),
                "power_bi": pbi_summary,
                "snowflake": "generated" if snowflake_name in snowflake_metric_names else "not generated",
                "status": status,
            }
        )

    pbi_relationships = {
        item
        for item in (relationship_signature_from_powerbi(rel) for rel in powerbi.get("relationships", []))
        if item is not None
    }
    for relationship in dbt_semantics.get("semantic_model", {}).get("relationships", []):
        signature = relationship_signature_from_dbt(relationship)
        if signature not in pbi_relationships:
            findings["relationship_drift"].append(
                f"{signature[0]}.{signature[1]} -> {signature[2]}.{signature[3]} is missing in Power BI."
            )

    return {"rows": rows, "findings": findings}


def canonical_definition(metric: dict[str, Any]) -> str:
    if metric.get("translation_pattern") in {"filtered_count", "basic_count"}:
        return f"{metric.get('measure_agg')}({metric.get('measure_expression')})"
    if metric.get("translation_pattern") == "ratio":
        return f"{metric.get('numerator')} / {metric.get('denominator')}"
    return metric.get("translation_pattern") or STATUS_MANUAL_REVIEW_REQUIRED


def render_compatibility_markdown(
    comparison: dict[str, Any],
    canonical_source: str = CANONICAL_SOURCE,
    compiled_source: str = "target/semantic_manifest.json",
) -> str:
    lines = [
        "# Semantic Compatibility Report",
        "",
        GENERATED_NOTICE,
        f"Canonical source: `{canonical_source}` via `{compiled_source}`.",
        "",
        "| Metric | Canonical dbt definition | Actual Power BI implementation | Generated Snowflake implementation | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in comparison["rows"]:
        lines.append(
            f"| `{row['metric']}` | `{row.get('canonical', '')}` | `{row.get('power_bi', '')}` | {row.get('snowflake', '')} | `{row['status']}` |"
        )

    sections = [
        ("Power BI metadata drift", "power_bi_metadata_drift"),
        ("Power BI definition drift", "power_bi_definition_drift"),
        ("Relationship drift", "relationship_drift"),
        ("Unsupported cross-platform translation", "unsupported_translation"),
    ]
    for title, key in sections:
        lines.extend(["", f"## {title}", ""])
        findings = comparison["findings"].get(key, [])
        if findings:
            lines.extend(f"- {finding}" for finding in findings)
        else:
            lines.append("- None.")
    lines.append("")
    return "\n".join(lines)
