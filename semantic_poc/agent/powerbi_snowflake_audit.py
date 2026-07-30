from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping, Sequence

import jsonschema
import yaml

from semantic_poc.powerbi_measure_benchmark import (
    PowerBIBenchmarkError,
    load_measure_spec,
    measure_spec_sha256,
)
from semantic_poc.src.models import DBT_SEMANTIC_YAML, PROJECT_ROOT

from .powerbi_import import (
    PowerBIImportError,
    PowerBIModelInventory,
    PowerBIMeasure,
    analyze_dax_measure,
    extract_powerbi_inventory,
)
from .result_evidence_compare import (
    ResultEvidenceComparisonError,
    values_match,
)


AUDIT_SCHEMA_VERSION = 1
AUDIT_ENGINE_VERSION = "1.0.0"
AUDIT_KIND = "RECONCILE_TARGET_DRIFT"

FIDELITY_STATUSES = frozenset(
    {
        "STRUCTURALLY_EQUIVALENT",
        "CONFIRMED_INCORRECT",
        "POTENTIALLY_INCORRECT",
        "OMITTED",
        "MANUAL_REVIEW_REQUIRED",
        "NOT_EVALUABLE",
    }
)
BEHAVIORAL_STATUSES = frozenset({"PASSED", "FAILED", "NOT_AVAILABLE"})
DETECTION_STATUSES = frozenset(
    {"PROVEN_CAUGHT", "PROVEN_NOT_CAUGHT", "NOT_PROVEN"}
)
OBSERVED_HANDLING = frozenset(
    {"EMITTED", "EMITTED_WITH_CAUTION", "CHANGED", "OMITTED", "REJECTED"}
)
AUTOMATION_DISPOSITIONS = frozenset(
    {"AUTO_CONVERT", "FLAG_SOURCE_DEFECT", "MANUAL_REVIEW_REQUIRED"}
)

_SAFE_OUTPUT_FILES = frozenset(
    {
        "conversion-findings.json",
        "POWERBI_SNOWFLAKE_CONVERSION_FINDINGS.md",
        "snowflake-query-pack.sql",
        "result-evidence.template.json",
    }
)
_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_IDENTIFIER_NORMALIZER = re.compile(r"[^A-Za-z0-9]+")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_PBI_TRIAL_V2_CONFIRMED: Mapping[str, tuple[str, str]] = {
    "Split Coverage Rate": (
        "WRONG_DENOMINATOR_AND_GRAIN",
        "Snowflake divides distinct split result IDs by split rows; Power BI divides by result-grain Result Rows.",
    ),
    "Nominal Bike KM Across Timed Results": (
        "FILTER_AND_GRAIN_LOSS",
        "Snowflake sums the distance dimension and loses both the positive-bike-time population and one-distance-per-result iteration.",
    ),
    "Weighted Bike Speed KM/H": (
        "POPULATION_AND_GRAIN_DRIFT",
        "Snowflake does not preserve the shared positive-bike-time population and aggregates across fact and dimension grains.",
    ),
    "NC - Split-Multiplied Bike Seconds": (
        "FACT_GRAIN_FANOUT_REMOVED",
        "Snowflake emits a result-grain SUM and does not preserve the source split-grain fanout calculation.",
    ),
}
_PBI_TRIAL_V2_POTENTIAL: Mapping[str, tuple[str, str]] = {
    "# Events": (
        "DAX_BLANK_SQL_NULL_RISK",
        "DAX DISTINCTCOUNT and SQL COUNT(DISTINCT ...) can differ for BLANK/NULL and empty contexts.",
    ),
    "Results With Splits": (
        "DAX_BLANK_SQL_NULL_RISK",
        "DAX DISTINCTCOUNT and SQL COUNT(DISTINCT ...) can differ for BLANK/NULL and empty contexts.",
    ),
    "Total Transition Seconds": (
        "DAX_BLANK_SQL_NULL_ARITHMETIC_RISK",
        "DAX blank arithmetic can coerce a missing component to zero while SQL addition propagates NULL.",
    ),
    "Total Recorded Seconds": (
        "DAX_BLANK_SQL_NULL_ARITHMETIC_RISK",
        "DAX blank arithmetic can coerce a missing component to zero while SQL addition propagates NULL.",
    ),
}
_PBI_TRIAL_V2_STRUCTURAL = frozenset(
    {
        "Split Time Seconds",
        "Bike Time Seconds",
        "Swim Time Seconds",
        "Run Time Seconds",
        "Bike Time Hours",
        "Swim Time Hours",
        "Run Time Hours",
        "Average Bike Seconds",
        "Min Bike Seconds",
        "Max Bike Seconds",
        "NC - Bike Time Hours Divisor 60",
        "NC - Event ID Total",
        "NC - Overall Relative Total",
    }
)
_PBI_TRIAL_V2_PROFILE = "pbi_trial_measure_conversion_v2"
_PBI_TRIAL_V2_POWERBI_STRUCTURE_SHA256 = (
    "2a84093d61d2caf24dc96f7cf49a08b86c5e86e3052fb626b881076c415b4d67"
)
_PBI_TRIAL_V2_SNOWFLAKE_STRUCTURE_SHA256 = (
    "bfa4a1192fc55a4f18a1a7850ceca705f632791b2e9d305c06a80a23f426378c"
)
_PBI_TRIAL_V2_TARGET_EXPRESSION_SHA256: Mapping[str, str] = {
    "Split Time Seconds": "1aba40ed23b505ce6a0341ce5b181aa4c444bf06c0acfda0fb9bae4cffa84051",
    "Results With Splits": "c7636139859d19f6695586d1843270d5632ba0ad7b78cc8a5df970039380cf9d",
    "# Events": "9f28d3850c0b2a5733963a56a915d6825f9e36c7b9e79e5e9b990df1ca2db3ad",
    "Swim Time Seconds": "9d3732634b6fbaec1162bf204a500c37406968b0cafda9457df600b62d8c0743",
    "Bike Time Seconds": "ddebb96ad52fb2f234647639d5180435e48ee1bb5ab2377f7e93c886329a8597",
    "Run Time Seconds": "92d9fca0a9c6ba665f70e78fdbdbb9ec15d12f04af2c6d43b3605d7342653e65",
    "Total Transition Seconds": "791a2e8f581f02ac27790f0bd3cd05b022b61a9f762fff917e84ce1f4fd15cb7",
    "Total Recorded Seconds": "43e4b08f3d6cd70bdccfc8e0fedba793e6f3c8ab8a804f7ba79cf3829b8fdc49",
    "Split Coverage Rate": "551e3ce13c7e299d03de0e41e94114bcc2d3de8eca86305753b0a7e691947357",
    "Average Bike Seconds": "7c4c9cde42c1f049d90816f935a1665144c27f5c99c3bbf58c8b0f19a01b38c8",
    "Min Bike Seconds": "6f710d9ea7e7a080c485c53fd2ad793db8100b5b8e58cebe207cab898e824478",
    "Max Bike Seconds": "775c81aba11e6e1181243ec4099476ce62f75211d674cca1c3888d884906347a",
    "Run Time Hours": "3490d2808f899764c761d298a9c2f584570ed8cf2b86122bf06a128b215e9acb",
    "Bike Time Hours": "8d15ce35a64dad3fe8aea52a65796ad792488e07a70450cdf3ac022c189f348e",
    "Swim Time Hours": "9827ceb7344d282b6e89f25451e61808f54d6f7539f5815b8dccc26f98085591",
    "Nominal Bike KM Across Timed Results": "0eb568eb874e9a1d8e1e35049b6fd3c469f82ae1b6abd7424160bd034116187a",
    "Weighted Bike Speed KM/H": "d3496fa102051f23818a8df9b2c1678bdaf9ab9befbbd034dba3966b1fa49ef3",
    "NC - Bike Time Hours Divisor 60": "7c31c9051858cd4828e1ca629b0b4cf5c02eb1dadaaa5ef113697e7f80c99ed7",
    "NC - Event ID Total": "8c9bc5454571c56e9803a707c997d73e1b243d84dc09b4c89cc729362d3848fb",
    "NC - Overall Relative Total": "c663d25f1feac8b6557e1d53428f85e02871fe588c7c2943aee178a89079431c",
    "NC - Split-Multiplied Bike Seconds": "ddebb96ad52fb2f234647639d5180435e48ee1bb5ab2377f7e93c886329a8597",
}
_PBI_TRIAL_V2_TARGET_TABLE: Mapping[str, str | None] = {
    **{
        name: "FCT_SPLIT"
        for name in (
            "Split Time Seconds",
            "Results With Splits",
            "Split Coverage Rate",
        )
    },
    **{
        name: "FCT_RESULT"
        for name in (
            "# Events",
            "Swim Time Seconds",
            "Bike Time Seconds",
            "Run Time Seconds",
            "Total Transition Seconds",
            "Total Recorded Seconds",
            "Average Bike Seconds",
            "Min Bike Seconds",
            "Max Bike Seconds",
            "Run Time Hours",
            "Bike Time Hours",
            "Swim Time Hours",
            "NC - Bike Time Hours Divisor 60",
            "NC - Event ID Total",
            "NC - Overall Relative Total",
            "NC - Split-Multiplied Bike Seconds",
        )
    },
    "Nominal Bike KM Across Timed Results": "DIM_DISTANCE",
    "Weighted Bike Speed KM/H": None,
}

_SLICE_QUERY_DIMENSION_SETS: Mapping[str, tuple[tuple[str, ...], ...]] = {
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


class PowerBISnowflakeAuditError(RuntimeError):
    """Base error for deterministic Power BI/Snowflake audits."""


class AuditInputError(PowerBISnowflakeAuditError):
    """The requested audit input or evidence is malformed."""


class AuditStaleEvidenceError(PowerBISnowflakeAuditError):
    """Hash-bound diagnostics or result evidence no longer matches the inputs."""


class AuditStateError(PowerBISnowflakeAuditError):
    """The requested output lifecycle operation conflicts with local state."""


@dataclass(frozen=True)
class SnowflakeMetric:
    object_id: str
    table: str | None
    name: str
    expression: str
    description: str
    source_location: str
    raw_metadata: Mapping[str, Any]

    @property
    def qualified_name(self) -> str:
        return f"{self.table}.{self.name}" if self.table else self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "table": self.table,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "expression": self.expression,
            "description": self.description,
            "source_location": self.source_location,
            "metadata_fields": sorted(self.raw_metadata),
        }


@dataclass(frozen=True)
class AuditMeasureFinding:
    case_id: str
    source: Mapping[str, Any]
    canonical: Mapping[str, Any]
    target: Mapping[str, Any] | None
    mapping_method: str
    fidelity_status: str
    behavioral_status: str
    detection_status: str
    observed_handling: str
    automation_disposition: str
    finding_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    rationale: tuple[str, ...]
    dependency_risks: tuple[str, ...]
    metadata_findings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "source": dict(self.source),
            "canonical": dict(self.canonical),
            "target": dict(self.target) if self.target is not None else None,
            "mapping_method": self.mapping_method,
            "fidelity_status": self.fidelity_status,
            "behavioral_status": self.behavioral_status,
            "detection_status": self.detection_status,
            "observed_handling": self.observed_handling,
            "automation_disposition": self.automation_disposition,
            "finding_ids": list(self.finding_ids),
            "reason_codes": list(self.reason_codes),
            "rationale": list(self.rationale),
            "dependency_risks": list(self.dependency_risks),
            "metadata_findings": list(self.metadata_findings),
        }


@dataclass(frozen=True)
class PowerBISnowflakeAudit:
    audit_id: str
    inputs: Mapping[str, Any]
    authority: Mapping[str, Any]
    summary: Mapping[str, Any]
    measures: tuple[AuditMeasureFinding, ...]
    powerbi_inventory: Mapping[str, Any]
    snowflake_inventory: Mapping[str, Any]
    relationship_comparison: Mapping[str, Any]
    runtime_evidence: Mapping[str, Any]
    model_findings: tuple[Mapping[str, Any], ...]

    @property
    def has_blockers(self) -> bool:
        return bool(self.summary["blocker_count"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "engine_version": AUDIT_ENGINE_VERSION,
            "audit_id": self.audit_id,
            "audit_kind": AUDIT_KIND,
            "authority": dict(self.authority),
            "inputs": dict(self.inputs),
            "summary": dict(self.summary),
            "measures": [item.to_dict() for item in self.measures],
            "powerbi_inventory": dict(self.powerbi_inventory),
            "snowflake_inventory": dict(self.snowflake_inventory),
            "relationship_comparison": dict(self.relationship_comparison),
            "runtime_evidence": dict(self.runtime_evidence),
            "model_findings": [dict(item) for item in self.model_findings],
        }


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return prefix + _sha256_bytes(_canonical_json_bytes(parts))[:16]


def normalize_snowflake_identifier(value: str) -> str:
    """Return the only implicit source/target mapping normalization used by the audit."""

    return _IDENTIFIER_NORMALIZER.sub("_", value).strip("_").upper()


def _without_source_locations(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_source_locations(item)
            for key, item in value.items()
            if key not in {"source_location", "definition_path", "source_tree_hash"}
        }
    if isinstance(value, (list, tuple)):
        return [_without_source_locations(item) for item in value]
    return value


def _powerbi_structure_sha256(inventory: PowerBIModelInventory) -> str:
    """Fingerprint non-measure model semantics without paths or source bytes."""

    model = inventory.model.to_dict()
    payload = {
        "model": {
            key: model.get(key)
            for key in (
                "tmdl_model_name",
                "compatibility_level",
                "culture",
            )
        },
        "tables": [item.to_dict() for item in inventory.tables],
        "columns": [item.to_dict() for item in inventory.columns],
        "relationships": [item.to_dict() for item in inventory.relationships],
        "partitions": [item.to_dict() for item in inventory.partitions],
        "hierarchies": [item.to_dict() for item in inventory.hierarchies],
        "calculation_groups": [
            item.to_dict() for item in inventory.calculation_groups
        ],
        "roles": [item.to_dict() for item in inventory.roles],
    }
    return _sha256_bytes(
        _canonical_json_bytes(_without_source_locations(payload))
    )


def powerbi_structure_sha256(inventory: PowerBIModelInventory) -> str:
    """Return the stable, path-free structural fingerprint used by the audit."""

    return _powerbi_structure_sha256(inventory)


def _snowflake_structure_sha256(data: Mapping[str, Any]) -> str:
    """Fingerprint target grain and role mappings without account identifiers."""

    tables: list[dict[str, Any]] = []
    for table_index, raw_table in enumerate(data.get("tables", []) or []):
        if not isinstance(raw_table, Mapping):
            raise AuditInputError(f"Snowflake tables[{table_index}] must be a mapping.")
        table_name = _required_safe_text(
            raw_table.get("name"), f"Snowflake tables[{table_index}].name"
        )
        base_table = raw_table.get("base_table") or {}
        if not isinstance(base_table, Mapping):
            raise AuditInputError(
                f"Snowflake table {table_name} base_table must be a mapping."
            )
        base_table_name = str(base_table.get("table", ""))
        roles: dict[str, list[dict[str, Any]]] = {}
        for role in ("dimensions", "time_dimensions", "facts"):
            raw_objects = raw_table.get(role, []) or []
            if not isinstance(raw_objects, list):
                raise AuditInputError(
                    f"Snowflake table {table_name} {role} must be an array."
                )
            role_records: list[dict[str, Any]] = []
            for object_index, item in enumerate(raw_objects):
                if not isinstance(item, Mapping):
                    raise AuditInputError(
                        f"Snowflake {table_name}.{role}[{object_index}] must be a mapping."
                    )
                role_records.append(
                    {
                        "name": _required_safe_text(
                            item.get("name"),
                            f"Snowflake {table_name}.{role}[{object_index}].name",
                        ),
                        "expression": _required_safe_text(
                            item.get("expr"),
                            f"Snowflake {table_name}.{role}[{object_index}].expr",
                        ),
                        "data_type": item.get("data_type"),
                    }
                )
            roles[role] = sorted(
                role_records, key=lambda item: item["name"].casefold()
            )
        primary_key = raw_table.get("primary_key") or {}
        if not isinstance(primary_key, Mapping):
            raise AuditInputError(
                f"Snowflake table {table_name} primary_key must be a mapping."
            )
        primary_columns = primary_key.get("columns", []) or []
        if not isinstance(primary_columns, list):
            raise AuditInputError(
                f"Snowflake table {table_name} primary-key columns must be an array."
            )
        tables.append(
            {
                "name": table_name,
                "base_table_location_sha256": (
                    _sha256_bytes(
                        _canonical_json_bytes(
                            {
                                "database": str(base_table.get("database", "")),
                                "schema": str(base_table.get("schema", "")),
                                "table": base_table_name,
                            }
                        )
                    )
                    if base_table
                    else None
                ),
                **roles,
                "primary_key_columns": [str(item) for item in primary_columns],
            }
        )

    relationships: list[dict[str, Any]] = []
    for index, raw in enumerate(data.get("relationships", []) or []):
        if not isinstance(raw, Mapping):
            raise AuditInputError(f"Snowflake relationships[{index}] must be a mapping.")
        raw_columns = raw.get("relationship_columns", []) or []
        if not isinstance(raw_columns, list):
            raise AuditInputError(
                f"Snowflake relationships[{index}].relationship_columns must be an array."
            )
        columns: list[dict[str, str]] = []
        for column_index, column in enumerate(raw_columns):
            if not isinstance(column, Mapping):
                raise AuditInputError(
                    "Snowflake relationships"
                    f"[{index}].relationship_columns[{column_index}] must be a mapping."
                )
            columns.append(
                {
                    "left_column": str(column.get("left_column", "")),
                    "right_column": str(column.get("right_column", "")),
                }
            )
        relationships.append(
            {
                "name": str(raw.get("name", "")),
                "left_table": str(raw.get("left_table", "")),
                "right_table": str(raw.get("right_table", "")),
                "relationship_type": str(raw.get("relationship_type", "")),
                "relationship_columns": columns,
            }
        )
    payload = {
        "tables": sorted(tables, key=lambda item: item["name"].casefold()),
        "relationships": sorted(
            relationships,
            key=lambda item: (
                item["name"].casefold(),
                item["left_table"].casefold(),
                item["right_table"].casefold(),
            ),
        ),
    }
    return _sha256_bytes(_canonical_json_bytes(payload))


def _is_junction(path: Path) -> bool:
    predicate = getattr(path, "is_junction", None)
    return bool(predicate()) if callable(predicate) else False


def _require_repository_path(
    path: str | Path,
    repository_root: Path,
    *,
    label: str,
    kind: str,
) -> Path:
    root = repository_root.resolve()
    requested = Path(path)
    if not requested.is_absolute():
        requested = root / requested
    resolved = requested.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AuditInputError(f"{label} must resolve inside {root}.") from exc
    cursor = resolved
    while cursor != root:
        if cursor.is_symlink() or _is_junction(cursor):
            raise AuditInputError(f"{label} cannot traverse a symbolic link or junction.")
        cursor = cursor.parent
    if kind == "file" and not resolved.is_file():
        raise AuditInputError(f"{label} does not exist or is not a file: {resolved}")
    if kind == "directory" and not resolved.is_dir():
        raise AuditInputError(f"{label} does not exist or is not a directory: {resolved}")
    if kind == "existing" and not resolved.exists():
        raise AuditInputError(f"{label} does not exist: {resolved}")
    return resolved


def _read_mapping_file(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    if path.stat().st_size > _MAX_EVIDENCE_BYTES:
        raise AuditInputError(f"{label} exceeds the {_MAX_EVIDENCE_BYTES}-byte limit.")
    try:
        text = path.read_text(encoding="utf-8")
        loaded = (
            json.loads(text)
            if path.suffix.casefold() == ".json"
            else yaml.safe_load(text)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise AuditInputError(f"{label} could not be read or parsed: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise AuditInputError(f"{label} must contain a mapping at its root.")
    return dict(loaded), _sha256_bytes(text.encode("utf-8"))


def _load_snowflake_yaml(path: Path) -> tuple[dict[str, Any], str]:
    data, digest = _read_mapping_file(path, label="Snowflake semantic-view YAML")
    if not isinstance(data.get("tables", []), list):
        raise AuditInputError("Snowflake semantic-view tables must be an array.")
    if not isinstance(data.get("metrics", []), list):
        raise AuditInputError("Snowflake semantic-view top-level metrics must be an array.")
    if not isinstance(data.get("relationships", []), list):
        raise AuditInputError("Snowflake semantic-view relationships must be an array.")
    return data, digest


def _required_safe_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuditInputError(f"{label} must be non-empty text.")
    if any(ord(character) < 32 and character not in "\t\r\n" for character in value):
        raise AuditInputError(f"{label} contains unsafe control characters.")
    return value


def _snowflake_inventory(data: Mapping[str, Any]) -> tuple[
    tuple[SnowflakeMetric, ...], dict[str, Any]
]:
    metrics: list[SnowflakeMetric] = []
    tables_summary: list[dict[str, Any]] = []
    for table_index, raw_table in enumerate(data.get("tables", []) or []):
        if not isinstance(raw_table, Mapping):
            raise AuditInputError(f"Snowflake tables[{table_index}] must be a mapping.")
        table_name = _required_safe_text(
            raw_table.get("name"), f"Snowflake tables[{table_index}].name"
        )
        role_counts: dict[str, int] = {}
        role_objects: dict[str, list[dict[str, Any]]] = {
            "dimensions": [],
            "time_dimensions": [],
            "facts": [],
        }
        for role in ("dimensions", "time_dimensions", "facts", "metrics"):
            raw_objects = raw_table.get(role, []) or []
            if not isinstance(raw_objects, list):
                raise AuditInputError(
                    f"Snowflake table {table_name} {role} must be an array."
                )
            role_counts[role] = len(raw_objects)
            if role != "metrics":
                for object_index, item in enumerate(raw_objects):
                    if not isinstance(item, Mapping):
                        raise AuditInputError(
                            f"Snowflake {table_name}.{role}[{object_index}] must be a mapping."
                        )
                    object_name = _required_safe_text(
                        item.get("name"),
                        f"Snowflake {table_name}.{role}[{object_index}].name",
                    )
                    expression = _required_safe_text(
                        item.get("expr"),
                        f"Snowflake {table_name}.{role}[{object_index}].expr",
                    )
                    role_objects[role].append(
                        {
                            "name": object_name,
                            "expression": expression,
                            "data_type": item.get("data_type"),
                            "description": str(item.get("description", "")),
                            "source_location": (
                                f"tables[{table_index}].{role}[{object_index}]"
                            ),
                            "metadata_fields": sorted(
                                str(key)
                                for key in item
                                if key
                                not in {
                                    "name",
                                    "expr",
                                    "data_type",
                                    "description",
                                }
                            ),
                        }
                    )
                continue
            for metric_index, item in enumerate(raw_objects):
                metrics.append(
                    _parse_snowflake_metric(
                        item,
                        table=table_name,
                        label=f"Snowflake {table_name}.metrics[{metric_index}]",
                        source_location=(
                            f"tables[{table_index}].metrics[{metric_index}]"
                        ),
                    )
                )
        primary_key = raw_table.get("primary_key") or {}
        primary_columns = (
            list(primary_key.get("columns", []) or [])
            if isinstance(primary_key, Mapping)
            else []
        )
        tables_summary.append(
            {
                "name": table_name,
                "dimension_count": role_counts["dimensions"],
                "time_dimension_count": role_counts["time_dimensions"],
                "fact_count": role_counts["facts"],
                "metric_count": role_counts["metrics"],
                "source_location": f"tables[{table_index}]",
                "dimensions": role_objects["dimensions"],
                "time_dimensions": role_objects["time_dimensions"],
                "facts": role_objects["facts"],
                "primary_key_columns": [str(item) for item in primary_columns],
            }
        )
    table_identities = [item["name"].casefold() for item in tables_summary]
    if len(table_identities) != len(set(table_identities)):
        raise AuditInputError(
            "Snowflake logical-table names must be case-insensitively unique."
        )
    for metric_index, item in enumerate(data.get("metrics", []) or []):
        metrics.append(
            _parse_snowflake_metric(
                item,
                table=None,
                label=f"Snowflake metrics[{metric_index}]",
                source_location=f"metrics[{metric_index}]",
            )
        )
    object_ids = [item.object_id for item in metrics]
    if len(object_ids) != len(set(object_ids)):
        raise AuditInputError("Snowflake metric object IDs must be unique.")
    summary = {
        "semantic_view_name": str(data.get("name", "")),
        "tables": sorted(tables_summary, key=lambda item: item["name"].casefold()),
        "metrics": [
            item.to_dict()
            for item in sorted(metrics, key=lambda item: item.qualified_name.casefold())
        ],
        "metric_count": len(metrics),
        "dimension_count": sum(item["dimension_count"] for item in tables_summary),
        "time_dimension_count": sum(
            item["time_dimension_count"] for item in tables_summary
        ),
        "fact_count": sum(item["fact_count"] for item in tables_summary),
        "relationship_count": len(data.get("relationships", []) or []),
    }
    return tuple(metrics), summary


def _parse_snowflake_metric(
    raw: Any, *, table: str | None, label: str, source_location: str
) -> SnowflakeMetric:
    if not isinstance(raw, Mapping):
        raise AuditInputError(f"{label} must be a mapping.")
    name = _required_safe_text(raw.get("name"), f"{label}.name")
    expression = _required_safe_text(raw.get("expr"), f"{label}.expr")
    description = str(raw.get("description", ""))
    object_id = f"snowflake:metric:{table or '_global'}:{name}"
    metadata = {
        str(key): value
        for key, value in raw.items()
        if key not in {"name", "expr", "description"}
    }
    return SnowflakeMetric(
        object_id=object_id,
        table=table,
        name=name,
        expression=expression,
        description=description,
        source_location=source_location,
        raw_metadata=metadata,
    )


def _canonical_index(path: Path | None) -> tuple[dict[str, dict[str, Any]], str | None]:
    if path is None or not path.is_file():
        return {}, None
    data, digest = _read_mapping_file(path, label="Canonical dbt semantic YAML")
    result: dict[str, dict[str, Any]] = {}
    for raw in data.get("metrics", []) or []:
        if not isinstance(raw, Mapping):
            continue
        config = raw.get("config") or {}
        meta = config.get("meta") or {} if isinstance(config, Mapping) else {}
        power_bi = meta.get("power_bi") or {} if isinstance(meta, Mapping) else {}
        if not isinstance(power_bi, Mapping) or not power_bi.get("measure"):
            continue
        snowflake = meta.get("snowflake") or {} if isinstance(meta, Mapping) else {}
        source_name = str(power_bi["measure"])
        source_key = source_name.casefold()
        if source_key in result:
            raise AuditInputError(
                "Canonical dbt semantic YAML maps the same Power BI measure more "
                f"than once: {source_name}"
            )
        result[source_key] = {
            "metric": str(raw.get("name", "")),
            "source_file": path.as_posix(),
            "power_bi_table": power_bi.get("table"),
            "snowflake_logical_table": (
                snowflake.get("logical_table")
                if isinstance(snowflake, Mapping)
                else None
            ),
            "snowflake_metric_name": (
                snowflake.get("metric_name")
                if isinstance(snowflake, Mapping)
                else None
            ),
        }
    return result, digest


def _case_manifest(
    inventory: PowerBIModelInventory, spec: Mapping[str, Any] | None
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    by_name = {item.name.casefold(): item for item in inventory.measures}
    if len(by_name) != len(inventory.measures):
        raise AuditInputError("Power BI measure names must be case-insensitively unique.")
    if spec is None:
        cases = [
            {
                "case_id": _stable_id("src_", measure.table, measure.name),
                "name": measure.name,
                "dax": measure.expression,
                "description": measure.description,
                "display_folder": measure.display_folder,
                "format_string": measure.format_string,
                "source_unit": "NOT_DECLARED",
                "target_unit": "NOT_DECLARED",
                "semantic_status": "UNKNOWN",
                "expected_review_category": "MANUAL_REVIEW_REQUIRED",
                "expected_local_parser_supported": measure.analysis.supported,
                "intentional_defects": [],
                "correction": None,
            }
            for measure in inventory.measures
        ]
        return cases, ("OVERALL",)

    cases = [dict(item) for item in spec["measures"]]
    expected_names = {str(item["name"]).casefold() for item in cases}
    actual_names = set(by_name)
    if expected_names != actual_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise AuditInputError(
            "Benchmark specification does not describe the selected Power BI model "
            f"(missing={missing}, extra={extra})."
        )
    for item in cases:
        measure = by_name[str(item["name"]).casefold()]
        expected = analyze_dax_measure(str(item["dax"])).normalized_expression
        if measure.analysis.normalized_expression != expected:
            raise AuditInputError(
                f"Benchmark DAX does not match the selected model for {measure.name}."
            )
    comparison = spec.get("comparison") or {}
    slices = comparison.get("required_slices") or ["OVERALL"]
    if (
        not isinstance(slices, list)
        or not slices
        or any(str(item) not in _SLICE_QUERY_DIMENSION_SETS for item in slices)
    ):
        raise AuditInputError("Benchmark required_slices contains an unsupported slice.")
    return cases, tuple(str(item) for item in slices)


def _behavioral_slice_scope(
    *,
    benchmark_spec_path: Path | None,
    cases: Sequence[Mapping[str, Any]],
    required_slices: Sequence[str],
    repository_root: Path,
) -> tuple[dict[str, tuple[str, ...]], Path | None, str | None]:
    all_names = {
        str(item["name"]).casefold(): str(item["name"]) for item in cases
    }
    default = {
        name: tuple(required_slices)
        for name in all_names
    }
    if benchmark_spec_path is None:
        return default, None, None
    baseline_path = benchmark_spec_path.parent / "powerbi-baseline.dax"
    if not baseline_path.is_file():
        return default, None, None
    baseline_path = _require_repository_path(
        baseline_path,
        repository_root,
        label="Power BI behavioral baseline",
        kind="file",
    )
    if baseline_path.stat().st_size > _MAX_EVIDENCE_BYTES:
        raise AuditInputError(
            f"Power BI behavioral baseline exceeds the {_MAX_EVIDENCE_BYTES}-byte limit."
        )
    try:
        text = baseline_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AuditInputError(
            f"Power BI behavioral baseline could not be read: {exc}"
        ) from exc
    sections = re.split(r"(?m)^-- Query \d+:[^\n]*\r?\n", text)[1:]
    if len(sections) != len(required_slices):
        raise AuditInputError(
            "Power BI behavioral baseline query count does not match required_slices."
        )
    selected_by_slice: dict[str, set[str]] = {}
    for slice_name, section in zip(required_slices, sections):
        selected: set[str] = set()
        for label, reference in re.findall(
            r'"([^"]+)"\s*,\s*\[([^\]]+)\]', section
        ):
            if label != reference:
                raise AuditInputError(
                    f"Power BI behavioral baseline aliases {label!r} to a different measure."
                )
            key = reference.casefold()
            if key not in all_names:
                raise AuditInputError(
                    f"Power BI behavioral baseline references unknown measure: {reference}"
                )
            selected.add(key)
        if not selected:
            raise AuditInputError(
                f"Power BI behavioral baseline slice {slice_name} has no measures."
            )
        selected_by_slice[str(slice_name)] = selected
    if selected_by_slice.get("OVERALL") != set(all_names):
        raise AuditInputError(
            "Power BI behavioral OVERALL baseline must contain every benchmark measure."
        )
    scope = {
        name: tuple(
            slice_name
            for slice_name in required_slices
            if name in selected_by_slice[str(slice_name)]
        )
        for name in all_names
    }
    return scope, baseline_path, _sha256_file(baseline_path)


def _target_indexes(
    metrics: Sequence[SnowflakeMetric],
) -> tuple[dict[str, list[SnowflakeMetric]], dict[str, list[SnowflakeMetric]]]:
    exact: dict[str, list[SnowflakeMetric]] = defaultdict(list)
    normalized: dict[str, list[SnowflakeMetric]] = defaultdict(list)
    for metric in metrics:
        exact[metric.name.casefold()].append(metric)
        normalized[normalize_snowflake_identifier(metric.name)].append(metric)
    return dict(exact), dict(normalized)


def _map_target(
    measure_name: str,
    *,
    explicit_matches: Sequence[SnowflakeMetric],
    exact: Mapping[str, list[SnowflakeMetric]],
    normalized: Mapping[str, list[SnowflakeMetric]],
    source_normalized_counts: Mapping[str, int],
) -> tuple[SnowflakeMetric | None, str, str | None]:
    explicit = {item.object_id: item for item in explicit_matches}
    if len(explicit) == 1:
        return next(iter(explicit.values())), "EXPLICIT_MAPPING", None
    if len(explicit) > 1:
        return None, "AMBIGUOUS", "DUPLICATE_EXPLICIT_TARGET"
    exact_matches = exact.get(measure_name.casefold(), [])
    if len(exact_matches) == 1:
        return exact_matches[0], "EXACT_NAME", None
    if len(exact_matches) > 1:
        return None, "AMBIGUOUS", "DUPLICATE_EXACT_TARGET"
    key = normalize_snowflake_identifier(measure_name)
    if source_normalized_counts.get(key, 0) != 1:
        return None, "AMBIGUOUS", "SOURCE_NORMALIZATION_COLLISION"
    matches = normalized.get(key, [])
    if len(matches) == 1:
        return matches[0], "UNIQUE_IDENTIFIER_NORMALIZATION", None
    if len(matches) > 1:
        return None, "AMBIGUOUS", "TARGET_NORMALIZATION_COLLISION"
    return None, "NO_MATCH", None


def _explicit_target_matches(
    measure: PowerBIMeasure,
    metrics: Sequence[SnowflakeMetric],
    canonical_index: Mapping[str, Mapping[str, Any]],
) -> tuple[SnowflakeMetric, ...]:
    matches: dict[str, SnowflakeMetric] = {}
    for metric in metrics:
        declared_source = next(
            (
                metric.raw_metadata[key]
                for key in ("source_measure", "power_bi_measure", "source_name")
                if isinstance(metric.raw_metadata.get(key), str)
            ),
            None,
        )
        if (
            isinstance(declared_source, str)
            and declared_source.casefold() == measure.name.casefold()
        ):
            matches[metric.object_id] = metric
    canonical = canonical_index.get(measure.name.casefold()) or {}
    target_name = canonical.get("snowflake_metric_name")
    target_table = canonical.get("snowflake_logical_table")
    if isinstance(target_name, str) and target_name:
        for metric in metrics:
            if metric.name.casefold() != target_name.casefold():
                continue
            if (
                isinstance(target_table, str)
                and target_table
                and (metric.table or "").casefold() != target_table.casefold()
            ):
                continue
            matches[metric.object_id] = metric
    return tuple(
        sorted(matches.values(), key=lambda item: item.qualified_name.casefold())
    )


def _compact_expression(value: str) -> str:
    return re.sub(r"\s+", "", value).upper().replace('"', "")


def _generic_simple_equivalence(
    measure: PowerBIMeasure, target: SnowflakeMetric
) -> tuple[str, str, str]:
    dax = measure.analysis.normalized_expression
    sql = _compact_expression(target.expression)
    target_table = normalize_snowflake_identifier(target.table or "")

    countrows = re.fullmatch(r"COUNTROWS\(([^)]+)\)", dax, flags=re.IGNORECASE)
    if countrows:
        source_table = normalize_snowflake_identifier(countrows.group(1).strip("'"))
        if source_table == target_table and sql == "COUNT(*)":
            return (
                "MANUAL_REVIEW_REQUIRED",
                "GENERIC_STRUCTURE_PROOF_UNAVAILABLE",
                "COUNTROWS and COUNT(*) have the same expression shape, but the "
                "generic audit has no reviewed table-grain fingerprint.",
            )

    aggregate = re.fullmatch(
        r"(SUM|AVERAGE|MIN|MAX)\('?([^'\[]+)'?\[([^\]]+)\]\)",
        dax,
        flags=re.IGNORECASE,
    )
    if aggregate:
        dax_function = aggregate.group(1).upper()
        sql_function = "AVG" if dax_function == "AVERAGE" else dax_function
        table = normalize_snowflake_identifier(aggregate.group(2))
        column = normalize_snowflake_identifier(aggregate.group(3))
        expected = f"{sql_function}({table}.{column})"
        if sql == expected and (not target_table or target_table == table):
            return (
                "MANUAL_REVIEW_REQUIRED",
                "GENERIC_STRUCTURE_PROOF_UNAVAILABLE",
                "The direct aggregate shape matches, but the generic audit cannot "
                "prove base-table, column-type, key, and grain equivalence.",
            )

    distinct = re.fullmatch(
        r"DISTINCTCOUNT\('?([^'\[]+)'?\[([^\]]+)\]\)",
        dax,
        flags=re.IGNORECASE,
    )
    if distinct:
        table = normalize_snowflake_identifier(distinct.group(1))
        column = normalize_snowflake_identifier(distinct.group(2))
        if sql == f"COUNT(DISTINCT{table}.{column})":
            return (
                "POTENTIALLY_INCORRECT",
                "DAX_BLANK_SQL_NULL_RISK",
                "The aggregate shape matches, but BLANK/NULL and empty-context behavior needs differential testing.",
            )

    return (
        "MANUAL_REVIEW_REQUIRED",
        "UNPROVEN_EXPRESSION_EQUIVALENCE",
        "The allowlisted static comparer cannot establish expression and context equivalence.",
    )


def _static_fidelity(
    measure: PowerBIMeasure,
    case: Mapping[str, Any],
    target: SnowflakeMetric | None,
    mapping_error: str | None,
    *,
    benchmark_profile: str | None,
    benchmark_source_structure_matches: bool,
    benchmark_target_structure_matches: bool,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if mapping_error:
        return (
            "MANUAL_REVIEW_REQUIRED",
            (mapping_error,),
            ("Deterministic identifier mapping is ambiguous.",),
        )
    if target is None:
        return (
            "OMITTED",
            ("TARGET_OMISSION",),
            ("No exact or uniquely normalized Snowflake metric was emitted.",),
        )
    if benchmark_profile == _PBI_TRIAL_V2_PROFILE:
        expected_expression_hash = _PBI_TRIAL_V2_TARGET_EXPRESSION_SHA256.get(
            measure.name
        )
        observed_expression_hash = _sha256_bytes(
            _compact_expression(target.expression).encode("utf-8")
        )
        if expected_expression_hash is None:
            return (
                "MANUAL_REVIEW_REQUIRED",
                ("BENCHMARK_TARGET_FINGERPRINT_MISSING",),
                (
                    "The benchmark has no reviewed target-expression fingerprint for this emitted metric.",
                ),
            )
        expected_table = _PBI_TRIAL_V2_TARGET_TABLE.get(measure.name)
        if (
            normalize_snowflake_identifier(target.name)
            != normalize_snowflake_identifier(measure.name)
            or (
                normalize_snowflake_identifier(target.table or "")
                != normalize_snowflake_identifier(expected_table or "")
            )
        ):
            return (
                "MANUAL_REVIEW_REQUIRED",
                ("TARGET_IDENTITY_DRIFT",),
                (
                    "The emitted metric moved or was renamed relative to the reviewed benchmark target identity.",
                ),
            )
        if observed_expression_hash != expected_expression_hash:
            return (
                "MANUAL_REVIEW_REQUIRED",
                ("TARGET_EXPRESSION_DRIFT",),
                (
                    "The emitted target expression changed from the reviewed benchmark fingerprint and must be re-audited.",
                ),
            )
        if not benchmark_source_structure_matches:
            return (
                "MANUAL_REVIEW_REQUIRED",
                ("POWERBI_REVIEWED_STRUCTURE_DRIFT",),
                (
                    "The Power BI table, column, partition, or relationship structure "
                    "changed from the reviewed benchmark profile.",
                ),
            )
        if not benchmark_target_structure_matches:
            return (
                "MANUAL_REVIEW_REQUIRED",
                ("SNOWFLAKE_REVIEWED_STRUCTURE_DRIFT",),
                (
                    "The Snowflake base-table, dimension, fact, type, primary-key, "
                    "or relationship structure changed from the reviewed benchmark profile.",
                ),
            )
        if measure.name in _PBI_TRIAL_V2_CONFIRMED:
            code, rationale = _PBI_TRIAL_V2_CONFIRMED[measure.name]
            return "CONFIRMED_INCORRECT", (code,), (rationale,)
        if measure.name in _PBI_TRIAL_V2_POTENTIAL:
            code, rationale = _PBI_TRIAL_V2_POTENTIAL[measure.name]
            return "POTENTIALLY_INCORRECT", (code,), (rationale,)
        if measure.name in _PBI_TRIAL_V2_STRUCTURAL:
            return (
                "STRUCTURALLY_EQUIVALENT",
                ("ALLOWLISTED_STRUCTURAL_EQUIVALENCE",),
                (
                    "The benchmark's proof-backed source pattern and emitted expression have the same aggregation, dependency, and grain.",
                ),
            )
        return (
            "MANUAL_REVIEW_REQUIRED",
            ("BENCHMARK_EXPECTATION_UNRESOLVED",),
            ("This emitted benchmark pattern is not in the proof-backed conversion subset.",),
        )
    status, code, rationale = _generic_simple_equivalence(measure, target)
    return status, (code,), (rationale,)


def _metadata_findings(
    measure: PowerBIMeasure,
    case: Mapping[str, Any],
    target: SnowflakeMetric | None,
) -> tuple[str, ...]:
    if target is None:
        return ()
    findings: list[str] = []
    metadata = {
        str(key).casefold(): value
        for key, value in target.raw_metadata.items()
    }

    def normalized(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value).strip()).casefold()

    def compare_declared(
        *,
        keys: set[str],
        expected: str | None,
        missing_code: str,
        mismatch_code: str,
    ) -> None:
        if not expected:
            return
        declared = [metadata[key] for key in sorted(keys) if key in metadata]
        if not declared:
            findings.append(missing_code)
        elif any(normalized(value) != normalized(expected) for value in declared):
            findings.append(mismatch_code)

    compare_declared(
        keys={"format", "format_string", "formatstring"},
        expected=measure.format_string,
        missing_code="FORMAT_NOT_PRESERVED",
        mismatch_code="FORMAT_MISMATCH",
    )
    compare_declared(
        keys={"display_folder", "displayfolder"},
        expected=measure.display_folder,
        missing_code="DISPLAY_FOLDER_NOT_PRESERVED",
        mismatch_code="DISPLAY_FOLDER_MISMATCH",
    )
    compare_declared(
        keys={"lineage", "lineage_tag", "lineagetag", "source_lineage"},
        expected=measure.lineage_tag,
        missing_code="LINEAGE_NOT_PRESERVED",
        mismatch_code="LINEAGE_MISMATCH",
    )
    source_unit = str(case.get("source_unit", "NOT_DECLARED"))
    target_unit = str(case.get("target_unit", "NOT_DECLARED"))
    expected_target_unit = (
        target_unit
        if target_unit != "NOT_DECLARED"
        else source_unit
        if source_unit != "NOT_DECLARED"
        else None
    )
    compare_declared(
        keys={"unit", "target_unit"},
        expected=expected_target_unit,
        missing_code="UNIT_METADATA_NOT_PRESERVED",
        mismatch_code="TARGET_UNIT_MISMATCH",
    )
    if "source_unit" in metadata and source_unit != "NOT_DECLARED":
        if normalized(metadata["source_unit"]) != normalized(source_unit):
            findings.append("SOURCE_UNIT_MISMATCH")
    if measure.description and normalized(target.description) != normalized(
        measure.description
    ):
        findings.append("DESCRIPTION_MISMATCH")
    source_description = measure.description.casefold()
    target_description = target.description.casefold()
    if (
        "experimental_benchmark_only" in source_description
        and "experimental_benchmark_only" not in target_description
    ):
        findings.append("EXPERIMENTAL_PROVENANCE_NOT_PRESERVED")
    if (
        "negative control" in source_description
        and "do not use for reporting" in source_description
        and "do not use for reporting" not in target_description
    ):
        findings.append("BLOCKING_SAFETY_LABEL_NOT_PRESERVED")
    return tuple(sorted(findings))


def _load_diagnostics(
    path: Path | None,
    *,
    expected_hashes: Mapping[str, str | None],
    cases_by_name: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, Any]]:
    if path is None:
        return {}, {"status": "NOT_AVAILABLE", "sha256": None, "diagnostic_count": 0}
    data, digest = _read_mapping_file(path, label="Sanitized Snowflake diagnostics")
    if set(data) != {"schema_version", "inputs", "diagnostics"}:
        raise AuditInputError(
            "Snowflake diagnostics fields must be schema_version, inputs, and diagnostics."
        )
    if data["schema_version"] != 1 or not isinstance(data["inputs"], Mapping):
        raise AuditInputError("Snowflake diagnostics schema_version or inputs is invalid.")
    _validate_hash_bindings(
        data["inputs"], expected_hashes, label="Snowflake diagnostics"
    )
    raw_items = data["diagnostics"]
    if not isinstance(raw_items, list):
        raise AuditInputError("Snowflake diagnostics must be an array.")
    rejected: dict[str, set[str]] = defaultdict(set)
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            raise AuditInputError(f"Snowflake diagnostics[{index}] must be a mapping.")
        allowed = {
            "source_measure",
            "defect_code",
            "disposition",
            "diagnostic_id",
            "severity",
            "message",
        }
        if not {"source_measure", "defect_code", "disposition"}.issubset(raw) or not set(
            raw
        ).issubset(allowed):
            raise AuditInputError(
                f"Snowflake diagnostics[{index}] has invalid fields."
            )
        source_name = _required_safe_text(
            raw["source_measure"], f"Snowflake diagnostics[{index}].source_measure"
        )
        case = cases_by_name.get(source_name.casefold())
        if case is None:
            raise AuditInputError(
                f"Snowflake diagnostic references unknown measure: {source_name}"
            )
        defect = _required_safe_text(
            raw["defect_code"], f"Snowflake diagnostics[{index}].defect_code"
        )
        if defect not in case.get("intentional_defects", []):
            raise AuditInputError(
                f"Snowflake diagnostic defect_code is not oracle-backed for {source_name}."
            )
        disposition = str(raw["disposition"]).upper()
        if disposition not in {"REJECTED", "WARNING", "INFORMATIONAL"}:
            raise AuditInputError(
                f"Snowflake diagnostics[{index}].disposition is invalid."
            )
        if disposition == "REJECTED":
            rejected[source_name.casefold()].add(defect)
    return dict(rejected), {
        "status": "AVAILABLE",
        "sha256": digest,
        "diagnostic_count": len(raw_items),
        "defect_specific_rejection_count": sum(len(items) for items in rejected.values()),
    }


def _validate_hash_bindings(
    supplied: Mapping[str, Any],
    expected: Mapping[str, str | None],
    *,
    label: str,
) -> None:
    if set(supplied) != set(expected):
        raise AuditInputError(
            f"{label} input hashes must be exactly: {', '.join(sorted(expected))}."
        )
    for key, expected_value in expected.items():
        value = supplied.get(key)
        if expected_value is None:
            if value is not None:
                raise AuditStaleEvidenceError(
                    f"{label} binds unexpected {key} evidence."
                )
            continue
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise AuditInputError(f"{label} {key} must be a lowercase SHA-256.")
        if value != expected_value:
            raise AuditStaleEvidenceError(
                f"{label} is stale: {key} does not match the current input."
            )


def _coordinate_key(
    raw: Any, label: str, *, slice_name: str
) -> tuple[str, frozenset[str]]:
    if not isinstance(raw, Mapping):
        raise AuditInputError(f"{label} coordinates must be a mapping.")
    coordinates: dict[str, str | int | float | bool | None] = {}
    for key, value in raw.items():
        name = _required_safe_text(key, f"{label} coordinate name")
        if isinstance(value, (dict, list)) or (
            value is not None and not isinstance(value, (str, int, float, bool))
        ):
            raise AuditInputError(
                f"{label} coordinate values must be JSON scalars or null."
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise AuditInputError(f"{label} coordinate values must be finite.")
        coordinates[name] = value
    coordinate_names = frozenset(coordinates)
    expected_groupings = {
        frozenset(grouping)
        for grouping in _SLICE_QUERY_DIMENSION_SETS[slice_name]
    }
    if coordinate_names not in expected_groupings:
        expected = " or ".join(
            "{" + ", ".join(grouping) + "}" if grouping else "{}"
            for grouping in _SLICE_QUERY_DIMENSION_SETS[slice_name]
        )
        raise AuditInputError(
            f"{label} coordinate keys do not match the {slice_name} grouping "
            f"contract; expected {expected}."
        )
    return (
        json.dumps(
            coordinates, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        coordinate_names,
    )


def _endpoint_rows(
    raw: Any, label: str, *, slice_name: str
) -> tuple[bool, dict[str, int | float | None]]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "status",
        "complete",
        "result_sha256",
        "rows",
    }:
        raise AuditInputError(
            f"{label} must contain exactly status, complete, result_sha256, and rows."
        )
    status = str(raw["status"]).upper()
    if status == "NOT_AVAILABLE":
        if (
            raw["complete"] is not False
            or raw["result_sha256"] is not None
            or raw["rows"] != []
        ):
            raise AuditInputError(
                f"{label} NOT_AVAILABLE evidence must be incomplete with no hash or rows."
            )
        return False, {}
    if status != "AVAILABLE":
        raise AuditInputError(f"{label} status must be AVAILABLE or NOT_AVAILABLE.")
    if raw["complete"] is not True or not isinstance(raw["rows"], list):
        raise AuditInputError(
            f"{label} AVAILABLE evidence must be complete and contain a rows array."
        )
    expected_result_hash = _sha256_bytes(
        _canonical_json_bytes(
            {
                "complete": raw["complete"],
                "rows": raw["rows"],
                "status": raw["status"],
            }
        )
    )
    if raw["result_sha256"] != expected_result_hash:
        raise AuditInputError(f"{label} result_sha256 does not match its canonical rows.")
    rows: dict[str, int | float | None] = {}
    observed_groupings: set[frozenset[str]] = set()
    for index, item in enumerate(raw["rows"]):
        if not isinstance(item, Mapping) or set(item) != {"coordinates", "value"}:
            raise AuditInputError(
                f"{label}.rows[{index}] must contain coordinates and value."
            )
        key, grouping = _coordinate_key(
            item["coordinates"],
            f"{label}.rows[{index}]",
            slice_name=slice_name,
        )
        observed_groupings.add(grouping)
        if key in rows:
            raise AuditInputError(f"{label} contains duplicate coordinate rows.")
        value = item["value"]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise AuditInputError(f"{label}.rows[{index}] value must be numeric or null.")
        if isinstance(value, float) and not math.isfinite(value):
            raise AuditInputError(f"{label}.rows[{index}] value must be finite.")
        rows[key] = value
    if not rows:
        raise AuditInputError(
            f"{label} AVAILABLE evidence must contain at least one row."
        )
    if slice_name == "OVERALL" and len(rows) != 1:
        raise AuditInputError("Complete OVERALL evidence requires exactly one row.")
    expected_groupings = {
        frozenset(grouping)
        for grouping in _SLICE_QUERY_DIMENSION_SETS[slice_name]
    }
    if observed_groupings != expected_groupings:
        missing = sorted(
            (
                list(grouping)
                for grouping in expected_groupings - observed_groupings
            ),
            key=lambda item: (len(item), item),
        )
        raise AuditInputError(
            f"{label} complete {slice_name} evidence is missing grouping sets: "
            f"{missing}."
        )
    return True, rows


def _values_match(
    value_type: str,
    left: int | float | None,
    right: int | float | None,
) -> bool:
    try:
        return values_match(value_type, left, right)
    except ResultEvidenceComparisonError as exc:
        raise AuditInputError(str(exc)) from exc


def _load_result_evidence(
    path: Path | None,
    *,
    expected_hashes: Mapping[str, str | None],
    cases_by_name: Mapping[str, Mapping[str, Any]],
    required_slices: Sequence[str],
    required_slices_by_measure: Mapping[str, Sequence[str]],
) -> tuple[dict[str, str], dict[str, Any]]:
    if path is None:
        return (
            {name: "NOT_AVAILABLE" for name in cases_by_name},
            {
                "status": "NOT_AVAILABLE",
                "sha256": None,
                "required_slices": list(required_slices),
                "required_slices_by_measure": {
                    cases_by_name[name]["name"]: list(
                        required_slices_by_measure[name]
                    )
                    for name in sorted(cases_by_name)
                },
                "comparison": {
                    "integer": "EXACT",
                    "decimal_absolute_tolerance": 1e-9,
                    "decimal_relative_tolerance": 1e-9,
                    "blank_null_representation": "JSON_NULL",
                },
                "result_count": 0,
            },
        )
    data, digest = _read_mapping_file(path, label="Runtime result evidence")
    schema_path = Path(__file__).with_name("result_evidence.schema.json")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(data)
    except (OSError, UnicodeError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
        raise AuditInputError(f"Runtime result evidence schema validation failed: {exc}") from exc
    if data["profile"] != "PBI_TRIAL_V2_AUDIT":
        raise AuditInputError("Runtime result evidence profile must be PBI_TRIAL_V2_AUDIT.")
    _validate_hash_bindings(
        data["subject_hashes"],
        expected_hashes,
        label="Runtime result evidence",
    )
    expected_comparison = {
        "integer": "EXACT",
        "decimal_absolute_tolerance": 1e-9,
        "decimal_relative_tolerance": 1e-9,
        "blank_null_representation": "JSON_NULL",
    }
    if data["comparison"] != expected_comparison:
        raise AuditInputError(
            "Runtime result evidence comparison must use exact integers and absolute/relative 1e-9 decimals."
        )
    raw_results = data["results"]
    if not isinstance(raw_results, list):
        raise AuditInputError("Runtime result evidence results must be an array.")
    observed: dict[tuple[str, str], bool | None] = {}
    for index, raw in enumerate(raw_results):
        if not isinstance(raw, Mapping) or set(raw) != {
            "source_measure",
            "slice",
            "value_type",
            "power_bi",
            "snowflake",
        }:
            raise AuditInputError(
                f"Runtime result evidence results[{index}] has invalid fields."
            )
        source_name = _required_safe_text(
            raw["source_measure"], f"results[{index}].source_measure"
        )
        source_key = source_name.casefold()
        if source_key not in cases_by_name:
            raise AuditInputError(
                f"Runtime result evidence references unknown measure: {source_name}"
            )
        slice_name = str(raw["slice"]).upper()
        if slice_name not in required_slices:
            raise AuditInputError(
                f"Runtime result evidence uses undeclared slice: {slice_name}"
            )
        if slice_name not in required_slices_by_measure[source_key]:
            raise AuditInputError(
                f"Runtime result evidence uses slice {slice_name} outside the declared "
                f"benchmark scope for {source_name}."
            )
        key = (source_key, slice_name)
        if key in observed:
            raise AuditInputError(
                f"Duplicate runtime result evidence for {source_name}/{slice_name}."
            )
        value_type = str(raw["value_type"]).upper()
        if value_type not in {"INTEGER", "DECIMAL"}:
            raise AuditInputError(
                "result evidence value_type must be INTEGER or DECIMAL."
            )
        pbi_available, pbi_rows = _endpoint_rows(
            raw["power_bi"],
            f"results[{index}].power_bi",
            slice_name=slice_name,
        )
        snow_available, snow_rows = _endpoint_rows(
            raw["snowflake"],
            f"results[{index}].snowflake",
            slice_name=slice_name,
        )
        if not pbi_available or not snow_available:
            observed[key] = None
        elif set(pbi_rows) != set(snow_rows):
            observed[key] = False
        else:
            observed[key] = all(
                _values_match(value_type, pbi_rows[row_key], snow_rows[row_key])
                for row_key in pbi_rows
            )
    statuses: dict[str, str] = {}
    for source_key in cases_by_name:
        values = [
            observed.get((source_key, item))
            for item in required_slices_by_measure[source_key]
        ]
        if any(item is False for item in values):
            statuses[source_key] = "FAILED"
        elif values and all(item is True for item in values):
            statuses[source_key] = "PASSED"
        else:
            statuses[source_key] = "NOT_AVAILABLE"
    return statuses, {
        "status": "AVAILABLE",
        "sha256": digest,
        "required_slices": list(required_slices),
        "required_slices_by_measure": {
            cases_by_name[name]["name"]: list(required_slices_by_measure[name])
            for name in sorted(cases_by_name)
        },
        "comparison": expected_comparison,
        "result_count": len(raw_results),
        "status_counts": dict(sorted(Counter(statuses.values()).items())),
    }


def _relationship_comparison(
    inventory: PowerBIModelInventory, snowflake: Mapping[str, Any]
) -> dict[str, Any]:
    source_by_signature: dict[tuple[str, str, str, str], Any] = {}
    source_signature_counts: Counter[tuple[str, str, str, str]] = Counter()
    for relationship in inventory.relationships:
        signature = tuple(
            normalize_snowflake_identifier(item)
            for item in (
                relationship.from_table,
                relationship.from_column,
                relationship.to_table,
                relationship.to_column,
            )
        )
        source_signature_counts[signature] += 1
        source_by_signature[signature] = relationship

    target_signatures: dict[tuple[str, str, str, str], str] = {}
    target_records: list[dict[str, Any]] = []
    for index, raw in enumerate(snowflake.get("relationships", []) or []):
        if not isinstance(raw, Mapping):
            raise AuditInputError(f"Snowflake relationships[{index}] must be a mapping.")
        left_table = _required_safe_text(
            raw.get("left_table"), f"Snowflake relationships[{index}].left_table"
        )
        right_table = _required_safe_text(
            raw.get("right_table"), f"Snowflake relationships[{index}].right_table"
        )
        columns = raw.get("relationship_columns", []) or []
        if not isinstance(columns, list) or len(columns) != 1 or not isinstance(
            columns[0], Mapping
        ):
            target_records.append(
                {
                    "name": str(raw.get("name", f"relationship_{index}")),
                    "status": "MANUAL_REVIEW_REQUIRED",
                    "reason": "COMPOSITE_OR_INVALID_RELATIONSHIP",
                    "source_location": f"relationships[{index}]",
                }
            )
            continue
        signature = tuple(
            normalize_snowflake_identifier(str(item))
            for item in (
                left_table,
                columns[0].get("left_column", ""),
                right_table,
                columns[0].get("right_column", ""),
            )
        )
        name = str(raw.get("name", f"relationship_{index}"))
        target_signatures[signature] = name
        source = source_by_signature.get(signature)
        if source is None:
            target_records.append(
                {
                    "name": name,
                    "status": "TARGET_ONLY",
                    "signature": list(signature),
                    "relationship_type": raw.get("relationship_type"),
                    "source_location": f"relationships[{index}]",
                }
            )
            continue
        target_type = str(raw.get("relationship_type", "")).casefold()
        source_type = (
            f"{source.from_cardinality}_to_{source.to_cardinality}".casefold()
        )
        cardinality_matches = target_type == source_type
        active_state_compatible = source.is_active
        cross_filter_direction_compatible = (
            normalize_snowflake_identifier(source.cross_filter_direction)
            in {"ONEDIRECTION", "SINGLE", "ONEWAY"}
        )
        if not cardinality_matches:
            status = "CARDINALITY_MISMATCH"
        elif not active_state_compatible or not cross_filter_direction_compatible:
            status = "RELATIONSHIP_PROPERTY_MISMATCH"
        else:
            status = "ENDPOINTS_MATCH"
        target_records.append(
            {
                "name": name,
                "source_relationship": source.name,
                "status": status,
                "signature": list(signature),
                "relationship_type": raw.get("relationship_type"),
                "source_location": f"relationships[{index}]",
                "power_bi_cardinality": (
                    f"{source.from_cardinality}_to_{source.to_cardinality}"
                ),
                "power_bi_cardinality_explicit": bool(
                    source.from_cardinality_explicit
                    and source.to_cardinality_explicit
                ),
                "cardinality_matches": cardinality_matches,
                "power_bi_is_active": source.is_active,
                "power_bi_cross_filter_direction": source.cross_filter_direction,
                "active_state_compatible": active_state_compatible,
                "cross_filter_direction_compatible": (
                    cross_filter_direction_compatible
                ),
            }
        )
    target_signature_counts = Counter(
        tuple(item["signature"])
        for item in target_records
        if isinstance(item.get("signature"), list)
    )
    for record in target_records:
        signature_value = record.get("signature")
        if (
            isinstance(signature_value, list)
            and target_signature_counts[tuple(signature_value)] > 1
        ):
            record["status"] = "DUPLICATE_TARGET_SIGNATURE"
    source_only = [
        {
            "name": relationship.name,
            "signature": [
                normalize_snowflake_identifier(item)
                for item in (
                    relationship.from_table,
                    relationship.from_column,
                    relationship.to_table,
                    relationship.to_column,
                )
            ],
        }
        for relationship in inventory.relationships
        if tuple(
            normalize_snowflake_identifier(item)
            for item in (
                relationship.from_table,
                relationship.from_column,
                relationship.to_table,
                relationship.to_column,
            )
        )
        not in target_signatures
    ]
    counts = Counter(item["status"] for item in target_records)
    source_duplicates = [
        list(signature)
        for signature, count in sorted(source_signature_counts.items())
        if count > 1
    ]
    target_duplicates = [
        list(signature)
        for signature, count in sorted(target_signature_counts.items())
        if count > 1
    ]
    return {
        "source_relationship_count": len(inventory.relationships),
        "target_relationship_count": len(snowflake.get("relationships", []) or []),
        "endpoint_match_count": counts["ENDPOINTS_MATCH"],
        "cardinality_mismatch_count": counts["CARDINALITY_MISMATCH"],
        "property_mismatch_count": counts["RELATIONSHIP_PROPERTY_MISMATCH"],
        "source_only": source_only,
        "source_duplicate_signatures": source_duplicates,
        "target_duplicate_signatures": target_duplicates,
        "target_records": sorted(target_records, key=lambda item: item["name"].casefold()),
        "unrepresented_power_bi_properties": [
            "explicit_active_state_provenance",
            "explicit_cross_filter_direction_provenance",
            "explicit_cardinality_provenance",
        ],
    }


def _canonical_source(
    measure: PowerBIMeasure,
    canonical_index: Mapping[str, Mapping[str, Any]],
    canonical_source_file: str | None,
) -> dict[str, Any]:
    mapped = canonical_index.get(measure.name.casefold())
    return {
        "metric": mapped.get("metric") if mapped else None,
        "resolution_status": "EXACT_MAPPING" if mapped else "UNRESOLVED_BENCHMARK_EVIDENCE",
        "source_file": canonical_source_file,
    }


def _source_record(
    measure: PowerBIMeasure, case: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "object_id": measure.object_id,
        "table": measure.table,
        "name": measure.name,
        "expression": measure.expression,
        "expression_sha256": measure.expression_hash,
        "description": measure.description,
        "format_string": measure.format_string,
        "display_folder": measure.display_folder,
        "lineage_tag": measure.lineage_tag,
        "semantic_role": "measure",
        "semantic_status": case.get("semantic_status", "UNKNOWN"),
        "expected_review_category": case.get(
            "expected_review_category", "MANUAL_REVIEW_REQUIRED"
        ),
        "parser_supported": measure.analysis.supported,
        "parser_pattern": measure.analysis.pattern,
        "dependencies": list(measure.analysis.measure_dependencies),
        "referenced_tables": list(measure.analysis.table_dependencies),
        "referenced_columns": list(measure.analysis.column_dependencies),
        "filters": [item.to_dict() for item in measure.analysis.filters],
        "source_unit": case.get("source_unit", "NOT_DECLARED"),
        "target_unit": case.get("target_unit", "NOT_DECLARED"),
        "intentional_defects": list(case.get("intentional_defects", [])),
        "correction": case.get("correction"),
        "source_location": measure.source_location.to_dict(),
    }


def _powerbi_inventory_summary(
    inventory: PowerBIModelInventory,
) -> dict[str, Any]:
    return {
        "model": inventory.model.to_dict(),
        "semantic_sha256": inventory.semantic_hash,
        "tables": [item.to_dict() for item in inventory.tables],
        "columns": [item.to_dict() for item in inventory.columns],
        "relationships": [item.to_dict() for item in inventory.relationships],
        "partitions": [item.to_dict() for item in inventory.partitions],
        "hierarchies": [item.to_dict() for item in inventory.hierarchies],
        "calculation_groups": [
            item.to_dict() for item in inventory.calculation_groups
        ],
        "dependency_graph": {
            "nodes": [item.object_id for item in inventory.measures],
            "edges": [
                {"from": source, "to": target}
                for source, target in inventory.dependency_edges
            ],
        },
        "counts": {
            "tables": len(inventory.tables),
            "columns": len(inventory.columns),
            "measures": len(inventory.measures),
            "relationships": len(inventory.relationships),
            "partitions": len(inventory.partitions),
            "hierarchies": len(inventory.hierarchies),
            "calculation_groups": len(inventory.calculation_groups),
        },
    }


def _target_record(target: SnowflakeMetric) -> dict[str, Any]:
    return {
        "object_id": target.object_id,
        "table": target.table,
        "name": target.name,
        "qualified_name": target.qualified_name,
        "expression": target.expression,
        "description": target.description,
        "source_location": target.source_location,
        "semantic_role": "metric",
        "metadata_fields": sorted(target.raw_metadata),
    }


def _propagate_dependency_risks(
    findings: list[dict[str, Any]],
) -> None:
    by_name = {item["source"]["name"].casefold(): item for item in findings}

    def add_reason(
        item: dict[str, Any],
        *,
        code: str,
        rationale: str,
    ) -> None:
        if code in item["reason_codes"]:
            return
        item["reason_codes"].append(code)
        item["rationale"].append(rationale)
        target = item.get("target")
        item["finding_ids"].append(
            _stable_id(
                "fnd_",
                item["case_id"],
                item["source"]["name"],
                code,
                target["object_id"] if target else None,
            )
        )

    changed = True
    while changed:
        changed = False
        for item in findings:
            semantic_risks: list[str] = []
            automation_risks: list[str] = []
            for dependency in item["source"]["dependencies"]:
                upstream = by_name.get(str(dependency).casefold())
                if upstream is None:
                    semantic_risks.append(f"{dependency}:UNRESOLVED")
                elif upstream["fidelity_status"] != "STRUCTURALLY_EQUIVALENT":
                    semantic_risks.append(
                        f"{dependency}:{upstream['fidelity_status']}"
                    )
                elif (
                    upstream["canonical"]["resolution_status"] != "EXACT_MAPPING"
                    or upstream["automation_disposition"] != "AUTO_CONVERT"
                ):
                    automation_risks.append(
                        f"{dependency}:"
                        f"{upstream['canonical']['resolution_status']}:"
                        f"{upstream['automation_disposition']}"
                    )
            combined_risks = sorted(set(semantic_risks + automation_risks))
            if combined_risks:
                item["dependency_risks"] = sorted(
                    set(item["dependency_risks"]) | set(combined_risks)
                )
            if (
                semantic_risks
                and item["fidelity_status"] == "STRUCTURALLY_EQUIVALENT"
            ):
                item["fidelity_status"] = "MANUAL_REVIEW_REQUIRED"
                add_reason(
                    item,
                    code="TRANSITIVE_DEPENDENCY_RISK",
                    rationale=(
                        "At least one transitive source measure is not "
                        "structurally proven."
                    ),
                )
                if item["source"]["semantic_status"] != "INTENTIONAL_DEFECT":
                    item["automation_disposition"] = "MANUAL_REVIEW_REQUIRED"
                changed = True
            elif (
                automation_risks
                and item["automation_disposition"] == "AUTO_CONVERT"
            ):
                item["automation_disposition"] = "MANUAL_REVIEW_REQUIRED"
                add_reason(
                    item,
                    code="TRANSITIVE_DEPENDENCY_AUTOMATION_RISK",
                    rationale=(
                        "At least one dependency lacks exact canonical lineage "
                        "or an automation-safe target mapping."
                    ),
                )
                changed = True


def _model_findings(
    findings: Sequence[Mapping[str, Any]],
    relationship_comparison: Mapping[str, Any],
    extra_targets: Sequence[SnowflakeMetric],
    reviewed_profile_structure: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    records: list[Mapping[str, Any]] = []
    target_sources: dict[str, list[str]] = defaultdict(list)
    target_names: dict[str, str] = {}
    for item in findings:
        target = item.get("target")
        if not isinstance(target, Mapping):
            continue
        target_id = str(target["object_id"])
        target_sources[target_id].append(str(item["source"]["name"]))
        target_names[target_id] = str(target["qualified_name"])
    duplicate_target_mappings = [
        {
            "target": target_names[target_id],
            "source_measures": sorted(source_names, key=str.casefold),
        }
        for target_id, source_names in sorted(target_sources.items())
        if len(source_names) > 1
    ]
    if duplicate_target_mappings:
        records.append(
            {
                "finding_id": _stable_id(
                    "fnd_",
                    "DUPLICATE_TARGET_MAPPING",
                    duplicate_target_mappings,
                ),
                "code": "DUPLICATE_TARGET_MAPPING",
                "severity": "BLOCKER",
                "objects": duplicate_target_mappings,
            }
        )
    if reviewed_profile_structure["status"] == "DRIFT":
        records.append(
            {
                "finding_id": _stable_id(
                    "fnd_",
                    "REVIEWED_MODEL_STRUCTURE_DRIFT",
                    reviewed_profile_structure,
                ),
                "code": "REVIEWED_MODEL_STRUCTURE_DRIFT",
                "severity": "BLOCKER",
                "powerbi_matches": reviewed_profile_structure[
                    "powerbi_matches"
                ],
                "snowflake_matches": reviewed_profile_structure[
                    "snowflake_matches"
                ],
            }
        )
    if extra_targets:
        records.append(
            {
                "finding_id": _stable_id(
                    "fnd_", "EXTRA_TARGET_METRICS", [item.object_id for item in extra_targets]
                ),
                "code": "EXTRA_TARGET_METRICS",
                "severity": "BLOCKER",
                "objects": [item.qualified_name for item in extra_targets],
            }
        )
    if relationship_comparison["source_only"]:
        records.append(
            {
                "finding_id": _stable_id(
                    "fnd_",
                    "SOURCE_RELATIONSHIPS_OMITTED",
                    relationship_comparison["source_only"],
                ),
                "code": "SOURCE_RELATIONSHIPS_OMITTED",
                "severity": "BLOCKER",
                "objects": [
                    item["name"] for item in relationship_comparison["source_only"]
                ],
            }
        )
    relationship_drift = [
        item
        for item in relationship_comparison["target_records"]
        if item["status"] != "ENDPOINTS_MATCH"
    ]
    if (
        relationship_drift
        or relationship_comparison["source_duplicate_signatures"]
        or relationship_comparison["target_duplicate_signatures"]
    ):
        records.append(
            {
                "finding_id": _stable_id(
                    "fnd_",
                    "RELATIONSHIP_OR_CARDINALITY_DRIFT",
                    relationship_drift,
                    relationship_comparison["source_duplicate_signatures"],
                    relationship_comparison["target_duplicate_signatures"],
                ),
                "code": "RELATIONSHIP_OR_CARDINALITY_DRIFT",
                "severity": "BLOCKER",
                "objects": [
                    item["name"] for item in relationship_drift
                ],
                "source_duplicate_signatures": relationship_comparison[
                    "source_duplicate_signatures"
                ],
                "target_duplicate_signatures": relationship_comparison[
                    "target_duplicate_signatures"
                ],
            }
        )
    if relationship_comparison["endpoint_match_count"]:
        records.append(
            {
                "finding_id": _stable_id(
                    "fnd_",
                    "RELATIONSHIP_PROPERTIES_UNREPRESENTED",
                    relationship_comparison["unrepresented_power_bi_properties"],
                ),
                "code": "RELATIONSHIP_PROPERTIES_UNREPRESENTED",
                "severity": "WARNING",
                "objects": list(
                    relationship_comparison["unrepresented_power_bi_properties"]
                ),
            }
        )
    metadata_counts = Counter(
        code for item in findings for code in item["metadata_findings"]
    )
    if metadata_counts:
        records.append(
            {
                "finding_id": _stable_id(
                    "fnd_", "TARGET_METADATA_LOSS", dict(sorted(metadata_counts.items()))
                ),
                "code": "TARGET_METADATA_LOSS",
                "severity": "WARNING",
                "counts": dict(sorted(metadata_counts.items())),
            }
        )
    return tuple(records)


def audit_powerbi_snowflake(
    *,
    model_dir: str | Path,
    snowflake_yaml: str | Path,
    benchmark_spec: str | Path | None = None,
    snowflake_diagnostics: str | Path | None = None,
    result_evidence: str | Path | None = None,
    repository_root: str | Path = PROJECT_ROOT,
    canonical_yaml: str | Path | None = DBT_SEMANTIC_YAML,
) -> PowerBISnowflakeAudit:
    """Create a read-only, hash-bound source-to-target conversion audit."""

    root = Path(repository_root).resolve()
    if not root.is_dir():
        raise AuditInputError(f"Repository root does not exist: {root}")
    model_path = _require_repository_path(
        model_dir, root, label="Power BI model", kind="existing"
    )
    snowflake_path = _require_repository_path(
        snowflake_yaml, root, label="Snowflake semantic-view YAML", kind="file"
    )
    spec_path = (
        _require_repository_path(
            benchmark_spec, root, label="Benchmark specification", kind="file"
        )
        if benchmark_spec is not None
        else None
    )
    diagnostic_path = (
        _require_repository_path(
            snowflake_diagnostics,
            root,
            label="Sanitized Snowflake diagnostics",
            kind="file",
        )
        if snowflake_diagnostics is not None
        else None
    )
    evidence_path = (
        _require_repository_path(
            result_evidence, root, label="Runtime result evidence", kind="file"
        )
        if result_evidence is not None
        else None
    )
    canonical_path = None
    if canonical_yaml is not None:
        candidate = Path(canonical_yaml)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            canonical_path = _require_repository_path(
                candidate, root, label="Canonical dbt semantic YAML", kind="file"
            )

    try:
        inventory = extract_powerbi_inventory(model_path, repository_root=root)
    except (OSError, UnicodeError, ValueError, PowerBIImportError) as exc:
        raise AuditInputError(f"Power BI model inventory failed: {exc}") from exc
    snowflake_data, snowflake_hash = _load_snowflake_yaml(snowflake_path)
    metrics, target_inventory = _snowflake_inventory(snowflake_data)
    spec = None
    spec_hash = None
    if spec_path is not None:
        try:
            spec = load_measure_spec(spec_path)
            spec_hash = measure_spec_sha256(spec)
        except (OSError, UnicodeError, yaml.YAMLError, PowerBIBenchmarkError) as exc:
            raise AuditInputError(f"Benchmark specification is invalid: {exc}") from exc
    cases, required_slices = _case_manifest(inventory, spec)
    cases_by_name = {str(item["name"]).casefold(): item for item in cases}
    benchmark_profile = (
        str(spec.get("benchmark_name")) if spec is not None else None
    )
    powerbi_structure_hash = _powerbi_structure_sha256(inventory)
    snowflake_structure_hash = _snowflake_structure_sha256(snowflake_data)
    if benchmark_profile == _PBI_TRIAL_V2_PROFILE:
        powerbi_structure_matches = (
            powerbi_structure_hash
            == _PBI_TRIAL_V2_POWERBI_STRUCTURE_SHA256
        )
        snowflake_structure_matches = (
            snowflake_structure_hash
            == _PBI_TRIAL_V2_SNOWFLAKE_STRUCTURE_SHA256
        )
        reviewed_profile_structure = {
            "status": (
                "MATCH"
                if powerbi_structure_matches and snowflake_structure_matches
                else "DRIFT"
            ),
            "profile": benchmark_profile,
            "powerbi_matches": powerbi_structure_matches,
            "snowflake_matches": snowflake_structure_matches,
        }
    else:
        powerbi_structure_matches = True
        snowflake_structure_matches = True
        reviewed_profile_structure = {
            "status": "NOT_APPLICABLE",
            "profile": benchmark_profile,
            "powerbi_matches": None,
            "snowflake_matches": None,
        }
    relationships = _relationship_comparison(inventory, snowflake_data)
    relationship_blocked = bool(
        relationships["source_only"]
        or relationships["source_duplicate_signatures"]
        or relationships["target_duplicate_signatures"]
        or any(
            item["status"] != "ENDPOINTS_MATCH"
            for item in relationships["target_records"]
        )
    )
    (
        required_slices_by_measure,
        behavioral_baseline_path,
        behavioral_baseline_hash,
    ) = _behavioral_slice_scope(
        benchmark_spec_path=spec_path,
        cases=cases,
        required_slices=required_slices,
        repository_root=root,
    )
    canonical_map, canonical_hash = _canonical_index(canonical_path)
    canonical_source_file = (
        canonical_path.relative_to(root).as_posix()
        if canonical_path is not None
        else None
    )

    diagnostic_hashes: dict[str, str | None] = {
        "powerbi_source_tree_sha256": inventory.model.source_tree_hash,
        "snowflake_yaml_sha256": snowflake_hash,
        "benchmark_spec_sha256": spec_hash,
        "behavioral_baseline_sha256": behavioral_baseline_hash,
    }
    query_pack_path = (
        spec_path.parent / "snowflake-query-pack.sql"
        if benchmark_profile == _PBI_TRIAL_V2_PROFILE and spec_path is not None
        else None
    )
    if query_pack_path is not None and not query_pack_path.is_file():
        raise AuditInputError("The PBI trial v2 Snowflake query pack is missing.")
    runtime_hashes: dict[str, str | None] = {
        "behavioral_baseline_sha256": behavioral_baseline_hash,
        "benchmark_spec_sha256": spec_hash,
        "powerbi_model_tree_sha256": inventory.model.source_tree_hash,
        "snowflake_query_pack_sha256": (
            _sha256_file(query_pack_path) if query_pack_path is not None else None
        ),
        "snowflake_yaml_sha256": snowflake_hash,
    }
    rejected_defects, diagnostic_summary = _load_diagnostics(
        diagnostic_path,
        expected_hashes=diagnostic_hashes,
        cases_by_name=cases_by_name,
    )
    behavior_by_name, runtime_summary = _load_result_evidence(
        evidence_path,
        expected_hashes=runtime_hashes,
        cases_by_name=cases_by_name,
        required_slices=required_slices,
        required_slices_by_measure=required_slices_by_measure,
    )

    exact_target, normalized_target = _target_indexes(metrics)
    source_normalized_counts = Counter(
        normalize_snowflake_identifier(item.name) for item in inventory.measures
    )
    measures_by_name = {item.name.casefold(): item for item in inventory.measures}
    mapped_target_ids: set[str] = set()
    mutable_findings: list[dict[str, Any]] = []
    for case in cases:
        measure = measures_by_name[str(case["name"]).casefold()]
        target, mapping_method, mapping_error = _map_target(
            measure.name,
            explicit_matches=_explicit_target_matches(
                measure, metrics, canonical_map
            ),
            exact=exact_target,
            normalized=normalized_target,
            source_normalized_counts=source_normalized_counts,
        )
        if target is not None:
            mapped_target_ids.add(target.object_id)
        fidelity, reason_codes, rationale = _static_fidelity(
            measure,
            case,
            target,
            mapping_error,
            benchmark_profile=benchmark_profile,
            benchmark_source_structure_matches=powerbi_structure_matches,
            benchmark_target_structure_matches=snowflake_structure_matches,
        )
        if relationship_blocked and fidelity == "STRUCTURALLY_EQUIVALENT":
            fidelity = "MANUAL_REVIEW_REQUIRED"
            reason_codes = (*reason_codes, "MODEL_RELATIONSHIP_RISK")
            rationale = (
                *rationale,
                "Unresolved relationship, cardinality, active-state, or "
                "cross-filter drift prevents automatic equivalence.",
            )
        canonical_record = _canonical_source(
            measure, canonical_map, canonical_source_file
        )
        defects = tuple(str(item) for item in case.get("intentional_defects", []))
        proven_rejections = rejected_defects.get(measure.name.casefold(), set())
        if proven_rejections and target is not None:
            raise AuditStateError(
                "Snowflake diagnostics reject a defect while the hash-bound YAML "
                f"still emits an active target for {measure.name}."
            )
        if defects and set(defects).issubset(proven_rejections):
            detection = "PROVEN_CAUGHT"
            handling = "REJECTED"
        elif defects and target is not None:
            detection = "PROVEN_NOT_CAUGHT"
            if fidelity == "CONFIRMED_INCORRECT":
                handling = "CHANGED"
            elif "caution" in target.description.casefold() or "non-standard" in target.description.casefold():
                handling = "EMITTED_WITH_CAUTION"
            else:
                handling = "EMITTED"
        elif defects:
            detection = "NOT_PROVEN"
            handling = "OMITTED"
        else:
            detection = "NOT_PROVEN"
            handling = (
                "OMITTED"
                if target is None
                else "CHANGED"
                if fidelity == "CONFIRMED_INCORRECT"
                else "EMITTED"
            )
        if defects:
            automation = "FLAG_SOURCE_DEFECT"
        elif (
            fidelity == "STRUCTURALLY_EQUIVALENT"
            and canonical_record["resolution_status"] == "EXACT_MAPPING"
        ):
            automation = "AUTO_CONVERT"
        else:
            automation = "MANUAL_REVIEW_REQUIRED"
        metadata = _metadata_findings(measure, case, target)
        if automation == "AUTO_CONVERT" and metadata:
            automation = "MANUAL_REVIEW_REQUIRED"
            reason_codes = (*reason_codes, "TARGET_METADATA_DRIFT")
            rationale = (
                *rationale,
                "Target format, folder, lineage, unit, description, or safety "
                "metadata is missing or does not match the source evidence.",
            )
        ids = tuple(
            _stable_id(
                "fnd_",
                case["case_id"],
                measure.name,
                code,
                target.object_id if target else None,
            )
            for code in reason_codes
        )
        mutable_findings.append(
            {
                "case_id": str(case["case_id"]),
                "source": _source_record(measure, case),
                "canonical": canonical_record,
                "target": _target_record(target) if target else None,
                "mapping_method": mapping_method,
                "fidelity_status": fidelity,
                "behavioral_status": (
                    behavior_by_name[measure.name.casefold()]
                    if target is not None
                    else "NOT_AVAILABLE"
                ),
                "detection_status": detection,
                "observed_handling": handling,
                "automation_disposition": automation,
                "finding_ids": list(ids),
                "reason_codes": list(reason_codes),
                "rationale": list(rationale),
                "dependency_risks": [],
                "metadata_findings": list(metadata),
            }
        )
    target_to_findings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in mutable_findings:
        if item["target"] is not None:
            target_to_findings[item["target"]["object_id"]].append(item)
    for target_id, reused in target_to_findings.items():
        if len(reused) < 2:
            continue
        source_names = sorted(
            (str(item["source"]["name"]) for item in reused),
            key=str.casefold,
        )
        collision_id = _stable_id(
            "fnd_", "DUPLICATE_TARGET_MAPPING", target_id, source_names
        )
        for item in reused:
            item["mapping_method"] = "AMBIGUOUS"
            item["fidelity_status"] = "MANUAL_REVIEW_REQUIRED"
            item["reason_codes"].append("DUPLICATE_TARGET_MAPPING")
            item["rationale"].append(
                "The same Snowflake metric is mapped to multiple Power BI measures: "
                + ", ".join(source_names)
                + "."
            )
            item["finding_ids"].append(collision_id)
            if item["source"]["semantic_status"] != "INTENTIONAL_DEFECT":
                item["automation_disposition"] = "MANUAL_REVIEW_REQUIRED"
    _propagate_dependency_risks(mutable_findings)

    findings: list[AuditMeasureFinding] = []
    for item in mutable_findings:
        for status, allowed, label in (
            (item["fidelity_status"], FIDELITY_STATUSES, "fidelity"),
            (item["behavioral_status"], BEHAVIORAL_STATUSES, "behavioral"),
            (item["detection_status"], DETECTION_STATUSES, "detection"),
            (item["observed_handling"], OBSERVED_HANDLING, "observed handling"),
            (
                item["automation_disposition"],
                AUTOMATION_DISPOSITIONS,
                "automation disposition",
            ),
        ):
            if status not in allowed:
                raise AssertionError(f"Invalid {label} status: {status}")
        findings.append(
            AuditMeasureFinding(
                case_id=item["case_id"],
                source=item["source"],
                canonical=item["canonical"],
                target=item["target"],
                mapping_method=item["mapping_method"],
                fidelity_status=item["fidelity_status"],
                behavioral_status=item["behavioral_status"],
                detection_status=item["detection_status"],
                observed_handling=item["observed_handling"],
                automation_disposition=item["automation_disposition"],
                finding_ids=tuple(item["finding_ids"]),
                reason_codes=tuple(item["reason_codes"]),
                rationale=tuple(item["rationale"]),
                dependency_risks=tuple(item["dependency_risks"]),
                metadata_findings=tuple(item["metadata_findings"]),
            )
        )

    extras = tuple(item for item in metrics if item.object_id not in mapped_target_ids)
    model_findings = _model_findings(
        mutable_findings,
        relationships,
        extras,
        reviewed_profile_structure,
    )
    fidelity_counts = Counter(item.fidelity_status for item in findings)
    behavior_counts = Counter(item.behavioral_status for item in findings)
    runtime_summary["status_counts"] = dict(sorted(behavior_counts.items()))
    defect_findings = [
        item for item in findings if item.source["semantic_status"] == "INTENTIONAL_DEFECT"
    ]
    detection_counts = Counter(item.detection_status for item in defect_findings)
    handling_counts = Counter(item.observed_handling for item in defect_findings)
    automation_counts = Counter(item.automation_disposition for item in findings)
    measure_blocker_count = sum(
        item.fidelity_status != "STRUCTURALLY_EQUIVALENT"
        or item.behavioral_status == "FAILED"
        or item.automation_disposition != "AUTO_CONVERT"
        for item in findings
    )
    model_blocker_count = sum(
        item.get("severity") == "BLOCKER" for item in model_findings
    )
    blocker_count = measure_blocker_count + model_blocker_count
    summary = {
        "source_measure_count": len(findings),
        "target_metric_count": len(metrics),
        "matched_measure_count": sum(
            item.target is not None and item.mapping_method != "AMBIGUOUS"
            for item in findings
        ),
        "omitted_measure_count": fidelity_counts["OMITTED"],
        "extra_target_metric_count": len(extras),
        "fidelity_status_counts": dict(sorted(fidelity_counts.items())),
        "behavioral_status_counts": dict(sorted(behavior_counts.items())),
        "automation_disposition_counts": dict(sorted(automation_counts.items())),
        "negative_control_count": len(defect_findings),
        "negative_control_detection_counts": dict(sorted(detection_counts.items())),
        "negative_control_handling_counts": dict(sorted(handling_counts.items())),
        "proven_caught_count": detection_counts["PROVEN_CAUGHT"],
        "measure_blocker_count": measure_blocker_count,
        "model_blocker_count": model_blocker_count,
        "blocker_count": blocker_count,
        "all_in_scope_mappings_proven_safe": blocker_count == 0,
        "executive_verdict": (
            "ALL_IN_SCOPE_MAPPINGS_PROVEN_SAFE"
            if blocker_count == 0
            else "SNOWFLAKE_DID_NOT_PROVE_COMPLETE_OR_CORRECT_CONVERSION"
        ),
    }
    inputs = {
        "powerbi_model_path": model_path.relative_to(root).as_posix(),
        "powerbi_source_tree_sha256": inventory.model.source_tree_hash,
        "powerbi_inventory_semantic_sha256": inventory.semantic_hash,
        "powerbi_structure_sha256": powerbi_structure_hash,
        "snowflake_yaml_path": snowflake_path.relative_to(root).as_posix(),
        "snowflake_yaml_sha256": snowflake_hash,
        "snowflake_structure_sha256": snowflake_structure_hash,
        "benchmark_spec_path": (
            spec_path.relative_to(root).as_posix() if spec_path is not None else None
        ),
        "benchmark_spec_sha256": spec_hash,
        "behavioral_baseline_path": (
            behavioral_baseline_path.relative_to(root).as_posix()
            if behavioral_baseline_path is not None
            else None
        ),
        "behavioral_baseline_sha256": behavioral_baseline_hash,
        "canonical_contract_path": (
            canonical_path.relative_to(root).as_posix()
            if canonical_path is not None
            else None
        ),
        "canonical_contract_sha256": canonical_hash,
        "snowflake_diagnostics": diagnostic_summary,
        "runtime_result_evidence_sha256": runtime_summary["sha256"],
        "snowflake_query_pack_sha256": runtime_hashes[
            "snowflake_query_pack_sha256"
        ],
    }
    audit_id = _stable_id(
        "audit_",
        inventory.model.source_tree_hash,
        inventory.semantic_hash,
        snowflake_hash,
        spec_hash,
        behavioral_baseline_hash,
        canonical_hash,
        diagnostic_summary["sha256"],
        runtime_summary["sha256"],
        AUDIT_ENGINE_VERSION,
    )
    authority = {
        "canonical_contract": (
            canonical_path.relative_to(root).as_posix()
            if canonical_path is not None
            else "models/semantic/triathlon_semantic.yml"
        ),
        "canonical_contract_role": "ONLY_PRODUCTION_SEMANTIC_AUTHORITY",
        "power_bi_role": "INSPECTED_TARGET_AND_BENCHMARK_EVIDENCE",
        "snowflake_role": "GENERATED_TARGET_EVIDENCE",
        "benchmark_role": "TEST_ORACLE_ONLY",
        "creates_change_request": False,
        "applies_or_deploys": False,
    }
    runtime_summary = {
        **runtime_summary,
        "query_form_documentation": "https://docs.snowflake.com/en/user-guide/views-semantic/querying",
    }
    return PowerBISnowflakeAudit(
        audit_id=audit_id,
        inputs=inputs,
        authority=authority,
        summary=summary,
        measures=tuple(findings),
        powerbi_inventory={
            **_powerbi_inventory_summary(inventory),
            "structure_sha256": powerbi_structure_hash,
        },
        snowflake_inventory={
            **target_inventory,
            "structure_sha256": snowflake_structure_hash,
            "reviewed_profile_structure": reviewed_profile_structure,
            "extra_metrics": [item.to_dict() for item in extras],
        },
        relationship_comparison=relationships,
        runtime_evidence=runtime_summary,
        model_findings=model_findings,
    )


def _md(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace(
        "\n", " "
    )


def render_audit_markdown(audit: PowerBISnowflakeAudit) -> str:
    data = audit.to_dict()
    summary = data["summary"]
    inputs = data["inputs"]
    measures = data["measures"]
    evidence_roles = ["Power BI model", "Snowflake export"]
    if inputs["benchmark_spec_sha256"]:
        evidence_roles.insert(1, "benchmark oracle")
    evidence_subject = (
        f"The {evidence_roles[0]} and the {evidence_roles[1]}"
        if len(evidence_roles) == 2
        else (
            "The "
            + ", the ".join(evidence_roles[:-1])
            + ", and the "
            + evidence_roles[-1]
        )
    )
    reproduction = [
        "semantic-agent audit-powerbi-snowflake",
        f"--model-dir {json.dumps(inputs['powerbi_model_path'])}",
        f"--snowflake-yaml {json.dumps(inputs['snowflake_yaml_path'])}",
    ]
    if inputs["benchmark_spec_path"]:
        reproduction.append(
            f"--benchmark-spec {json.dumps(inputs['benchmark_spec_path'])}"
        )
    if inputs["snowflake_diagnostics"]["sha256"]:
        reproduction.append(
            "--snowflake-diagnostics <sanitized-diagnostics-file>"
        )
    if inputs["runtime_result_evidence_sha256"]:
        reproduction.append("--result-evidence <hash-bound-result-file>")
    reproduction.extend(["--output-dir <controlled-directory>", "--check"])
    reproduction_commands = [" ".join(reproduction)]
    if (
        data["snowflake_inventory"]["reviewed_profile_structure"]["profile"]
        == _PBI_TRIAL_V2_PROFILE
    ):
        reproduction_commands.append(
            "python semantic_poc/run_pbi_trial_v2_audit.py --check"
        )
    defects = [
        item for item in measures if item["source"]["semantic_status"] == "INTENTIONAL_DEFECT"
    ]
    confirmed = [
        item for item in measures if item["fidelity_status"] == "CONFIRMED_INCORRECT"
    ]
    potential = [
        item for item in measures if item["fidelity_status"] == "POTENTIALLY_INCORRECT"
    ]
    omitted = [item for item in measures if item["fidelity_status"] == "OMITTED"]
    lines = [
        "# Power BI to Snowflake Conversion Audit Findings",
        "",
        f"Audit ID: `{audit.audit_id}`",
        f"Audit kind: `{AUDIT_KIND}`",
        f"Audit engine: `{AUDIT_ENGINE_VERSION}`",
        "",
        "## Authority and evidence",
        "",
        f"`{audit.authority['canonical_contract']}` is the only production semantic "
        f"authority. {evidence_subject} are read-only evidence; "
        "this audit creates no change request and performs no application or deployment.",
        "",
        f"- Canonical contract SHA-256: `{inputs['canonical_contract_sha256'] or 'NOT_AVAILABLE'}`",
        f"- Power BI TMDL tree SHA-256: `{inputs['powerbi_source_tree_sha256']}`",
        f"- Power BI semantic inventory SHA-256: `{inputs['powerbi_inventory_semantic_sha256']}`",
        f"- Power BI structural SHA-256: `{inputs['powerbi_structure_sha256']}`",
        f"- Snowflake YAML SHA-256: `{inputs['snowflake_yaml_sha256']}`",
        f"- Snowflake structural SHA-256: `{inputs['snowflake_structure_sha256']}`",
        f"- Benchmark oracle SHA-256: `{inputs['benchmark_spec_sha256'] or 'NOT_AVAILABLE'}`",
        f"- Behavioral baseline SHA-256: `{inputs['behavioral_baseline_sha256'] or 'NOT_AVAILABLE'}`",
        f"- Snowflake diagnostics SHA-256: `{inputs['snowflake_diagnostics']['sha256'] or 'NOT_AVAILABLE'}`",
        f"- Runtime result evidence SHA-256: `{inputs['runtime_result_evidence_sha256'] or 'NOT_AVAILABLE'}`",
        "",
        "## Executive verdict",
        "",
        f"**{summary['executive_verdict']}.** Snowflake emitted "
        f"{summary['matched_measure_count']} of {summary['source_measure_count']} measures and "
        f"omitted {summary['omitted_measure_count']}. The audit confirms "
        f"{summary['fidelity_status_counts'].get('CONFIRMED_INCORRECT', 0)} mistranslations and "
        f"identifies {summary['fidelity_status_counts'].get('POTENTIALLY_INCORRECT', 0)} additional "
        "potential semantic mismatches that need differential evidence.",
        "",
        f"Of {summary['negative_control_count']} intentional defects, "
        f"{summary['proven_caught_count']} are proven caught. Silent omission is `NOT_PROVEN`; "
        "cautionary prose on an active metric is not a blocking diagnostic.",
        "",
        "The static scope is aligned with Snowflake's documented support categories for "
        "[Power BI ingestion](https://docs.snowflake.com/en/user-guide/views-semantic/power-bi-ingestion). "
        "Runtime queries use the documented "
        "[semantic-view query form](https://docs.snowflake.com/en/user-guide/views-semantic/querying).",
        "",
        "## All measures",
        "",
        "| Case | Power BI measure | Canonical metric | Snowflake metric | Fidelity | Behavior | Detection | Handling | Automation | Finding codes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in measures:
        lines.append(
            "| "
            + " | ".join(
                _md(value)
                for value in (
                    f"`{item['case_id']}`",
                    f"`{item['source']['name']}`",
                    (
                        f"`{item['canonical']['metric']}`"
                        if item["canonical"]["metric"]
                        else "unresolved benchmark evidence"
                    ),
                    (
                        f"`{item['target']['qualified_name']}`"
                        if item["target"]
                        else "—"
                    ),
                    f"`{item['fidelity_status']}`",
                    f"`{item['behavioral_status']}`",
                    f"`{item['detection_status']}`",
                    f"`{item['observed_handling']}`",
                    f"`{item['automation_disposition']}`",
                    ", ".join(f"`{code}`" for code in item["reason_codes"]),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Negative-control detection scorecard",
            "",
            "| Control | Defect | Target handling | Detection | Fidelity |",
            "|---|---|---|---|---|",
        ]
    )
    for item in defects:
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{_md(item['source']['name'])}`",
                    ", ".join(
                        f"`{_md(code)}`"
                        for code in item["source"]["intentional_defects"]
                    ),
                    f"`{item['observed_handling']}`",
                    f"`{item['detection_status']}`",
                    f"`{item['fidelity_status']}`",
                )
            )
            + " |"
        )
    lines.extend(["", "## Confirmed mistranslations", ""])
    for item in confirmed:
        lines.append(
            f"- `{item['source']['name']}` — {item['rationale'][0]} "
            f"Finding: `{item['finding_ids'][0]}`."
        )
    lines.extend(["", "## Potential semantic mismatches", ""])
    for item in potential:
        lines.append(
            f"- `{item['source']['name']}` — {item['rationale'][0]} "
            f"Behavioral status: `{item['behavioral_status']}`."
        )
    lines.extend(
        [
            "",
            "## Omissions",
            "",
            f"{len(omitted)} measures were omitted without defect-specific rejection evidence:",
            "",
        ]
    )
    lines.append(", ".join(f"`{item['source']['name']}`" for item in omitted) + ".")
    relationship = data["relationship_comparison"]
    source_counts = data["powerbi_inventory"]["counts"]
    target_inventory = data["snowflake_inventory"]
    metadata_counts = Counter(
        code for item in measures for code in item["metadata_findings"]
    )
    lines.extend(
        [
            "",
            "## Relationships, grain, and metadata",
            "",
            f"- Power BI inventory: {source_counts['tables']} tables, "
            f"{source_counts['columns']} columns, {source_counts['measures']} measures, "
            f"and {source_counts['relationships']} relationships.",
            f"- Snowflake inventory: {len(target_inventory['tables'])} logical tables, "
            f"{target_inventory['dimension_count']} dimensions, "
            f"{target_inventory['time_dimension_count']} time dimension"
            f"{'s' if target_inventory['time_dimension_count'] != 1 else ''}, "
            f"{target_inventory['fact_count']} facts, and "
            f"{target_inventory['metric_count']} metrics.",
            f"- Relationship endpoints matched for {relationship['endpoint_match_count']} of "
            f"{relationship['source_relationship_count']} Power BI relationships.",
            "- Default active, single-direction relationships are compatibility-checked. "
            "Inactive, bidirectional, cardinality-drifted, missing, or duplicate relationships "
            "are blockers; explicit relationship-property provenance remains unrepresented.",
            f"- Reviewed model-structure status: "
            f"`{target_inventory['reviewed_profile_structure']['status']}`.",
            f"- Metadata-loss counts: `{json.dumps(dict(sorted(metadata_counts.items())), sort_keys=True)}`.",
            "- Target descriptions replace the benchmark provenance and weaken negative-control "
            "safety labels. Formats, display folders, units, and source lineage are not represented.",
            "",
            "## Runtime evidence",
            "",
            f"Runtime evidence is `{data['runtime_evidence']['status']}`. A pass is accepted only "
            "when every required slice is supplied with current input hashes and complete Power BI "
            "and Snowflake grouped exports have identical coordinate sets. Integers compare exactly; "
            "decimals use absolute and relative tolerance `1e-9`. Missing metrics, missing rows, "
            "missing slices, and unavailable exports remain `NOT_AVAILABLE` or fail comparison.",
            "",
            "Required slices: "
            + ", ".join(
                f"`{item}`" for item in data["runtime_evidence"]["required_slices"]
            )
            + ".",
            "",
            "## Deterministic conversion recipes",
            "",
            "- `AUTO_CONVERT`: only after exact canonical resolution, compile the proof-backed "
            "typed pattern into a canonical-first proposal or candidate YAML; do not approve or deploy it.",
            "- `FLAG_SOURCE_DEFECT`: preserve the oracle defect evidence, stop automatic conversion, "
            "and route the named correction through canonical review.",
            "- `MANUAL_REVIEW_REQUIRED`: stop for ambiguity, context transition, relationship paths, "
            "fanout, unsupported iterators/ranking, metadata-only defects, or unresolved dependencies.",
            "- Never synthesize arbitrary final DAX or Snowflake SQL. Target definitions must come "
            "from deterministic typed compilers.",
            "",
            "## Reproduction",
            "",
            "```text",
            *reproduction_commands,
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_snowflake_query_pack(audit: PowerBISnowflakeAudit) -> str:
    lines = [
        "-- Sanitized Snowflake semantic-view differential query pack.",
        "-- Replace <database>.<schema>.<semantic_view> locally; never commit account identifiers or raw results.",
        "-- Query form: SEMANTIC_VIEW(... DIMENSIONS ... METRICS ...).",
        "-- Runtime evidence must use the exact qualified DIMENSIONS names as coordinate keys.",
        "-- EVENT preserves five baseline coordinates; DIVISION preserves DIVISION and IS_PRO.",
        "-- COUNTRY emits detail, region, continent, and grand-total grouping queries; omit non-grouped coordinate keys when merging exports.",
        f"-- Audit: {audit.audit_id}",
        "",
    ]
    for finding in audit.measures:
        if finding.target is None:
            lines.append(
                f"-- {finding.case_id} | {finding.source['name']} | OMITTED: no runnable target metric"
            )
            continue
        metric = finding.target["qualified_name"]
        source_name = str(finding.source["name"])
        slices = audit.runtime_evidence["required_slices_by_measure"][source_name]
        for slice_name in slices:
            dimension_sets = _SLICE_QUERY_DIMENSION_SETS[str(slice_name)]
            for grouping_index, dimensions in enumerate(dimension_sets, start=1):
                coordinate_keys = (
                    "{" + ", ".join(dimensions) + "}" if dimensions else "{}"
                )
                lines.extend(
                    [
                        f"-- {finding.case_id} | {source_name} | {slice_name} "
                        f"| grouping {grouping_index}/{len(dimension_sets)}",
                        f"-- Evidence coordinate keys: {coordinate_keys}",
                        "SELECT *",
                        "FROM SEMANTIC_VIEW(",
                        "  <database>.<schema>.<semantic_view>",
                        *(
                            ["  DIMENSIONS " + ", ".join(dimensions)]
                            if dimensions
                            else []
                        ),
                        f"  METRICS {metric}",
                        ");",
                        "",
                    ]
                )
    return "\n".join(lines)


def render_result_evidence_template(audit: PowerBISnowflakeAudit) -> str:
    value = {
        "schema_version": 1,
        "profile": "PBI_TRIAL_V2_AUDIT",
        "subject_hashes": {
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
        },
        "comparison": {
            "integer": "EXACT",
            "decimal_absolute_tolerance": 1e-9,
            "decimal_relative_tolerance": 1e-9,
            "blank_null_representation": "JSON_NULL",
        },
        "results": [],
    }
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def audit_artifact_contents(
    audit: PowerBISnowflakeAudit,
) -> dict[str, bytes]:
    return {
        "conversion-findings.json": (
            json.dumps(
                audit.to_dict(), indent=2, sort_keys=True, ensure_ascii=False
            )
            + "\n"
        ).encode("utf-8"),
        "POWERBI_SNOWFLAKE_CONVERSION_FINDINGS.md": render_audit_markdown(
            audit
        ).encode("utf-8"),
        "snowflake-query-pack.sql": render_snowflake_query_pack(audit).encode(
            "utf-8"
        ),
        "result-evidence.template.json": render_result_evidence_template(
            audit
        ).encode("utf-8"),
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or _is_junction(path):
        raise AuditStateError(f"Audit output cannot replace a link: {path}")
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
    temporary.replace(path)


def write_audit_output_directory(
    audit: PowerBISnowflakeAudit,
    *,
    output_dir: str | Path,
    repository_root: str | Path = PROJECT_ROOT,
    check: bool = False,
) -> dict[str, str]:
    root = Path(repository_root).resolve()
    requested = Path(output_dir)
    if not requested.is_absolute():
        requested = root / requested
    resolved = requested.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AuditStateError("Audit output directory must be inside the repository.") from exc
    cursor = resolved
    while cursor != root:
        if cursor.exists() and (cursor.is_symlink() or _is_junction(cursor)):
            raise AuditStateError(
                "Audit output directory cannot traverse a link or junction."
            )
        cursor = cursor.parent
    if resolved.exists() and (resolved.is_symlink() or _is_junction(resolved)):
        raise AuditStateError("Audit output directory cannot be a link or junction.")
    if resolved.exists() and not resolved.is_dir():
        raise AuditStateError("Audit output path exists and is not a directory.")
    if resolved.is_dir():
        unknown = sorted(
            item.name
            for item in resolved.iterdir()
            if item.name not in _SAFE_OUTPUT_FILES
        )
        if unknown:
            raise AuditStateError(
                "Existing audit output directory is not controlled; unexpected entries: "
                + ", ".join(unknown)
            )
    contents = audit_artifact_contents(audit)
    stale: list[str] = []
    for name, content in contents.items():
        path = resolved / name
        if check:
            if not path.is_file() or path.read_bytes() != content:
                stale.append(name)
        else:
            _atomic_write(path, content)
    if stale:
        raise AuditStateError(
            "Audit artifacts are missing or stale: " + ", ".join(stale)
        )
    return {
        name: (resolved / name).relative_to(root).as_posix()
        for name in sorted(contents)
    }


def write_fixed_audit_artifacts(
    audit: PowerBISnowflakeAudit,
    *,
    repository_root: str | Path = PROJECT_ROOT,
    check: bool = False,
) -> dict[str, str]:
    root = Path(repository_root).resolve()
    contents = audit_artifact_contents(audit)
    destinations = {
        "conversion-findings.json": root
        / "semantic_poc"
        / "benchmark"
        / "pbi_trial_v2"
        / "conversion-findings.json",
        "POWERBI_SNOWFLAKE_CONVERSION_FINDINGS.md": root
        / "docs"
        / "POWERBI_SNOWFLAKE_V2_CONVERSION_FINDINGS.md",
        "snowflake-query-pack.sql": root
        / "semantic_poc"
        / "benchmark"
        / "pbi_trial_v2"
        / "snowflake-query-pack.sql",
        "result-evidence.template.json": root
        / "semantic_poc"
        / "benchmark"
        / "pbi_trial_v2"
        / "result-evidence.template.json",
    }
    stale: list[str] = []
    for name, path in destinations.items():
        content = contents[name]
        if check:
            if not path.is_file() or path.read_bytes() != content:
                stale.append(path.relative_to(root).as_posix())
        else:
            _atomic_write(path, content)
    if stale:
        raise AuditStateError(
            "Committed fixed-audit artifacts are missing or stale: "
            + ", ".join(stale)
        )
    return {
        name: path.relative_to(root).as_posix()
        for name, path in sorted(destinations.items())
    }
