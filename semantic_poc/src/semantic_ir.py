from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Generic, Mapping, TypeVar


class MetricPattern(str, Enum):
    COUNT = "COUNT"
    COLUMN_COUNT = "COLUMN_COUNT"
    SUM = "SUM"
    AVERAGE = "AVERAGE"
    MIN = "MIN"
    MAX = "MAX"
    DISTINCT_COUNT = "DISTINCT_COUNT"
    SCALED_SUM = "SCALED_SUM"
    SUM_ADDITION = "SUM_ADDITION"
    METRIC_ADDITION = "METRIC_ADDITION"
    SCALED_METRIC = "SCALED_METRIC"
    FILTERED_COUNT = "FILTERED_COUNT"
    RATIO = "RATIO"


class Aggregation(str, Enum):
    COUNT = "COUNT"
    SUM = "SUM"
    AVERAGE = "AVERAGE"
    MIN = "MIN"
    MAX = "MAX"
    DISTINCT_COUNT = "DISTINCT_COUNT"
    DERIVED = "DERIVED"
    RATIO = "RATIO"


class FilterOperator(str, Enum):
    EQ = "EQ"


class SupportClassification(str, Enum):
    SUPPORTED_PATTERN = "SUPPORTED_PATTERN"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"


class DiagnosticSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class TargetPlatform(str, Enum):
    POWER_BI = "POWER_BI"
    SNOWFLAKE = "SNOWFLAKE"
    CROSS_TARGET = "CROSS_TARGET"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    field: str | None = None
    target: TargetPlatform | None = None


@dataclass(frozen=True)
class CanonicalSourceLocation:
    file: str
    selector: str


@dataclass(frozen=True)
class PowerBIMapping:
    table: str | None = None
    measure: str | None = None
    format_string: str | None = None
    display_folder: str | None = None


@dataclass(frozen=True)
class SnowflakeMapping:
    logical_table: str | None = None
    metric_name: str | None = None
    synonyms: tuple[str, ...] = ()


FilterValue = bool | int | float | str


@dataclass(frozen=True)
class FilterPredicate:
    field: str
    operator: FilterOperator
    value: FilterValue
    keep_filters: bool = False

    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.field.casefold(),
            self.operator.value,
            type(self.value).__name__,
            repr(self.value),
            str(self.keep_filters),
        )


@dataclass(frozen=True)
class SemanticSignature:
    canonical_metric: str
    canonical_source: str
    aggregation: Aggregation | None
    source_model: str | None
    source_entity: str | None
    source_field: str | None
    source_fields: tuple[str, ...]
    scale_divisor: str | None
    filters: tuple[FilterPredicate, ...]
    numerator: str | None
    denominator: str | None
    metric_references: tuple[str, ...]
    public: bool
    trace_id: str


@dataclass(frozen=True)
class SemanticMetricIR:
    canonical_name: str
    label: str
    description: str
    public: bool
    source: CanonicalSourceLocation
    trace_id: str
    pattern: MetricPattern | None
    aggregation: Aggregation | None
    source_semantic_model: str | None
    source_entity: str | None
    source_logical_table: str | None
    source_physical_table: str | None
    source_field: str | None
    source_fields: tuple[str, ...] = ()
    scale_divisor: str | None = None
    filters: tuple[FilterPredicate, ...] = ()
    numerator: str | None = None
    denominator: str | None = None
    metric_references: tuple[str, ...] = ()
    semantic_format: str | None = None
    power_bi: PowerBIMapping = PowerBIMapping()
    snowflake: SnowflakeMapping = SnowflakeMapping()
    support: SupportClassification = SupportClassification.MANUAL_REVIEW_REQUIRED
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_fields", tuple(self.source_fields))
        object.__setattr__(self, "filters", tuple(sorted(self.filters, key=FilterPredicate.sort_key)))
        object.__setattr__(self, "metric_references", tuple(self.metric_references))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @property
    def signature(self) -> SemanticSignature:
        return SemanticSignature(
            canonical_metric=self.canonical_name,
            canonical_source=self.source.file,
            aggregation=self.aggregation,
            source_model=self.source_semantic_model,
            source_entity=self.source_entity,
            source_field=self.source_field,
            source_fields=self.source_fields,
            scale_divisor=self.scale_divisor,
            filters=self.filters,
            numerator=self.numerator,
            denominator=self.denominator,
            metric_references=self.metric_references,
            public=self.public,
            trace_id=self.trace_id,
        )


@dataclass(frozen=True)
class PatternClassification:
    pattern: MetricPattern | None
    support: SupportClassification
    diagnostics: tuple[Diagnostic, ...] = ()


DefinitionT = TypeVar("DefinitionT")


@dataclass(frozen=True)
class GenerationResult(Generic[DefinitionT]):
    target: TargetPlatform
    canonical_metric: str
    canonical_source: str
    trace_id: str
    support: SupportClassification
    definition: DefinitionT | None
    signature: SemanticSignature
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class CrossTargetValidationResult:
    canonical_metric: str
    trace_id: str
    support: SupportClassification
    valid: bool
    diagnostics: tuple[Diagnostic, ...] = ()


Validator = Callable[[SemanticMetricIR], tuple[Diagnostic, ...]]
Compiler = Callable[[SemanticMetricIR, Mapping[str, SemanticMetricIR]], Any]


@dataclass(frozen=True)
class PatternSpec:
    pattern: MetricPattern
    required_fields: tuple[str, ...]
    allowed_operators: frozenset[FilterOperator]
    validator: Validator
    dax_generator: Compiler
    snowflake_generator: Compiler
    support_status: SupportClassification


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PREDICATE = re.compile(
    r"^\s*(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?P<value>TRUE(?:\(\))?|FALSE(?:\(\))?|1|0|-?\d+(?:\.\d+)?|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")\s*$",
    re.IGNORECASE,
)
_UNKNOWN_OPERATOR = re.compile(r"!=|<>|<=|>=|<|>")
_SCALED_SOURCE_EXPRESSION = re.compile(
    r"^\s*(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*/\s*(?P<divisor>\d+(?:\.\d+)?)\s*$"
)
_BOOLEAN_FIELD_NAMES = {
    "is_valid_sbr_finisher",
    "event_context_flag",
    "record_integrity_flag",
    "individual_profile_flag",
    "model_residual_flag",
    "individual_hard_flag",
    "any_review_flag",
}
FILTER_CONTEXT_BEHAVIOR_META_KEY = "filter_context_behavior"
FILTER_CONTEXT_INTERSECT_EXISTING = "INTERSECT_EXISTING"


def _diagnostic(
    code: str,
    message: str,
    *,
    field: str | None = None,
    target: TargetPlatform | None = None,
) -> Diagnostic:
    return Diagnostic(code=code, message=message, field=field, target=target)


def _metric_ref_name(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        name = value.get("name")
        return name if isinstance(name, str) else None
    return None


def _metric_meta(metric: Mapping[str, Any]) -> Mapping[str, Any]:
    config = metric.get("config") or {}
    return (config.get("meta") or {}) if isinstance(config, Mapping) else {}


def _parse_literal(raw: str, field: str) -> FilterValue:
    upper = raw.upper()
    if upper in {"TRUE", "TRUE()"}:
        return True
    if upper in {"FALSE", "FALSE()"}:
        return False
    if raw in {"0", "1"} and field in _BOOLEAN_FIELD_NAMES:
        return raw == "1"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    if raw.startswith("'"):
        return raw[1:-1].replace("''", "'")
    return raw[1:-1].replace('""', '"')


def parse_filter_expression(expression: str | None) -> tuple[tuple[FilterPredicate, ...], tuple[Diagnostic, ...]]:
    if not isinstance(expression, str) or not expression.strip():
        return (), (_diagnostic("FILTER_EXPRESSION_MISSING", "Filtered count has no filter expression."),)
    if re.search(r"\bOR\b", expression, re.IGNORECASE):
        return (), (_diagnostic("FILTER_NESTED_LOGIC_UNSUPPORTED", "OR and nested filter logic require manual review."),)
    without_boolean_calls = re.sub(r"\b(?:TRUE|FALSE)\(\)", "", expression, flags=re.IGNORECASE)
    if "(" in without_boolean_calls or ")" in without_boolean_calls:
        return (), (_diagnostic("FILTER_NESTED_LOGIC_UNSUPPORTED", "Parenthesized filter logic requires manual review."),)
    if _UNKNOWN_OPERATOR.search(expression):
        return (), (_diagnostic("FILTER_OPERATOR_UNSUPPORTED", "Only equality filters are supported."),)

    predicates: list[FilterPredicate] = []
    for part in re.split(r"\s+AND\s+", expression, flags=re.IGNORECASE):
        match = _PREDICATE.fullmatch(part)
        if not match:
            return (), (
                _diagnostic(
                    "FILTER_EXPRESSION_UNSUPPORTED",
                    "Filter expression is outside the supported flat equality grammar.",
                ),
            )
        field = match.group("field")
        predicates.append(FilterPredicate(field, FilterOperator.EQ, _parse_literal(match.group("value"), field)))
    return tuple(sorted(predicates, key=FilterPredicate.sort_key)), ()


def _mapping_values(metric: Mapping[str, Any]) -> tuple[PowerBIMapping, SnowflakeMapping, str | None, bool]:
    meta = _metric_meta(metric)
    semantic = meta.get("semantic_contract") or {}
    power_bi = meta.get("power_bi") or {}
    snowflake = meta.get("snowflake") or {}
    return (
        PowerBIMapping(
            table=power_bi.get("table"),
            measure=power_bi.get("measure"),
            format_string=power_bi.get("format_string"),
            display_folder=power_bi.get("display_folder"),
        ),
        SnowflakeMapping(
            logical_table=snowflake.get("logical_table"),
            metric_name=snowflake.get("metric_name"),
            synonyms=tuple(snowflake.get("synonyms") or ()),
        ),
        semantic.get("format"),
        semantic.get("public") is True,
    )


def _semantic_model_context(
    semantic_model: Mapping[str, Any] | None,
    mapped_logical_table: str | None,
) -> tuple[str | None, str | None, str | None, str | None, tuple[Diagnostic, ...]]:
    if semantic_model is None:
        return None, None, None, None, (
            _diagnostic("SOURCE_MODEL_MISSING", "The metric's source semantic model could not be resolved."),
        )
    diagnostics: list[Diagnostic] = []
    primary_entities = [
        entity
        for entity in semantic_model.get("entities", []) or []
        if isinstance(entity, Mapping) and entity.get("type") == "primary"
    ]
    entity = primary_entities[0] if len(primary_entities) == 1 else None
    if entity is None:
        diagnostics.append(
            _diagnostic(
                "SOURCE_ENTITY_AMBIGUOUS",
                "Exactly one primary source entity is required.",
                field="source_entity",
            )
        )

    model_meta = ((semantic_model.get("config") or {}).get("meta") or {}).get("semantic_contract") or {}
    logical_tables = [item for item in model_meta.get("snowflake_logical_tables", []) or [] if isinstance(item, Mapping)]
    candidates = logical_tables
    entity_expr = entity.get("expr") if entity else None
    if entity_expr:
        candidates = [item for item in candidates if item.get("primary_key") == entity_expr]
    if mapped_logical_table:
        candidates = [item for item in candidates if item.get("name") == mapped_logical_table]
    table = candidates[0] if len(candidates) == 1 else None
    if table is None:
        diagnostics.append(
            _diagnostic(
                "SOURCE_TABLE_AMBIGUOUS",
                "The primary entity must resolve to exactly one canonical logical table.",
                field="source_logical_table",
            )
        )
    return (
        semantic_model.get("name"),
        entity.get("name") if entity else None,
        table.get("name") if table else None,
        table.get("base_table") if table else None,
        tuple(diagnostics),
    )


def _placeholder_ir(
    name: str,
    canonical_source: str,
    trace_id: str | None,
    diagnostic: Diagnostic,
) -> SemanticMetricIR:
    return SemanticMetricIR(
        canonical_name=name,
        label=name,
        description="",
        public=False,
        source=CanonicalSourceLocation(canonical_source, f"metrics[{name}]"),
        trace_id=trace_id or f"canonical:{canonical_source}#metric:{name}",
        pattern=None,
        aggregation=None,
        source_semantic_model=None,
        source_entity=None,
        source_logical_table=None,
        source_physical_table=None,
        source_field=None,
        scale_divisor=None,
        support=SupportClassification.MANUAL_REVIEW_REQUIRED,
        diagnostics=(diagnostic,),
    )


def build_metric_ir_index(
    semantic_manifest: Mapping[str, Any],
    canonical_yaml: Mapping[str, Any],
    *,
    canonical_source: str,
    trace_id: str | None = None,
) -> dict[str, SemanticMetricIR]:
    raw_metrics: dict[str, list[Mapping[str, Any]]] = {}
    for metric in semantic_manifest.get("metrics", []) or []:
        if isinstance(metric, Mapping) and isinstance(metric.get("name"), str):
            raw_metrics.setdefault(metric["name"], []).append(metric)

    yaml_meta = {
        metric["name"]: _metric_meta(metric)
        for metric in canonical_yaml.get("metrics", []) or []
        if isinstance(metric, Mapping) and isinstance(metric.get("name"), str)
    }
    semantic_models = {
        model["name"]: model
        for model in semantic_manifest.get("semantic_models", []) or []
        if isinstance(model, Mapping) and isinstance(model.get("name"), str)
    }
    measures: dict[str, list[tuple[Mapping[str, Any], str]]] = {}
    for model_name, model in semantic_models.items():
        for measure in model.get("measures", []) or []:
            if isinstance(measure, Mapping) and isinstance(measure.get("name"), str):
                measures.setdefault(measure["name"], []).append((measure, model_name))

    alias_counts: dict[tuple[TargetPlatform, str], int] = {}
    for metric_name, candidates in raw_metrics.items():
        if len(candidates) != 1:
            continue
        meta = yaml_meta.get(metric_name, _metric_meta(candidates[0]))
        for target, value in (
            (TargetPlatform.POWER_BI, (meta.get("power_bi") or {}).get("measure")),
            (TargetPlatform.SNOWFLAKE, (meta.get("snowflake") or {}).get("metric_name")),
        ):
            if isinstance(value, str):
                key = (target, value.casefold())
                alias_counts[key] = alias_counts.get(key, 0) + 1

    cache: dict[str, SemanticMetricIR] = {}

    def build(name: str, stack: tuple[str, ...] = ()) -> SemanticMetricIR:
        if name in cache:
            return cache[name]
        candidates = raw_metrics.get(name, [])
        if len(candidates) != 1:
            code = "CANONICAL_METRIC_MISSING" if not candidates else "CANONICAL_METRIC_AMBIGUOUS"
            result = _placeholder_ir(
                name,
                canonical_source,
                trace_id,
                _diagnostic(code, f"Canonical metric {name!r} must resolve exactly once."),
            )
            cache[name] = result
            return result
        if name in stack:
            result = _placeholder_ir(
                name,
                canonical_source,
                trace_id,
                _diagnostic("METRIC_REFERENCE_CYCLE", "Metric reference cycle requires manual review."),
            )
            cache[name] = result
            return result

        metric = candidates[0]
        metric = {**metric, "config": {"meta": yaml_meta.get(name, _metric_meta(metric))}}
        semantic_contract = _metric_meta(metric).get("semantic_contract") or {}
        filter_context_behavior = (
            semantic_contract.get(FILTER_CONTEXT_BEHAVIOR_META_KEY)
            if isinstance(semantic_contract, Mapping)
            else None
        )
        power_bi, snowflake, semantic_format, public = _mapping_values(metric)
        diagnostics: list[Diagnostic] = []
        for target, value in (
            (TargetPlatform.POWER_BI, power_bi.measure),
            (TargetPlatform.SNOWFLAKE, snowflake.metric_name),
        ):
            if value and alias_counts.get((target, value.casefold()), 0) > 1:
                diagnostics.append(
                    _diagnostic(
                        "TARGET_MAPPING_AMBIGUOUS",
                        f"Mapped {target.value} name {value!r} is not unique.",
                        target=target,
                    )
                )
        if public and (not power_bi.table or not power_bi.measure):
            diagnostics.append(
                _diagnostic(
                    "POWER_BI_MAPPING_MISSING",
                    "Public metric requires Power BI table and measure mappings.",
                    target=TargetPlatform.POWER_BI,
                )
            )
        if public and (not snowflake.logical_table or not snowflake.metric_name):
            diagnostics.append(
                _diagnostic(
                    "SNOWFLAKE_MAPPING_MISSING",
                    "Public metric requires Snowflake logical-table and metric mappings.",
                    target=TargetPlatform.SNOWFLAKE,
                )
            )

        type_params = metric.get("type_params") or {}
        pattern: MetricPattern | None = None
        aggregation: Aggregation | None = None
        source_model = source_entity = source_logical = source_physical = source_field = None
        source_fields: tuple[str, ...] = ()
        scale_divisor = None
        filters: tuple[FilterPredicate, ...] = ()
        numerator = _metric_ref_name(type_params.get("numerator"))
        denominator = _metric_ref_name(type_params.get("denominator"))
        metric_references: tuple[str, ...] = ()

        if metric.get("type") == "simple":
            measure_name = _metric_ref_name(type_params.get("measure"))
            measure_candidates = measures.get(measure_name or "", [])
            if len(measure_candidates) != 1:
                diagnostics.append(
                    _diagnostic(
                        "SOURCE_MEASURE_AMBIGUOUS",
                        f"Source measure {measure_name!r} must resolve exactly once.",
                        field="source_field",
                    )
                )
                measure = None
                model_name = None
            else:
                measure, model_name = measure_candidates[0]
            source_model, source_entity, source_logical, source_physical, source_diagnostics = _semantic_model_context(
                semantic_models.get(model_name or ""), snowflake.logical_table
            )
            diagnostics.extend(source_diagnostics)
            if measure is not None:
                agg = measure.get("agg")
                expression = str(measure.get("expr") or "").strip()
                if agg in {"sum", "count"} and re.sub(r"\s+", "", expression) in {"1", "*"}:
                    pattern = MetricPattern.COUNT
                    aggregation = Aggregation.COUNT
                    source_field = "*"
                elif agg == "sum" and _IDENTIFIER.fullmatch(expression):
                    pattern = MetricPattern.SUM
                    aggregation = Aggregation.SUM
                    source_field = expression
                elif agg in {"average", "avg"} and _IDENTIFIER.fullmatch(expression):
                    pattern = MetricPattern.AVERAGE
                    aggregation = Aggregation.AVERAGE
                    source_field = expression
                elif agg == "min" and _IDENTIFIER.fullmatch(expression):
                    pattern = MetricPattern.MIN
                    aggregation = Aggregation.MIN
                    source_field = expression
                elif agg == "max" and _IDENTIFIER.fullmatch(expression):
                    pattern = MetricPattern.MAX
                    aggregation = Aggregation.MAX
                    source_field = expression
                elif agg == "count" and _IDENTIFIER.fullmatch(expression):
                    pattern = MetricPattern.COLUMN_COUNT
                    aggregation = Aggregation.COUNT
                    source_field = expression
                elif agg == "count_distinct" and _IDENTIFIER.fullmatch(expression):
                    pattern = MetricPattern.DISTINCT_COUNT
                    aggregation = Aggregation.DISTINCT_COUNT
                    source_field = expression
                elif agg == "sum" and (scaled := _SCALED_SOURCE_EXPRESSION.fullmatch(expression)):
                    try:
                        divisor = Decimal(scaled.group("divisor"))
                    except InvalidOperation:
                        divisor = Decimal(0)
                    if divisor > 0:
                        pattern = MetricPattern.SCALED_SUM
                        aggregation = Aggregation.SUM
                        source_field = scaled.group("field")
                        scale_divisor = format(divisor.normalize(), "f")
                    else:
                        diagnostics.append(
                            _diagnostic(
                                "SCALE_DIVISOR_INVALID",
                                "Scaled sums require a positive finite divisor.",
                            )
                        )
                elif agg == "sum_boolean":
                    pattern = MetricPattern.FILTERED_COUNT
                    aggregation = Aggregation.COUNT
                    source_field = "*"
                    filters, filter_diagnostics = parse_filter_expression(expression)
                    diagnostics.extend(filter_diagnostics)
                    if filter_context_behavior is not None:
                        if (
                            filter_context_behavior
                            == FILTER_CONTEXT_INTERSECT_EXISTING
                            and len(filters) == 1
                            and isinstance(filters[0].value, bool)
                        ):
                            predicate = filters[0]
                            filters = (
                                FilterPredicate(
                                    predicate.field,
                                    predicate.operator,
                                    predicate.value,
                                    keep_filters=True,
                                ),
                            )
                        else:
                            diagnostics.append(
                                _diagnostic(
                                    "FILTER_CONTEXT_BEHAVIOR_UNSUPPORTED",
                                    "filter_context_behavior must be INTERSECT_EXISTING "
                                    "on exactly one Boolean filtered-count predicate.",
                                )
                            )
                else:
                    diagnostics.append(
                        _diagnostic(
                            "METRIC_PATTERN_UNSUPPORTED",
                            f"Metric type/aggregation {metric.get('type')!r}/{agg!r} is unsupported.",
                        )
                    )
        elif metric.get("type") == "ratio":
            pattern = MetricPattern.RATIO
            aggregation = Aggregation.RATIO
            numerator_ir = build(numerator or "", stack + (name,)) if numerator else None
            denominator_ir = build(denominator or "", stack + (name,)) if denominator else None
            if numerator_ir is None or denominator_ir is None:
                diagnostics.append(
                    _diagnostic("RATIO_REFERENCE_MISSING", "Ratio requires numerator and denominator references.")
                )
            else:
                if numerator_ir.support is not SupportClassification.SUPPORTED_PATTERN or denominator_ir.support is not SupportClassification.SUPPORTED_PATTERN:
                    diagnostics.append(
                        _diagnostic("RATIO_REFERENCE_UNSUPPORTED", "Ratio references must both be supported metrics.")
                    )
                contexts = {
                    (
                        numerator_ir.source_semantic_model,
                        numerator_ir.source_entity,
                        numerator_ir.source_logical_table,
                        numerator_ir.source_physical_table,
                    ),
                    (
                        denominator_ir.source_semantic_model,
                        denominator_ir.source_entity,
                        denominator_ir.source_logical_table,
                        denominator_ir.source_physical_table,
                    ),
                }
                if len(contexts) != 1:
                    diagnostics.append(
                        _diagnostic(
                            "RATIO_RELATIONSHIP_UNSUPPORTED",
                            "Ratio references cross source contexts or require relationship inference.",
                        )
                    )
                else:
                    source_model, source_entity, source_logical, source_physical = next(iter(contexts))
                if snowflake.logical_table and source_logical and snowflake.logical_table != source_logical:
                    diagnostics.append(
                        _diagnostic(
                            "SNOWFLAKE_RELATIONSHIP_UNSUPPORTED",
                            "Snowflake mapping differs from the ratio reference source table.",
                            target=TargetPlatform.SNOWFLAKE,
                        )
                    )
        elif metric.get("type") == "derived":
            raw_references = type_params.get("metrics") or ()
            aliases: dict[str, str] = {}
            if not isinstance(raw_references, (list, tuple)):
                diagnostics.append(
                    _diagnostic(
                        "DERIVED_REFERENCE_LIST_INVALID",
                        "Derived metrics require an ordered metrics list.",
                    )
                )
            else:
                for raw_reference in raw_references:
                    reference_name = _metric_ref_name(raw_reference)
                    alias = (
                        raw_reference.get("alias") or reference_name
                        if isinstance(raw_reference, Mapping)
                        else reference_name
                    )
                    if (
                        not isinstance(reference_name, str)
                        or not reference_name
                        or not isinstance(alias, str)
                        or not _IDENTIFIER.fullmatch(alias)
                        or alias.casefold() in aliases
                    ):
                        diagnostics.append(
                            _diagnostic(
                                "DERIVED_REFERENCE_AMBIGUOUS",
                                "Derived metric references require unique identifier aliases.",
                            )
                        )
                        aliases = {}
                        break
                    aliases[alias.casefold()] = reference_name

            expression = str(type_params.get("expr") or "").strip()
            compact_expression = re.sub(r"\s+", "", expression)
            additive_tokens = compact_expression.split("+")
            scaled_match = re.fullmatch(
                r"(?P<reference>[A-Za-z_][A-Za-z0-9_]*)/(?P<divisor>\d+(?:\.\d+)?)",
                compact_expression,
            )
            if (
                len(additive_tokens) >= 2
                and all(_IDENTIFIER.fullmatch(token) for token in additive_tokens)
                and all(token.casefold() in aliases for token in additive_tokens)
            ):
                pattern = MetricPattern.METRIC_ADDITION
                aggregation = Aggregation.DERIVED
                metric_references = tuple(aliases[token.casefold()] for token in additive_tokens)
            elif scaled_match and scaled_match.group("reference").casefold() in aliases:
                try:
                    divisor = Decimal(scaled_match.group("divisor"))
                except InvalidOperation:
                    divisor = Decimal(0)
                if divisor.is_finite() and divisor > 0:
                    pattern = MetricPattern.SCALED_METRIC
                    aggregation = Aggregation.DERIVED
                    metric_references = (
                        aliases[scaled_match.group("reference").casefold()],
                    )
                    scale_divisor = format(divisor.normalize(), "f")
                else:
                    diagnostics.append(
                        _diagnostic(
                            "SCALE_DIVISOR_INVALID",
                            "Scaled metric references require a positive finite divisor.",
                        )
                    )
            else:
                diagnostics.append(
                    _diagnostic(
                        "DERIVED_EXPRESSION_UNSUPPORTED",
                        "Only addition of metric references or one metric divided by a positive literal is supported.",
                    )
                )

            reference_irs = [
                build(reference, stack + (name,))
                for reference in metric_references
            ]
            if reference_irs:
                if any(
                    reference.support is not SupportClassification.SUPPORTED_PATTERN
                    for reference in reference_irs
                ):
                    diagnostics.append(
                        _diagnostic(
                            "DERIVED_REFERENCE_UNSUPPORTED",
                            "Derived metric references must all be supported metrics.",
                        )
                    )
                contexts = {
                    (
                        reference.source_semantic_model,
                        reference.source_entity,
                        reference.source_logical_table,
                        reference.source_physical_table,
                    )
                    for reference in reference_irs
                }
                if len(contexts) != 1:
                    diagnostics.append(
                        _diagnostic(
                            "DERIVED_RELATIONSHIP_UNSUPPORTED",
                            "Derived metric references cross source contexts or require relationship inference.",
                        )
                    )
                else:
                    source_model, source_entity, source_logical, source_physical = next(iter(contexts))
                if snowflake.logical_table and source_logical and snowflake.logical_table != source_logical:
                    diagnostics.append(
                        _diagnostic(
                            "SNOWFLAKE_RELATIONSHIP_UNSUPPORTED",
                            "Snowflake mapping differs from the derived references' source table.",
                            target=TargetPlatform.SNOWFLAKE,
                        )
                    )
        else:
            diagnostics.append(
                _diagnostic(
                    "METRIC_PATTERN_UNSUPPORTED",
                    f"Metric type {metric.get('type')!r} is unsupported.",
                )
            )
        if (
            filter_context_behavior is not None
            and pattern is not MetricPattern.FILTERED_COUNT
        ):
            diagnostics.append(
                _diagnostic(
                    "FILTER_CONTEXT_BEHAVIOR_NOT_APPLICABLE",
                    "filter_context_behavior is valid only for a filtered-count metric.",
                )
            )

        support = (
            SupportClassification.UNSUPPORTED
            if pattern is None and any(item.code == "METRIC_PATTERN_UNSUPPORTED" for item in diagnostics)
            else SupportClassification.MANUAL_REVIEW_REQUIRED
            if diagnostics
            else SupportClassification.SUPPORTED_PATTERN
        )
        result = SemanticMetricIR(
            canonical_name=name,
            label=metric.get("label") or name,
            description=metric.get("description") or "",
            public=public,
            source=CanonicalSourceLocation(canonical_source, f"metrics[{name}]"),
            trace_id=trace_id or f"canonical:{canonical_source}#metric:{name}",
            pattern=pattern,
            aggregation=aggregation,
            source_semantic_model=source_model,
            source_entity=source_entity,
            source_logical_table=source_logical,
            source_physical_table=source_physical,
            source_field=source_field,
            source_fields=source_fields,
            scale_divisor=scale_divisor,
            filters=filters,
            numerator=numerator,
            denominator=denominator,
            metric_references=metric_references,
            semantic_format=semantic_format,
            power_bi=power_bi,
            snowflake=snowflake,
            support=support,
            diagnostics=tuple(diagnostics),
        )
        cache[name] = result
        return result

    for metric_name in raw_metrics:
        build(metric_name)
    return cache


def build_canonical_metric_ir_index(
    canonical_yaml: Mapping[str, Any],
    *,
    canonical_source: str,
    trace_id: str | None = None,
) -> dict[str, SemanticMetricIR]:
    """Build the shared typed IR directly from the canonical dbt contract.

    dbt's semantic manifest and the canonical YAML expose the same metric and
    semantic-model structures used by this POC. Brownfield discovery may run
    before a fresh dbt parse, but it must not maintain a second interpretation
    of those structures. This entry point therefore deliberately delegates to
    the same builder used for compiled manifests.
    """

    return build_metric_ir_index(
        canonical_yaml,
        canonical_yaml,
        canonical_source=canonical_source,
        trace_id=trace_id,
    )


def canonical_metric_to_ir(
    metric_name: str,
    semantic_manifest: Mapping[str, Any],
    canonical_yaml: Mapping[str, Any],
    *,
    canonical_source: str,
    trace_id: str | None = None,
) -> SemanticMetricIR:
    if not isinstance(metric_name, str) or not metric_name:
        raise ValueError("metric_name must be a non-empty exact canonical metric name.")
    index = build_metric_ir_index(
        semantic_manifest,
        canonical_yaml,
        canonical_source=canonical_source,
        trace_id=trace_id,
    )
    if metric_name not in index:
        raise KeyError(f"Canonical metric does not exist: {metric_name}")
    return index[metric_name]


def _validate_count(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    missing = []
    if metric.aggregation is not Aggregation.COUNT:
        missing.append("aggregation")
    if metric.source_field != "*":
        missing.append("source_field")
    if not metric.source_physical_table:
        missing.append("source_physical_table")
    if missing:
        return (_diagnostic("COUNT_FIELDS_MISSING", "COUNT is missing: " + ", ".join(missing)),)
    return ()


def _validate_filtered_count(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    diagnostics = list(_validate_count(metric))
    if not metric.filters:
        diagnostics.append(_diagnostic("FILTERS_MISSING", "FILTERED_COUNT requires at least one filter."))
    for predicate in metric.filters:
        if predicate.operator is not FilterOperator.EQ:
            diagnostics.append(_diagnostic("FILTER_OPERATOR_UNSUPPORTED", "Only EQ filters are supported."))
    return tuple(diagnostics)


def _validate_column_aggregate(
    metric: SemanticMetricIR,
    expected: Aggregation,
    code: str,
) -> tuple[Diagnostic, ...]:
    missing = []
    if metric.aggregation is not expected:
        missing.append("aggregation")
    if not metric.source_field or metric.source_field == "*":
        missing.append("source_field")
    if not metric.source_physical_table:
        missing.append("source_physical_table")
    if missing:
        return (_diagnostic(code, f"{metric.pattern.value if metric.pattern else 'metric'} is missing: " + ", ".join(missing)),)
    return ()


def _validate_sum(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    return _validate_column_aggregate(metric, Aggregation.SUM, "SUM_FIELDS_MISSING")


def _validate_average(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    return _validate_column_aggregate(metric, Aggregation.AVERAGE, "AVERAGE_FIELDS_MISSING")


def _validate_min(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    return _validate_column_aggregate(metric, Aggregation.MIN, "MIN_FIELDS_MISSING")


def _validate_max(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    return _validate_column_aggregate(metric, Aggregation.MAX, "MAX_FIELDS_MISSING")


def _validate_column_count(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    return _validate_column_aggregate(metric, Aggregation.COUNT, "COLUMN_COUNT_FIELDS_MISSING")


def _validate_distinct_count(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    return _validate_column_aggregate(
        metric,
        Aggregation.DISTINCT_COUNT,
        "DISTINCT_COUNT_FIELDS_MISSING",
    )


def _validate_scaled_sum(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    diagnostics = list(_validate_sum(metric))
    try:
        divisor = Decimal(metric.scale_divisor or "")
    except InvalidOperation:
        divisor = Decimal(0)
    if divisor <= 0:
        diagnostics.append(
            _diagnostic(
                "SCALE_DIVISOR_INVALID",
                "SCALED_SUM requires a positive finite scale_divisor.",
            )
        )
    return tuple(diagnostics)


def _validate_sum_addition(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    missing: list[str] = []
    if metric.aggregation is not Aggregation.DERIVED:
        missing.append("aggregation")
    if len(metric.source_fields) < 2 or any(not field or field == "*" for field in metric.source_fields):
        missing.append("source_fields")
    if not metric.source_physical_table:
        missing.append("source_physical_table")
    if missing:
        return (
            _diagnostic(
                "SUM_ADDITION_FIELDS_MISSING",
                "SUM_ADDITION is missing: " + ", ".join(missing),
            ),
        )
    return ()


def _validate_metric_addition(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    missing: list[str] = []
    if metric.aggregation is not Aggregation.DERIVED:
        missing.append("aggregation")
    if len(metric.metric_references) < 2:
        missing.append("metric_references")
    if missing:
        return (
            _diagnostic(
                "METRIC_ADDITION_FIELDS_MISSING",
                "METRIC_ADDITION is missing: " + ", ".join(missing),
            ),
        )
    return ()


def _validate_scaled_metric(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    if metric.aggregation is not Aggregation.DERIVED or len(metric.metric_references) != 1:
        diagnostics.append(
            _diagnostic(
                "SCALED_METRIC_FIELDS_MISSING",
                "SCALED_METRIC requires DERIVED aggregation and exactly one metric reference.",
            )
        )
    try:
        divisor = Decimal(metric.scale_divisor or "")
    except InvalidOperation:
        divisor = Decimal(0)
    if not divisor.is_finite() or divisor <= 0:
        diagnostics.append(
            _diagnostic(
                "SCALE_DIVISOR_INVALID",
                "SCALED_METRIC requires a positive finite scale_divisor.",
            )
        )
    return tuple(diagnostics)


def _validate_ratio(metric: SemanticMetricIR) -> tuple[Diagnostic, ...]:
    missing = []
    if metric.aggregation is not Aggregation.RATIO:
        missing.append("aggregation")
    if not metric.numerator:
        missing.append("numerator")
    if not metric.denominator:
        missing.append("denominator")
    if missing:
        return (_diagnostic("RATIO_FIELDS_MISSING", "RATIO is missing: " + ", ".join(missing)),)
    return ()


def _dax_table(name: str) -> str:
    return name if _IDENTIFIER.fullmatch(name) else "'" + name.replace("'", "''") + "'"


def _dax_bracket(name: str) -> str:
    return "[" + name.replace("]", "]]" ) + "]"


def render_dax_literal(value: FilterValue) -> str:
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"
    if isinstance(value, str):
        return '"' + value.replace('"', '""') + '"'
    return str(value)


def _snowflake_identifier(name: str) -> str:
    return name if _IDENTIFIER.fullmatch(name) else '"' + name.replace('"', '""') + '"'


def render_snowflake_literal(value: FilterValue) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def render_canonical_filter_expression(predicates: tuple[FilterPredicate, ...]) -> str:
    """Render typed equality predicates for a canonical dbt sum_boolean draft."""

    return " AND ".join(
        f"{_snowflake_identifier(predicate.field)} = {render_snowflake_literal(predicate.value)}"
        for predicate in predicates
    )


def _dax_filter(table: str, predicate: FilterPredicate) -> str:
    expression = f"{_dax_table(table)}{_dax_bracket(predicate.field)} = {render_dax_literal(predicate.value)}"
    return f"KEEPFILTERS({expression})" if predicate.keep_filters else expression


def _snowflake_filter(predicate: FilterPredicate) -> str:
    field = _snowflake_identifier(predicate.field)
    if predicate.value is True:
        return field
    if predicate.value is False:
        return f"NOT {field}"
    return f"{field} = {render_snowflake_literal(predicate.value)}"


def _compile_dax_count(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    return f"COUNTROWS({_dax_table(metric.source_physical_table or '')})"


def _dax_column(metric: SemanticMetricIR) -> str:
    return f"{_dax_table(metric.source_physical_table or '')}{_dax_bracket(metric.source_field or '')}"


def _compile_dax_sum(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    return f"SUM({_dax_column(metric)})"


def _compile_dax_average(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    return f"AVERAGE({_dax_column(metric)})"


def _compile_dax_min(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    return f"MIN({_dax_column(metric)})"


def _compile_dax_max(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    return f"MAX({_dax_column(metric)})"


def _compile_dax_column_count(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    return f"COUNT({_dax_column(metric)})"


def _compile_dax_distinct_count(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    return f"DISTINCTCOUNT({_dax_column(metric)})"


def _compile_dax_scaled_sum(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    return f"DIVIDE( SUM({_dax_column(metric)}), {metric.scale_divisor} )"


def _compile_dax_sum_addition(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    table = _dax_table(metric.source_physical_table or "")
    return " + ".join(
        f"SUM({table}{_dax_bracket(field)})"
        for field in metric.source_fields
    )


def _compile_dax_filtered_count(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> str:
    table = metric.source_physical_table or ""
    filters = ", ".join(_dax_filter(table, predicate) for predicate in metric.filters)
    return f"CALCULATE( COUNTROWS({_dax_table(table)}), {filters} )"


def _reference_dax_name(reference: SemanticMetricIR, index: Mapping[str, SemanticMetricIR]) -> str:
    name = reference.power_bi.measure or reference.label
    matches = [
        item
        for item in index.values()
        if (item.power_bi.measure or item.label).casefold() == name.casefold()
    ]
    if len(matches) != 1:
        raise ValueError(f"Power BI measure reference {name!r} is ambiguous.")
    return name


def _compile_dax_ratio(metric: SemanticMetricIR, index: Mapping[str, SemanticMetricIR]) -> str:
    numerator = index[metric.numerator or ""]
    denominator = index[metric.denominator or ""]
    return f"DIVIDE( {_dax_bracket(_reference_dax_name(numerator, index))}, {_dax_bracket(_reference_dax_name(denominator, index))} )"


def _compile_dax_metric_addition(metric: SemanticMetricIR, index: Mapping[str, SemanticMetricIR]) -> str:
    references = [index[name] for name in metric.metric_references]
    return " + ".join(
        _dax_bracket(_reference_dax_name(reference, index))
        for reference in references
    )


def _compile_dax_scaled_metric(metric: SemanticMetricIR, index: Mapping[str, SemanticMetricIR]) -> str:
    reference = index[metric.metric_references[0]]
    return (
        f"DIVIDE( {_dax_bracket(_reference_dax_name(reference, index))}, "
        f"{metric.scale_divisor} )"
    )


def _snowflake_metric_name(metric: SemanticMetricIR) -> str:
    return metric.snowflake.metric_name or metric.canonical_name


def _snowflake_common(metric: SemanticMetricIR, expression: str) -> dict[str, Any]:
    return {
        "name": _snowflake_metric_name(metric),
        "expr": expression,
        "description": metric.description,
        "synonyms": list(metric.snowflake.synonyms),
        "access_modifier": "public_access" if metric.public else "private_access",
    }


def _compile_snowflake_count(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    return _snowflake_common(metric, "COUNT(*)")


def _snowflake_field(metric: SemanticMetricIR) -> str:
    return _snowflake_identifier(metric.source_field or "")


def _compile_snowflake_sum(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    return _snowflake_common(metric, f"SUM({_snowflake_field(metric)})")


def _compile_snowflake_average(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    return _snowflake_common(metric, f"AVG({_snowflake_field(metric)})")


def _compile_snowflake_min(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    return _snowflake_common(metric, f"MIN({_snowflake_field(metric)})")


def _compile_snowflake_max(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    return _snowflake_common(metric, f"MAX({_snowflake_field(metric)})")


def _compile_snowflake_column_count(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    return _snowflake_common(metric, f"COUNT({_snowflake_field(metric)})")


def _compile_snowflake_distinct_count(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    field = _snowflake_field(metric)
    # DAX DISTINCTCOUNT treats BLANK as one distinct value. SQL COUNT(DISTINCT)
    # drops NULL, so the explicit NULL term preserves Power BI semantics.
    return _snowflake_common(
        metric,
        f"COUNT(DISTINCT {field}) + IFF(COUNT_IF({field} IS NULL) > 0, 1, 0)",
    )


def _compile_snowflake_scaled_sum(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    return _snowflake_common(
        metric,
        f"SUM({_snowflake_field(metric)}) / {metric.scale_divisor}",
    )


def _compile_snowflake_sum_addition(
    metric: SemanticMetricIR,
    _index: Mapping[str, SemanticMetricIR],
) -> dict[str, Any]:
    aggregates = [
        f"SUM({_snowflake_identifier(field)})"
        for field in metric.source_fields
    ]
    all_blank = " AND ".join(f"{aggregate} IS NULL" for aggregate in aggregates)
    total = " + ".join(f"COALESCE({aggregate}, 0)" for aggregate in aggregates)
    return _snowflake_common(metric, f"IFF({all_blank}, NULL, {total})")


def _compile_snowflake_filtered_count(metric: SemanticMetricIR, _index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    expression = " AND ".join(_snowflake_filter(predicate) for predicate in metric.filters)
    return _snowflake_common(metric, f"COUNT_IF({expression})")


def _compile_snowflake_ratio(metric: SemanticMetricIR, index: Mapping[str, SemanticMetricIR]) -> dict[str, Any]:
    numerator = index[metric.numerator or ""]
    denominator = index[metric.denominator or ""]
    logical_table = metric.snowflake.logical_table or metric.source_logical_table or ""
    prefix = _snowflake_identifier(logical_table)
    numerator_name = _snowflake_identifier(_snowflake_metric_name(numerator))
    denominator_name = _snowflake_identifier(_snowflake_metric_name(denominator))
    expression = f"{prefix}.{numerator_name} / NULLIF({prefix}.{denominator_name}, 0)"
    return _snowflake_common(metric, expression)


def _snowflake_metric_reference(
    owner: SemanticMetricIR,
    reference_name: str,
    index: Mapping[str, SemanticMetricIR],
) -> str:
    reference = index[reference_name]
    owner_table = owner.snowflake.logical_table or owner.source_logical_table
    reference_table = reference.snowflake.logical_table or reference.source_logical_table
    if not owner_table or not reference_table or owner_table.casefold() != reference_table.casefold():
        raise ValueError(
            f"Snowflake metric reference {reference_name!r} does not resolve on the same logical table."
        )
    return (
        f"{_snowflake_identifier(owner_table)}."
        f"{_snowflake_identifier(_snowflake_metric_name(reference))}"
    )


def _compile_snowflake_metric_addition(
    metric: SemanticMetricIR,
    index: Mapping[str, SemanticMetricIR],
) -> dict[str, Any]:
    references = [
        _snowflake_metric_reference(metric, reference, index)
        for reference in metric.metric_references
    ]
    all_blank = " AND ".join(f"{reference} IS NULL" for reference in references)
    total = " + ".join(f"COALESCE({reference}, 0)" for reference in references)
    return _snowflake_common(metric, f"IFF({all_blank}, NULL, {total})")


def _compile_snowflake_scaled_metric(
    metric: SemanticMetricIR,
    index: Mapping[str, SemanticMetricIR],
) -> dict[str, Any]:
    reference = _snowflake_metric_reference(metric, metric.metric_references[0], index)
    return _snowflake_common(metric, f"{reference} / {metric.scale_divisor}")


PATTERN_REGISTRY: Mapping[MetricPattern, PatternSpec] = MappingProxyType(
    {
        MetricPattern.COUNT: PatternSpec(
            MetricPattern.COUNT,
            ("aggregation", "source_physical_table", "source_field"),
            frozenset(),
            _validate_count,
            _compile_dax_count,
            _compile_snowflake_count,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.COLUMN_COUNT: PatternSpec(
            MetricPattern.COLUMN_COUNT,
            ("aggregation", "source_physical_table", "source_field"),
            frozenset(),
            _validate_column_count,
            _compile_dax_column_count,
            _compile_snowflake_column_count,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.SUM: PatternSpec(
            MetricPattern.SUM,
            ("aggregation", "source_physical_table", "source_field"),
            frozenset(),
            _validate_sum,
            _compile_dax_sum,
            _compile_snowflake_sum,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.AVERAGE: PatternSpec(
            MetricPattern.AVERAGE,
            ("aggregation", "source_physical_table", "source_field"),
            frozenset(),
            _validate_average,
            _compile_dax_average,
            _compile_snowflake_average,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.MIN: PatternSpec(
            MetricPattern.MIN,
            ("aggregation", "source_physical_table", "source_field"),
            frozenset(),
            _validate_min,
            _compile_dax_min,
            _compile_snowflake_min,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.MAX: PatternSpec(
            MetricPattern.MAX,
            ("aggregation", "source_physical_table", "source_field"),
            frozenset(),
            _validate_max,
            _compile_dax_max,
            _compile_snowflake_max,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.DISTINCT_COUNT: PatternSpec(
            MetricPattern.DISTINCT_COUNT,
            ("aggregation", "source_physical_table", "source_field"),
            frozenset(),
            _validate_distinct_count,
            _compile_dax_distinct_count,
            _compile_snowflake_distinct_count,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.SCALED_SUM: PatternSpec(
            MetricPattern.SCALED_SUM,
            ("aggregation", "source_physical_table", "source_field", "scale_divisor"),
            frozenset(),
            _validate_scaled_sum,
            _compile_dax_scaled_sum,
            _compile_snowflake_scaled_sum,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.SUM_ADDITION: PatternSpec(
            MetricPattern.SUM_ADDITION,
            ("aggregation", "source_physical_table", "source_fields"),
            frozenset(),
            _validate_sum_addition,
            _compile_dax_sum_addition,
            _compile_snowflake_sum_addition,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.METRIC_ADDITION: PatternSpec(
            MetricPattern.METRIC_ADDITION,
            ("aggregation", "metric_references"),
            frozenset(),
            _validate_metric_addition,
            _compile_dax_metric_addition,
            _compile_snowflake_metric_addition,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.SCALED_METRIC: PatternSpec(
            MetricPattern.SCALED_METRIC,
            ("aggregation", "metric_references", "scale_divisor"),
            frozenset(),
            _validate_scaled_metric,
            _compile_dax_scaled_metric,
            _compile_snowflake_scaled_metric,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.FILTERED_COUNT: PatternSpec(
            MetricPattern.FILTERED_COUNT,
            ("aggregation", "source_physical_table", "source_field", "filters"),
            frozenset({FilterOperator.EQ}),
            _validate_filtered_count,
            _compile_dax_filtered_count,
            _compile_snowflake_filtered_count,
            SupportClassification.SUPPORTED_PATTERN,
        ),
        MetricPattern.RATIO: PatternSpec(
            MetricPattern.RATIO,
            ("aggregation", "numerator", "denominator"),
            frozenset(),
            _validate_ratio,
            _compile_dax_ratio,
            _compile_snowflake_ratio,
            SupportClassification.SUPPORTED_PATTERN,
        ),
    }
)


def classify_pattern(metric_ir: SemanticMetricIR) -> PatternClassification:
    if metric_ir.pattern is None or metric_ir.pattern not in PATTERN_REGISTRY:
        support = (
            SupportClassification.UNSUPPORTED
            if metric_ir.support is SupportClassification.UNSUPPORTED
            else SupportClassification.MANUAL_REVIEW_REQUIRED
        )
        return PatternClassification(metric_ir.pattern, support, metric_ir.diagnostics)
    spec = PATTERN_REGISTRY[metric_ir.pattern]
    diagnostics = metric_ir.diagnostics + spec.validator(metric_ir)
    support = metric_ir.support
    if diagnostics and support is SupportClassification.SUPPORTED_PATTERN:
        support = SupportClassification.MANUAL_REVIEW_REQUIRED
    return PatternClassification(metric_ir.pattern, support, diagnostics)


def _generation_failure(
    metric_ir: SemanticMetricIR,
    target: TargetPlatform,
    support: SupportClassification,
    diagnostics: tuple[Diagnostic, ...],
) -> GenerationResult[Any]:
    return GenerationResult(
        target=target,
        canonical_metric=metric_ir.canonical_name,
        canonical_source=metric_ir.source.file,
        trace_id=metric_ir.trace_id,
        support=support,
        definition=None,
        signature=metric_ir.signature,
        diagnostics=diagnostics,
    )


def generate_dax_definition(
    metric_ir: SemanticMetricIR,
    metric_index: Mapping[str, SemanticMetricIR],
) -> GenerationResult[str]:
    classification = classify_pattern(metric_ir)
    diagnostics = list(classification.diagnostics)
    if metric_ir.public and (not metric_ir.power_bi.table or not metric_ir.power_bi.measure):
        diagnostics.append(
            _diagnostic(
                "POWER_BI_MAPPING_MISSING",
                "Public metric cannot generate DAX without its target mapping.",
                target=TargetPlatform.POWER_BI,
            )
        )
    if classification.support is not SupportClassification.SUPPORTED_PATTERN or diagnostics:
        return _generation_failure(
            metric_ir,
            TargetPlatform.POWER_BI,
            classification.support
            if classification.support is not SupportClassification.SUPPORTED_PATTERN
            else SupportClassification.MANUAL_REVIEW_REQUIRED,
            tuple(diagnostics),
        )
    try:
        definition = PATTERN_REGISTRY[metric_ir.pattern].dax_generator(metric_ir, metric_index)  # type: ignore[index]
    except (KeyError, ValueError) as exc:
        return _generation_failure(
            metric_ir,
            TargetPlatform.POWER_BI,
            SupportClassification.MANUAL_REVIEW_REQUIRED,
            (_diagnostic("POWER_BI_REFERENCE_AMBIGUOUS", str(exc), target=TargetPlatform.POWER_BI),),
        )
    return GenerationResult(
        TargetPlatform.POWER_BI,
        metric_ir.canonical_name,
        metric_ir.source.file,
        metric_ir.trace_id,
        SupportClassification.SUPPORTED_PATTERN,
        definition,
        metric_ir.signature,
        (),
    )


def generate_snowflake_definition(
    metric_ir: SemanticMetricIR,
    metric_index: Mapping[str, SemanticMetricIR],
) -> GenerationResult[dict[str, Any]]:
    classification = classify_pattern(metric_ir)
    diagnostics = list(classification.diagnostics)
    if metric_ir.public and (not metric_ir.snowflake.logical_table or not metric_ir.snowflake.metric_name):
        diagnostics.append(
            _diagnostic(
                "SNOWFLAKE_MAPPING_MISSING",
                "Public metric cannot generate Snowflake YAML without its target mapping.",
                target=TargetPlatform.SNOWFLAKE,
            )
        )
    if classification.support is not SupportClassification.SUPPORTED_PATTERN or diagnostics:
        return _generation_failure(
            metric_ir,
            TargetPlatform.SNOWFLAKE,
            classification.support
            if classification.support is not SupportClassification.SUPPORTED_PATTERN
            else SupportClassification.MANUAL_REVIEW_REQUIRED,
            tuple(diagnostics),
        )
    try:
        definition = PATTERN_REGISTRY[metric_ir.pattern].snowflake_generator(metric_ir, metric_index)  # type: ignore[index]
    except (KeyError, ValueError) as exc:
        return _generation_failure(
            metric_ir,
            TargetPlatform.SNOWFLAKE,
            SupportClassification.MANUAL_REVIEW_REQUIRED,
            (_diagnostic("SNOWFLAKE_REFERENCE_AMBIGUOUS", str(exc), target=TargetPlatform.SNOWFLAKE),),
        )
    return GenerationResult(
        TargetPlatform.SNOWFLAKE,
        metric_ir.canonical_name,
        metric_ir.source.file,
        metric_ir.trace_id,
        SupportClassification.SUPPORTED_PATTERN,
        definition,
        metric_ir.signature,
        (),
    )


def validate_cross_target(
    metric_ir: SemanticMetricIR,
    dax_result: GenerationResult[str],
    snowflake_result: GenerationResult[dict[str, Any]],
) -> CrossTargetValidationResult:
    diagnostics: list[Diagnostic] = []
    if dax_result.target is not TargetPlatform.POWER_BI or snowflake_result.target is not TargetPlatform.SNOWFLAKE:
        diagnostics.append(_diagnostic("TARGET_RESULT_MISMATCH", "Generation results use unexpected targets."))
    for result in (dax_result, snowflake_result):
        if result.canonical_metric != metric_ir.canonical_name or result.canonical_source != metric_ir.source.file:
            diagnostics.append(
                _diagnostic("CANONICAL_TRACE_MISMATCH", "Target result does not identify the same canonical metric/source.")
            )
        if result.trace_id != metric_ir.trace_id:
            diagnostics.append(_diagnostic("TRACE_ID_MISMATCH", "Target result trace ID differs from the IR."))
        if result.signature != metric_ir.signature:
            diagnostics.append(
                _diagnostic("SEMANTIC_SIGNATURE_MISMATCH", "Target result was not generated from the same semantic signature.")
            )
        if result.support is not SupportClassification.SUPPORTED_PATTERN or result.definition is None:
            diagnostics.extend(result.diagnostics)
    valid = not diagnostics
    return CrossTargetValidationResult(
        canonical_metric=metric_ir.canonical_name,
        trace_id=metric_ir.trace_id,
        support=SupportClassification.SUPPORTED_PATTERN if valid else SupportClassification.MANUAL_REVIEW_REQUIRED,
        valid=valid,
        diagnostics=tuple(diagnostics),
    )
