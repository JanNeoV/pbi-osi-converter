from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA_VERSION = 2
CANONICAL_FILE = "models/semantic/triathlon_semantic.yml"
CHANGE_ID_PATTERN = re.compile(r"^chg_\d{8}T\d{6}Z_[0-9a-f]{8}$")
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ChangeIntent(str, Enum):
    CREATE_METRIC = "CREATE_METRIC"
    UPDATE_METRIC = "UPDATE_METRIC"
    RENAME_METRIC = "RENAME_METRIC"
    DEPRECATE_METRIC = "DEPRECATE_METRIC"
    RECONCILE_TARGET_DRIFT = "RECONCILE_TARGET_DRIFT"


class ChangeMode(str, Enum):
    PROPOSE = "PROPOSE"
    APPLY_LOCAL = "APPLY_LOCAL"
    VALIDATE = "VALIDATE"


class TargetSupport(str, Enum):
    SUPPORTED_PATTERN = "SUPPORTED_PATTERN"
    METADATA_ONLY = "METADATA_ONLY"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"


class TargetName(str, Enum):
    CANONICAL_DBT = "CANONICAL_DBT"
    POWER_BI = "POWER_BI"
    SNOWFLAKE = "SNOWFLAKE"


class ChangeStatus(str, Enum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    NO_OP = "NO_OP"
    APPROVED = "APPROVED"
    APPLIED_LOCAL = "APPLIED_LOCAL"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    DISCARDED = "DISCARDED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class ApprovalState(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ValidationState(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"


class DeploymentState(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    NOT_PERFORMED = "NOT_PERFORMED"
    BLOCKED = "BLOCKED"


class OperationKind(str, Enum):
    SET_LABEL = "SET_LABEL"
    SET_DESCRIPTION = "SET_DESCRIPTION"
    SET_FORMAT = "SET_FORMAT"
    ADD_FILTER = "ADD_FILTER"
    REMOVE_FILTER = "REMOVE_FILTER"
    REPLACE_FILTER = "REPLACE_FILTER"
    SET_NUMERATOR = "SET_NUMERATOR"
    SET_DENOMINATOR = "SET_DENOMINATOR"
    ENSURE_EXCLUDED_VALUES = "ENSURE_EXCLUDED_VALUES"
    CREATE_METRIC = "CREATE_METRIC"
    RENAME_METRIC = "RENAME_METRIC"
    DEPRECATE_METRIC = "DEPRECATE_METRIC"


class RequestFilterOperator(str, Enum):
    EQ = "EQ"


SUPPORTED_FORMATS = frozenset({"whole_number", "decimal_2", "percentage_1_decimal"})
UPDATE_OPERATION_KINDS = frozenset(
    {
        OperationKind.SET_LABEL,
        OperationKind.SET_DESCRIPTION,
        OperationKind.SET_FORMAT,
        OperationKind.ADD_FILTER,
        OperationKind.REMOVE_FILTER,
        OperationKind.REPLACE_FILTER,
        OperationKind.SET_NUMERATOR,
        OperationKind.SET_DENOMINATOR,
        OperationKind.ENSURE_EXCLUDED_VALUES,
    }
)


def utc_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp source must be timezone-aware.")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_change_id(now: datetime | None = None, entropy: str | None = None) -> str:
    timestamp = utc_timestamp(now).replace("-", "").replace(":", "")
    suffix = entropy or uuid.uuid4().hex[:8]
    if not re.fullmatch(r"[0-9a-f]{8}", suffix):
        raise ValueError("Change ID entropy must contain exactly eight lowercase hexadecimal characters.")
    return f"chg_{timestamp}_{suffix}"


def validate_change_id(change_id: str) -> None:
    if not isinstance(change_id, str) or not CHANGE_ID_PATTERN.fullmatch(change_id):
        raise ValueError(f"Invalid change ID: {change_id!r}")


def validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid UTC timestamp: {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(f"Invalid UTC timestamp: {value!r}") from exc


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value


def _safe_metric_text(value: Any, field_name: str, *, identifier_only: bool = False) -> str:
    result = _required_text(value, field_name)
    if ".." in result or "/" in result or "\\" in result:
        raise ValueError(f"{field_name} must not contain path components.")
    if identifier_only and not SAFE_IDENTIFIER_PATTERN.fullmatch(result):
        raise ValueError(f"{field_name} must be a safe metric identifier.")
    return result


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array of strings.")
    result = tuple(value)
    if any(not isinstance(item, str) for item in result):
        raise ValueError(f"{field_name} must be an array of strings.")
    return result


@dataclass(frozen=True)
class FilterInput:
    field: str
    operator: RequestFilterOperator
    value: bool | int | float | str

    def __post_init__(self) -> None:
        _safe_metric_text(self.field, "filter.field", identifier_only=True)
        if type(self.value) not in {bool, int, float, str}:
            raise ValueError("filter.value must be a string, boolean, integer, or number.")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("filter.value must be a finite JSON number.")

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "operator": self.operator.value, "value": self.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FilterInput":
        _require_fields(data, {"field", "operator", "value"}, "filter")
        return cls(
            field=data["field"],
            operator=RequestFilterOperator(data["operator"]),
            value=data["value"],
        )


class CreateMetricPattern(str, Enum):
    COUNT = "COUNT"
    COLUMN_COUNT = "COLUMN_COUNT"
    SUM = "SUM"
    DISTINCT_COUNT = "DISTINCT_COUNT"
    FILTERED_COUNT = "FILTERED_COUNT"
    SCALED_SUM = "SCALED_SUM"
    RATIO = "RATIO"


@dataclass(frozen=True)
class TypedMetricDefinition:
    """Expression-free canonical metric shape accepted by CREATE_METRIC.

    Every target expression is derived from these typed fields.  The shape
    intentionally has no model, relationship, DAX, SQL, or raw-expression
    escape hatch.
    """

    pattern: CreateMetricPattern
    semantic_model: str
    label: str
    description: str
    public: bool
    source_field: str | None
    scale_divisor: str | None
    filters: tuple[FilterInput, ...]
    numerator: str | None
    denominator: str | None
    semantic_format: str
    power_bi_table: str
    power_bi_measure: str
    power_bi_format_string: str
    power_bi_display_folder: str | None
    snowflake_logical_table: str
    snowflake_metric_name: str
    snowflake_synonyms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("semantic_model", self.semantic_model),
            ("source_field", self.source_field),
            ("numerator", self.numerator),
            ("denominator", self.denominator),
            ("snowflake_logical_table", self.snowflake_logical_table),
            ("snowflake_metric_name", self.snowflake_metric_name),
        ):
            if value is not None:
                _safe_metric_text(value, f"definition.{name}", identifier_only=True)
        _required_text(self.label, "definition.label")
        if not isinstance(self.description, str):
            raise ValueError("definition.description must be a string.")
        if type(self.public) is not bool:
            raise ValueError("definition.public must be boolean.")
        if self.semantic_format not in SUPPORTED_FORMATS:
            raise ValueError("definition.semantic_format must be a registered semantic format.")
        for name, value in (
            ("power_bi_table", self.power_bi_table),
            ("power_bi_measure", self.power_bi_measure),
            ("power_bi_format_string", self.power_bi_format_string),
        ):
            _safe_metric_text(value, f"definition.{name}")
            if "\n" in value or "\r" in value:
                raise ValueError(f"definition.{name} must be a static single-line value.")
        if self.power_bi_display_folder is not None:
            _safe_metric_text(self.power_bi_display_folder, "definition.power_bi_display_folder")
        filters = tuple(self.filters)
        if len({item.field for item in filters}) != len(filters):
            raise ValueError("definition.filters must not repeat a field.")
        object.__setattr__(self, "filters", filters)
        synonyms = _string_tuple(self.snowflake_synonyms, "definition.snowflake_synonyms")
        if len(set(synonyms)) != len(synonyms) or any(not value.strip() for value in synonyms):
            raise ValueError("definition.snowflake_synonyms must contain unique non-empty strings.")
        object.__setattr__(self, "snowflake_synonyms", synonyms)

        column_patterns = {
            CreateMetricPattern.COLUMN_COUNT,
            CreateMetricPattern.SUM,
            CreateMetricPattern.DISTINCT_COUNT,
            CreateMetricPattern.SCALED_SUM,
        }
        if (self.source_field is not None) != (self.pattern in column_patterns):
            raise ValueError("definition.source_field is required only for typed column aggregations.")
        if bool(filters) != (self.pattern is CreateMetricPattern.FILTERED_COUNT):
            raise ValueError("definition.filters are required only for FILTERED_COUNT.")
        ratio = self.pattern is CreateMetricPattern.RATIO
        if bool(self.numerator) != ratio or bool(self.denominator) != ratio:
            raise ValueError("definition.numerator and denominator are required only for RATIO.")
        scaled = self.pattern is CreateMetricPattern.SCALED_SUM
        if (self.scale_divisor is not None) != scaled:
            raise ValueError("definition.scale_divisor is required only for SCALED_SUM.")
        if scaled:
            try:
                divisor = Decimal(self.scale_divisor or "")
            except InvalidOperation as exc:
                raise ValueError("definition.scale_divisor must be a positive decimal string.") from exc
            if not divisor.is_finite() or divisor <= 0:
                raise ValueError("definition.scale_divisor must be a positive decimal string.")
            object.__setattr__(self, "scale_divisor", format(divisor.normalize(), "f"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern.value,
            "semantic_model": self.semantic_model,
            "label": self.label,
            "description": self.description,
            "public": self.public,
            "source_field": self.source_field,
            "scale_divisor": self.scale_divisor,
            "filters": [item.to_dict() for item in self.filters],
            "numerator": self.numerator,
            "denominator": self.denominator,
            "semantic_format": self.semantic_format,
            "power_bi_table": self.power_bi_table,
            "power_bi_measure": self.power_bi_measure,
            "power_bi_format_string": self.power_bi_format_string,
            "power_bi_display_folder": self.power_bi_display_folder,
            "snowflake_logical_table": self.snowflake_logical_table,
            "snowflake_metric_name": self.snowflake_metric_name,
            "snowflake_synonyms": list(self.snowflake_synonyms),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TypedMetricDefinition":
        expected = {
            "pattern", "semantic_model", "label", "description", "public", "source_field",
            "scale_divisor", "filters", "numerator", "denominator", "semantic_format",
            "power_bi_table", "power_bi_measure", "power_bi_format_string",
            "power_bi_display_folder", "snowflake_logical_table", "snowflake_metric_name",
            "snowflake_synonyms",
        }
        _require_fields(data, expected, "typed metric definition")
        if isinstance(data["filters"], str) or not isinstance(data["filters"], (list, tuple)):
            raise ValueError("definition.filters must be an array.")
        return cls(
            pattern=CreateMetricPattern(data["pattern"]),
            semantic_model=data["semantic_model"],
            label=data["label"],
            description=data["description"],
            public=data["public"],
            source_field=data["source_field"],
            scale_divisor=data["scale_divisor"],
            filters=tuple(FilterInput.from_dict(item) for item in data["filters"]),
            numerator=data["numerator"],
            denominator=data["denominator"],
            semantic_format=data["semantic_format"],
            power_bi_table=data["power_bi_table"],
            power_bi_measure=data["power_bi_measure"],
            power_bi_format_string=data["power_bi_format_string"],
            power_bi_display_folder=data["power_bi_display_folder"],
            snowflake_logical_table=data["snowflake_logical_table"],
            snowflake_metric_name=data["snowflake_metric_name"],
            snowflake_synonyms=_string_tuple(data["snowflake_synonyms"], "definition.snowflake_synonyms"),
        )


def _require_fields(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a JSON object.")
    missing = expected - set(data)
    extra = set(data) - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected: " + ", ".join(sorted(extra)))
        raise ValueError(f"Invalid {label} fields (" + "; ".join(details) + ").")


@dataclass(frozen=True)
class MetricOperation:
    kind: OperationKind
    value: str | None = None
    definition: TypedMetricDefinition | None = None
    predicate: FilterInput | None = None
    current: FilterInput | None = None
    proposed: FilterInput | None = None
    field: str | None = None
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind in {OperationKind.SET_LABEL, OperationKind.SET_DESCRIPTION}:
            _required_text(self.value, "operation.value")
        elif self.kind is OperationKind.SET_FORMAT:
            if self.value not in SUPPORTED_FORMATS:
                raise ValueError("operation.value must be a registered semantic format.")
        elif self.kind in {OperationKind.ADD_FILTER, OperationKind.REMOVE_FILTER}:
            if self.predicate is None:
                raise ValueError(f"{self.kind.value} requires predicate.")
        elif self.kind is OperationKind.REPLACE_FILTER:
            if self.current is None or self.proposed is None:
                raise ValueError("REPLACE_FILTER requires current and proposed predicates.")
        elif self.kind in {OperationKind.SET_NUMERATOR, OperationKind.SET_DENOMINATOR}:
            _safe_metric_text(self.value, "operation.metric", identifier_only=True)
        elif self.kind is OperationKind.ENSURE_EXCLUDED_VALUES:
            _safe_metric_text(self.field, "operation.field", identifier_only=True)
            values = _string_tuple(self.values, "operation.values")
            if not values or any(not item.strip() for item in values):
                raise ValueError("operation.values must contain non-empty strings.")
            if len(set(values)) != len(values):
                raise ValueError("operation.values must not contain duplicates.")
            object.__setattr__(self, "values", values)
        elif self.kind is OperationKind.CREATE_METRIC:
            _safe_metric_text(self.value, "operation.metric", identifier_only=True)
            if self.definition is not None and not isinstance(self.definition, TypedMetricDefinition):
                raise ValueError("CREATE_METRIC definition must be a typed metric definition.")
        elif self.kind is OperationKind.RENAME_METRIC:
            _safe_metric_text(self.value, "operation.metric", identifier_only=True)
        elif self.kind is OperationKind.DEPRECATE_METRIC:
            _required_text(self.value, "operation.reason")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind.value}
        if self.kind in {
            OperationKind.SET_LABEL,
            OperationKind.SET_DESCRIPTION,
            OperationKind.SET_FORMAT,
        }:
            result["value"] = self.value
        elif self.kind in {OperationKind.ADD_FILTER, OperationKind.REMOVE_FILTER}:
            result["predicate"] = self.predicate.to_dict() if self.predicate else None
        elif self.kind is OperationKind.REPLACE_FILTER:
            result["current"] = self.current.to_dict() if self.current else None
            result["proposed"] = self.proposed.to_dict() if self.proposed else None
        elif self.kind in {OperationKind.SET_NUMERATOR, OperationKind.SET_DENOMINATOR}:
            result["metric"] = self.value
        elif self.kind is OperationKind.ENSURE_EXCLUDED_VALUES:
            result.update({"field": self.field, "values": list(self.values)})
        elif self.kind is OperationKind.CREATE_METRIC:
            result["proposed_name"] = self.value
            if self.definition is not None:
                result["definition"] = self.definition.to_dict()
        elif self.kind is OperationKind.RENAME_METRIC:
            result["new_name"] = self.value
        elif self.kind is OperationKind.DEPRECATE_METRIC:
            result["reason"] = self.value
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MetricOperation":
        if not isinstance(data, Mapping) or "kind" not in data:
            raise ValueError("operation must be an object with a kind discriminator.")
        try:
            kind = OperationKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported operation kind: {data.get('kind')!r}.") from exc
        if kind in {OperationKind.SET_LABEL, OperationKind.SET_DESCRIPTION, OperationKind.SET_FORMAT}:
            _require_fields(data, {"kind", "value"}, "operation")
            return cls(kind, value=data["value"])
        if kind in {OperationKind.ADD_FILTER, OperationKind.REMOVE_FILTER}:
            _require_fields(data, {"kind", "predicate"}, "operation")
            return cls(kind, predicate=FilterInput.from_dict(data["predicate"]))
        if kind is OperationKind.REPLACE_FILTER:
            _require_fields(data, {"kind", "current", "proposed"}, "operation")
            return cls(
                kind,
                current=FilterInput.from_dict(data["current"]),
                proposed=FilterInput.from_dict(data["proposed"]),
            )
        if kind in {OperationKind.SET_NUMERATOR, OperationKind.SET_DENOMINATOR}:
            _require_fields(data, {"kind", "metric"}, "operation")
            return cls(kind, value=data["metric"])
        if kind is OperationKind.ENSURE_EXCLUDED_VALUES:
            _require_fields(data, {"kind", "field", "values"}, "operation")
            return cls(kind, field=data["field"], values=_string_tuple(data["values"], "operation.values"))
        if kind is OperationKind.CREATE_METRIC:
            if set(data) == {"kind", "proposed_name"}:
                return cls(kind, value=data["proposed_name"])
            _require_fields(data, {"kind", "proposed_name", "definition"}, "operation")
            return cls(
                kind,
                value=data["proposed_name"],
                definition=TypedMetricDefinition.from_dict(data["definition"]),
            )
        key = {
            OperationKind.RENAME_METRIC: "new_name",
            OperationKind.DEPRECATE_METRIC: "reason",
        }[kind]
        _require_fields(data, {"kind", key}, "operation")
        return cls(kind, value=data[key])


def _validate_intent_operation(intent: ChangeIntent, kind: OperationKind) -> None:
    expected = {
        ChangeIntent.CREATE_METRIC: frozenset({OperationKind.CREATE_METRIC}),
        ChangeIntent.UPDATE_METRIC: UPDATE_OPERATION_KINDS,
        ChangeIntent.RENAME_METRIC: frozenset({OperationKind.RENAME_METRIC}),
        ChangeIntent.DEPRECATE_METRIC: frozenset({OperationKind.DEPRECATE_METRIC}),
        ChangeIntent.RECONCILE_TARGET_DRIFT: frozenset(),
    }[intent]
    if kind not in expected:
        raise ValueError(f"Operation {kind.value} is not valid for intent {intent.value}.")


@dataclass(frozen=True)
class MetricChangeRequest:
    schema_version: int
    change_id: str
    created_at: str
    user_request: str
    intent: ChangeIntent
    mode: ChangeMode
    canonical_metric_name: str
    canonical_file: str
    requested_semantic_change: str
    operation: MetricOperation
    affected_targets: tuple[TargetName, ...]
    target_support: Mapping[TargetName, TargetSupport]
    status: ChangeStatus
    approval_state: ApprovalState
    validation_state: ValidationState
    deployment_requested: bool
    deployment_state: DeploymentState
    assumptions: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}.")
        validate_change_id(self.change_id)
        validate_timestamp(self.created_at)
        _required_text(self.user_request, "user_request")
        _safe_metric_text(self.canonical_metric_name, "canonical_metric_name")
        _required_text(self.requested_semantic_change, "requested_semantic_change")
        if self.canonical_file != CANONICAL_FILE:
            raise ValueError(f"canonical_file must be {CANONICAL_FILE!r}.")
        if self.mode is not ChangeMode.PROPOSE:
            raise ValueError("mode must be PROPOSE for structured semantic change requests.")
        if self.status is not ChangeStatus.DRAFT:
            raise ValueError("status must be DRAFT for proposal input.")
        if self.approval_state is not ApprovalState.NOT_REQUESTED:
            raise ValueError("approval_state must be NOT_REQUESTED.")
        if self.validation_state is not ValidationState.NOT_RUN:
            raise ValueError("validation_state must be NOT_RUN.")
        if type(self.deployment_requested) is not bool or self.deployment_requested:
            raise ValueError("deployment_requested must be false.")
        if self.deployment_state is not DeploymentState.NOT_REQUESTED:
            raise ValueError("deployment_state must be NOT_REQUESTED.")
        _validate_intent_operation(self.intent, self.operation.kind)
        if not self.affected_targets or TargetName.CANONICAL_DBT not in self.affected_targets:
            raise ValueError("affected_targets must include CANONICAL_DBT.")
        if len(set(self.affected_targets)) != len(self.affected_targets):
            raise ValueError("affected_targets must not contain duplicates.")
        normalized_support = dict(self.target_support)
        if set(normalized_support) != set(self.affected_targets):
            raise ValueError("target_support keys must exactly match affected_targets.")
        object.__setattr__(self, "target_support", MappingProxyType(normalized_support))
        object.__setattr__(self, "assumptions", _string_tuple(self.assumptions, "assumptions"))
        object.__setattr__(self, "diagnostics", _string_tuple(self.diagnostics, "diagnostics"))

    @classmethod
    def create(
        cls,
        *,
        user_request: str,
        intent: ChangeIntent,
        canonical_metric_name: str,
        requested_semantic_change: str,
        operation: MetricOperation,
        affected_targets: tuple[TargetName, ...],
        target_support: Mapping[TargetName, TargetSupport],
        now: datetime | None = None,
        entropy: str | None = None,
        assumptions: tuple[str, ...] = (),
        diagnostics: tuple[str, ...] = (),
    ) -> "MetricChangeRequest":
        return cls(
            schema_version=SCHEMA_VERSION,
            change_id=new_change_id(now, entropy),
            created_at=utc_timestamp(now),
            user_request=user_request,
            intent=intent,
            mode=ChangeMode.PROPOSE,
            canonical_metric_name=canonical_metric_name,
            canonical_file=CANONICAL_FILE,
            requested_semantic_change=requested_semantic_change,
            operation=operation,
            affected_targets=affected_targets,
            target_support=target_support,
            status=ChangeStatus.DRAFT,
            approval_state=ApprovalState.NOT_REQUESTED,
            validation_state=ValidationState.NOT_RUN,
            deployment_requested=False,
            deployment_state=DeploymentState.NOT_REQUESTED,
            assumptions=assumptions,
            diagnostics=diagnostics,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "change_id": self.change_id,
            "created_at": self.created_at,
            "user_request": self.user_request,
            "intent": self.intent.value,
            "mode": self.mode.value,
            "canonical_metric_name": self.canonical_metric_name,
            "canonical_file": self.canonical_file,
            "requested_semantic_change": self.requested_semantic_change,
            "operation": self.operation.to_dict(),
            "affected_targets": [target.value for target in self.affected_targets],
            "target_support": {target.value: self.target_support[target].value for target in self.affected_targets},
            "status": self.status.value,
            "approval_state": self.approval_state.value,
            "validation_state": self.validation_state.value,
            "deployment_requested": self.deployment_requested,
            "deployment_state": self.deployment_state.value,
            "assumptions": list(self.assumptions),
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MetricChangeRequest":
        expected = {
            "schema_version", "change_id", "created_at", "user_request", "intent", "mode",
            "canonical_metric_name", "canonical_file", "requested_semantic_change", "operation",
            "affected_targets", "target_support", "status", "approval_state", "validation_state",
            "deployment_requested", "deployment_state", "assumptions", "diagnostics",
        }
        _require_fields(data, expected, "metric change request")
        try:
            targets = tuple(TargetName(item) for item in data["affected_targets"])
            raw_support = data["target_support"]
            if not isinstance(raw_support, Mapping):
                raise ValueError("target_support must be a JSON object.")
            support = {TargetName(key): TargetSupport(value) for key, value in raw_support.items()}
            return cls(
                schema_version=data["schema_version"],
                change_id=data["change_id"],
                created_at=data["created_at"],
                user_request=data["user_request"],
                intent=ChangeIntent(data["intent"]),
                mode=ChangeMode(data["mode"]),
                canonical_metric_name=data["canonical_metric_name"],
                canonical_file=data["canonical_file"],
                requested_semantic_change=data["requested_semantic_change"],
                operation=MetricOperation.from_dict(data["operation"]),
                affected_targets=targets,
                target_support=support,
                status=ChangeStatus(data["status"]),
                approval_state=ApprovalState(data["approval_state"]),
                validation_state=ValidationState(data["validation_state"]),
                deployment_requested=data["deployment_requested"],
                deployment_state=DeploymentState(data["deployment_state"]),
                assumptions=_string_tuple(data["assumptions"], "assumptions"),
                diagnostics=_string_tuple(data["diagnostics"], "diagnostics"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and not isinstance(exc, KeyError):
                raise
            raise ValueError(f"Invalid metric change request: {exc}") from exc
