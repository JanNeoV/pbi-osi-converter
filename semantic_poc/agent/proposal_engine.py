from __future__ import annotations

import hashlib
import json
import re
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import yaml

from semantic_poc.src.models import (
    CANONICAL_SOURCE,
    DBT_SEMANTIC_MANIFEST,
    DBT_SEMANTIC_YAML,
    OUTPUT_DIR,
    PBI_DEFINITION_DIR,
    PROJECT_ROOT,
    load_json,
    load_yaml,
    metric_ref_name,
)
from semantic_poc.src.semantic_ir import (
    Aggregation,
    Diagnostic,
    FilterOperator,
    FilterPredicate,
    MetricPattern,
    PowerBIMapping,
    SemanticMetricIR,
    SupportClassification,
    build_canonical_metric_ir_index,
    build_metric_ir_index,
    classify_pattern,
    generate_dax_definition,
    generate_snowflake_definition,
    validate_cross_target,
)

from .diff_models import canonical_pseudo_diff, definition_diff, text_diff
from .canonical_apply import CanonicalPatchError, render_candidate
from .inspection import MetricAmbiguousError, MetricNotFoundError, resolve_metric
from .proposal_models import (
    LocalApplicationState,
    PROPOSAL_SCHEMA_VERSION,
    ProposalDiagnostic,
    ProposalRecord,
    ProposalStatus,
    RiskLevel,
)
from .schemas import (
    ApprovalState,
    CANONICAL_FILE,
    ChangeIntent,
    DeploymentState,
    FilterInput,
    MetricChangeRequest,
    OperationKind,
    RequestFilterOperator,
    TargetName,
    TargetSupport,
    ValidationState,
)


FORMAT_STRINGS = {
    "whole_number": "0",
    "decimal_2": "#,0.00",
    "percentage_1_decimal": "0.0%",
}
REQUIRED_VALIDATION = (
    "dbt --no-version-check parse",
    "python semantic_poc/run_quality_checks.py",
    "python semantic_poc/run_poc.py --strict",
    "python -m pytest semantic_poc/tests -q -p no:cacheprovider",
)
SAFETY_ASSUMPTIONS = (
    "Canonical application requires an explicit approval and a current protected-source snapshot.",
    "Deployment is not available from semantic-agent; local application never requests deployment.",
)
CORE_OUTPUT_NAMES = (
    "dbt_semantics.json",
    "powerbi_semantics.json",
    "proposed_powerbi_patch.json",
    "semantic_compatibility.md",
    "snowflake_semantic_view.yml",
)
UPSTREAM_FINISHER_SOURCE = PROJECT_ROOT / "models" / "intermediate" / "int_result_times.sql"


class ProposalEngineError(RuntimeError):
    code = "PROPOSAL_FAILED"

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": str(self)}}


class ProposalInputError(ProposalEngineError):
    code = "INVALID_PROPOSAL_REQUEST"


class ProposalManualReview(ProposalEngineError):
    code = "MANUAL_REVIEW_REQUIRED"

    def __init__(self, message: str, *, diagnostic_code: str = "MANUAL_REVIEW_REQUIRED") -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.is_dir():
        return digest.hexdigest()
    for item in sorted(
        (candidate for candidate in path.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(path).as_posix(),
    ):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
    return digest.hexdigest()


def capture_source_snapshot(
    *,
    semantic_yaml_path: Path = DBT_SEMANTIC_YAML,
    manifest_path: Path = DBT_SEMANTIC_MANIFEST,
    powerbi_definition_dir: Path = PBI_DEFINITION_DIR,
    output_dir: Path = OUTPUT_DIR,
) -> dict[str, Any]:
    if not semantic_yaml_path.is_file():
        raise ProposalInputError(f"Canonical semantic YAML does not exist: {semantic_yaml_path}")
    if not manifest_path.is_file():
        raise ProposalInputError(
            "Compiled dbt semantic manifest is missing; run `dbt --no-version-check parse` before proposing."
        )
    manifest = load_json(manifest_path)
    semantic_manifest = {
        "semantic_models": manifest.get("semantic_models", []),
        "metrics": manifest.get("metrics", []),
    }
    output_hashes = {
        name: _sha256((output_dir / name).read_bytes()) if (output_dir / name).is_file() else None
        for name in CORE_OUTPUT_NAMES
    }
    components: dict[str, Any] = {
        "canonical_sha256": _sha256(semantic_yaml_path.read_bytes()),
        "semantic_manifest_sha256": _sha256(_json_bytes(semantic_manifest)),
        "upstream_finisher_evidence_sha256": _sha256(UPSTREAM_FINISHER_SOURCE.read_bytes()),
        "power_bi_definition_tree_sha256": _tree_hash(powerbi_definition_dir),
        "core_output_sha256": output_hashes,
    }
    components["aggregate_sha256"] = _sha256(_json_bytes(components))
    return components


def proposal_is_stale(
    proposal: ProposalRecord,
    *,
    semantic_yaml_path: Path = DBT_SEMANTIC_YAML,
    manifest_path: Path = DBT_SEMANTIC_MANIFEST,
    powerbi_definition_dir: Path = PBI_DEFINITION_DIR,
    output_dir: Path = OUTPUT_DIR,
) -> bool:
    current = capture_source_snapshot(
        semantic_yaml_path=semantic_yaml_path,
        manifest_path=manifest_path,
        powerbi_definition_dir=powerbi_definition_dir,
        output_dir=output_dir,
    )
    return current["aggregate_sha256"] != proposal.source_snapshot_hash


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def semantic_ir_to_dict(metric: SemanticMetricIR) -> dict[str, Any]:
    return _json_value(metric)


def _proposal_diagnostic(
    code: str,
    message: str,
    *,
    severity: str = "ERROR",
    target: str | None = None,
) -> ProposalDiagnostic:
    return ProposalDiagnostic(code, message, severity, target)


def _proposal_assumptions(request: MetricChangeRequest) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*request.assumptions, *SAFETY_ASSUMPTIONS)))


def _from_ir_diagnostic(item: Diagnostic) -> ProposalDiagnostic:
    return ProposalDiagnostic(
        item.code,
        item.message,
        item.severity.value,
        item.target.value if item.target else None,
    )


def _filter_input(value: FilterInput) -> FilterPredicate:
    if value.operator is not RequestFilterOperator.EQ:
        raise ProposalInputError("Only EQ filter predicates are supported.")
    return FilterPredicate(value.field, FilterOperator.EQ, value.value)


def _allowed_filter_fields(manifest: Mapping[str, Any], source_model: str | None) -> set[str]:
    allowed: set[str] = set()
    model = next(
        (
            item
            for item in manifest.get("semantic_models", []) or []
            if isinstance(item, Mapping) and item.get("name") == source_model
        ),
        None,
    )
    if not isinstance(model, Mapping):
        return allowed
    for collection in ("entities", "dimensions"):
        for item in model.get(collection, []) or []:
            if not isinstance(item, Mapping):
                continue
            for key in ("name", "expr"):
                value = item.get(key)
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                    allowed.add(value)
    for measure in model.get("measures", []) or []:
        expression = measure.get("expr") if isinstance(measure, Mapping) else None
        if isinstance(expression, str):
            allowed.update(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression))
    return allowed - {"AND", "OR", "TRUE", "FALSE"}


def _validate_filter_field(predicate: FilterPredicate, allowed: set[str]) -> None:
    if predicate.field not in allowed:
        raise ProposalManualReview(
            f"Filter field {predicate.field!r} is not declared in the resolved semantic-model context.",
            diagnostic_code="FILTER_FIELD_UNRESOLVED",
        )


def _same_source_context(first: SemanticMetricIR, second: SemanticMetricIR) -> bool:
    return (
        first.source_semantic_model,
        first.source_entity,
        first.source_logical_table,
        first.source_physical_table,
    ) == (
        second.source_semantic_model,
        second.source_entity,
        second.source_logical_table,
        second.source_physical_table,
    )


def _ensure_exclusion_no_op(metric: SemanticMetricIR, request: MetricChangeRequest) -> ProposalDiagnostic:
    operation = request.operation
    expected_values = {"DNS", "DNF", "DSQ"}
    if (
        metric.canonical_name != "valid_sbr_finishers"
        or operation.field != "finish_status"
        or {value.upper() for value in operation.values} != expected_values
    ):
        raise ProposalManualReview(
            "This exclusion guarantee is not registered for deterministic proof.",
            diagnostic_code="EXCLUSION_GUARANTEE_UNSUPPORTED",
        )
    expected_filter = FilterPredicate("is_valid_sbr_finisher", FilterOperator.EQ, True)
    if expected_filter not in metric.filters:
        raise ProposalManualReview(
            "valid_sbr_finishers no longer uses the expected canonical validity flag.",
            diagnostic_code="NO_OP_EVIDENCE_MISSING",
        )
    sql = UPSTREAM_FINISHER_SOURCE.read_text(encoding="utf-8")
    proof = re.search(
        r"when\s+finish_status\s*=\s*'FIN'[\s\S]{0,350}?end\s+as\s+bit\)\s+as\s+is_valid_sbr_finisher",
        sql,
        flags=re.IGNORECASE,
    )
    if proof is None:
        raise ProposalManualReview(
            "The upstream FIN-only derivation could not be proven from the canonical model.",
            diagnostic_code="NO_OP_EVIDENCE_MISSING",
        )
    return _proposal_diagnostic(
        "REQUEST_ALREADY_SATISFIED",
        "valid_sbr_finishers counts only is_valid_sbr_finisher rows; the canonical upstream rule sets that flag only when finish_status = 'FIN', so DNS, DNF, and DSQ are already excluded.",
        severity="INFO",
    )


def _apply_update(
    current: SemanticMetricIR,
    index: Mapping[str, SemanticMetricIR],
    request: MetricChangeRequest,
    manifest: Mapping[str, Any],
) -> tuple[SemanticMetricIR, tuple[ProposalDiagnostic, ...]]:
    operation = request.operation
    if operation.kind is OperationKind.SET_LABEL:
        if not current.power_bi.measure and any(
            item.numerator == current.canonical_name or item.denominator == current.canonical_name
            for item in index.values()
        ):
            raise ProposalManualReview(
                "Changing this private support label would alter dependent Power BI ratio references.",
                diagnostic_code="DEPENDENT_TARGET_UPDATE_UNSUPPORTED",
            )
        return replace(current, label=operation.value or ""), ()
    if operation.kind is OperationKind.SET_DESCRIPTION:
        return replace(current, description=operation.value or ""), ()
    if operation.kind is OperationKind.SET_FORMAT:
        power_bi = replace(current.power_bi, format_string=FORMAT_STRINGS[operation.value or ""])
        return replace(current, semantic_format=operation.value, power_bi=power_bi), ()
    if operation.kind is OperationKind.ENSURE_EXCLUDED_VALUES:
        return current, (_ensure_exclusion_no_op(current, request),)
    if operation.kind in {OperationKind.ADD_FILTER, OperationKind.REMOVE_FILTER, OperationKind.REPLACE_FILTER}:
        if current.pattern not in {MetricPattern.COUNT, MetricPattern.FILTERED_COUNT}:
            raise ProposalManualReview(
                "Filter operations require a supported COUNT or FILTERED_COUNT metric.",
                diagnostic_code="FILTER_OPERATION_PATTERN_UNSUPPORTED",
            )
        allowed = _allowed_filter_fields(manifest, current.source_semantic_model)
        filters = list(current.filters)
        if operation.kind is OperationKind.ADD_FILTER:
            predicate = _filter_input(operation.predicate)  # type: ignore[arg-type]
            _validate_filter_field(predicate, allowed)
            if predicate not in filters:
                filters.append(predicate)
        elif operation.kind is OperationKind.REMOVE_FILTER:
            predicate = _filter_input(operation.predicate)  # type: ignore[arg-type]
            _validate_filter_field(predicate, allowed)
            filters = [item for item in filters if item != predicate]
        else:
            old = _filter_input(operation.current)  # type: ignore[arg-type]
            new = _filter_input(operation.proposed)  # type: ignore[arg-type]
            _validate_filter_field(old, allowed)
            _validate_filter_field(new, allowed)
            if old not in filters:
                raise ProposalManualReview(
                    "The current predicate required by REPLACE_FILTER is absent.",
                    diagnostic_code="FILTER_PRECONDITION_MISMATCH",
                )
            filters = [new if item == old else item for item in filters]
        pattern = MetricPattern.FILTERED_COUNT if filters else MetricPattern.COUNT
        return replace(current, pattern=pattern, aggregation=Aggregation.COUNT, filters=tuple(filters)), ()
    if operation.kind in {OperationKind.SET_NUMERATOR, OperationKind.SET_DENOMINATOR}:
        if current.pattern is not MetricPattern.RATIO:
            raise ProposalManualReview(
                "Ratio reference operations require a supported RATIO metric.",
                diagnostic_code="RATIO_OPERATION_PATTERN_UNSUPPORTED",
            )
        reference = index.get(operation.value or "")
        if reference is None:
            raise ProposalInputError(f"Referenced canonical metric does not exist: {operation.value!r}")
        if classify_pattern(reference).support is not SupportClassification.SUPPORTED_PATTERN:
            raise ProposalManualReview(
                f"Referenced metric {reference.canonical_name!r} is not a supported pattern.",
                diagnostic_code="RATIO_REFERENCE_UNSUPPORTED",
            )
        proposed = replace(
            current,
            numerator=operation.value if operation.kind is OperationKind.SET_NUMERATOR else current.numerator,
            denominator=operation.value if operation.kind is OperationKind.SET_DENOMINATOR else current.denominator,
        )
        numerator = index.get(proposed.numerator or "")
        denominator = index.get(proposed.denominator or "")
        if numerator is None or denominator is None or not _same_source_context(numerator, denominator):
            raise ProposalManualReview(
                "Proposed ratio references do not share one supported source context.",
                diagnostic_code="RATIO_SOURCE_CONTEXT_MISMATCH",
            )
        if proposed.snowflake.logical_table != numerator.source_logical_table:
            raise ProposalManualReview(
                "Proposed ratio would require Snowflake relationship inference.",
                diagnostic_code="SNOWFLAKE_RELATIONSHIP_UNSUPPORTED",
            )
        return replace(
            proposed,
            source_semantic_model=numerator.source_semantic_model,
            source_entity=numerator.source_entity,
            source_logical_table=numerator.source_logical_table,
            source_physical_table=numerator.source_physical_table,
        ), ()
    raise ProposalManualReview(
        f"Operation {operation.kind.value} is representable but not supported for controlled local application.",
        diagnostic_code="OPERATION_NOT_IMPLEMENTED_M3",
    )


def _canonical_literal(predicate: FilterPredicate) -> str:
    if isinstance(predicate.value, bool):
        return "1" if predicate.value else "0"
    if isinstance(predicate.value, str):
        return "'" + predicate.value.replace("'", "''") + "'"
    return str(predicate.value)


def _canonical_expression(metric: SemanticMetricIR) -> tuple[str, str]:
    if metric.pattern is MetricPattern.COUNT:
        return "sum", "1"
    expression = " AND ".join(
        f"{predicate.field} = {_canonical_literal(predicate)}" for predicate in metric.filters
    )
    return "sum_boolean", expression


def _canonical_patch(
    current: SemanticMetricIR,
    proposed: SemanticMetricIR,
    request: MetricChangeRequest,
    raw_metric: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    name = current.canonical_name
    kind = request.operation.kind
    operations: list[dict[str, Any]] = []

    def add(selector: str, old: Any, new: Any) -> None:
        if old != new:
            operations.append({"operation": "replace", "selector": selector, "current": old, "proposed": new})

    if kind is OperationKind.SET_LABEL:
        add(f"metrics[{name}].label", current.label, proposed.label)
    elif kind is OperationKind.SET_DESCRIPTION:
        add(f"metrics[{name}].description", current.description, proposed.description)
    elif kind is OperationKind.SET_FORMAT:
        add(
            f"metrics[{name}].config.meta.semantic_contract.format",
            current.semantic_format,
            proposed.semantic_format,
        )
        add(
            f"metrics[{name}].config.meta.power_bi.format_string",
            current.power_bi.format_string,
            proposed.power_bi.format_string,
        )
    elif kind in {OperationKind.ADD_FILTER, OperationKind.REMOVE_FILTER, OperationKind.REPLACE_FILTER}:
        measure_name = metric_ref_name((raw_metric.get("type_params") or {}).get("measure"))
        current_agg, current_expr = _canonical_expression(current)
        proposed_agg, proposed_expr = _canonical_expression(proposed)
        selector = f"semantic_models[{current.source_semantic_model}].measures[{measure_name}]"
        add(selector + ".agg", current_agg, proposed_agg)
        add(selector + ".expr", current_expr, proposed_expr)
    elif kind is OperationKind.SET_NUMERATOR:
        add(f"metrics[{name}].type_params.numerator", current.numerator, proposed.numerator)
    elif kind is OperationKind.SET_DENOMINATOR:
        add(f"metrics[{name}].type_params.denominator", current.denominator, proposed.denominator)
    return tuple(operations)


def _target_support(
    canonical: TargetSupport,
    dax_support: SupportClassification,
    snowflake_support: SupportClassification,
) -> dict[str, str]:
    return {
        TargetName.CANONICAL_DBT.value: canonical.value,
        TargetName.POWER_BI.value: dax_support.value,
        TargetName.SNOWFLAKE.value: snowflake_support.value,
    }


def _manual_record(
    request: MetricChangeRequest,
    snapshot: Mapping[str, Any],
    diagnostic: ProposalDiagnostic,
    *,
    resolution: Mapping[str, Any],
    canonical_metric: str | None,
    current_ir: SemanticMetricIR | None = None,
    current_index: Mapping[str, SemanticMetricIR] | None = None,
) -> ProposalRecord:
    current_dax = current_snowflake = None
    diagnostics = [diagnostic]
    if current_ir is not None and current_index is not None:
        dax_result = generate_dax_definition(current_ir, current_index)
        snowflake_result = generate_snowflake_definition(current_ir, current_index)
        current_dax = dax_result.definition
        current_snowflake = snowflake_result.definition
        diagnostics.extend(_from_ir_diagnostic(item) for item in current_ir.diagnostics)
    support = {target.value: TargetSupport.MANUAL_REVIEW_REQUIRED.value for target in request.affected_targets}
    return ProposalRecord(
        schema_version=PROPOSAL_SCHEMA_VERSION,
        change_id=request.change_id,
        created_at=request.created_at,
        original_request=request.to_dict(),
        intent=request.intent.value,
        mode=request.mode.value,
        operation=request.operation.to_dict(),
        resolution=resolution,
        canonical_metric=canonical_metric,
        canonical_file=CANONICAL_FILE,
        source_snapshot=snapshot,
        source_snapshot_hash=snapshot["aggregate_sha256"],
        current_ir=None if current_ir is None else semantic_ir_to_dict(current_ir),
        proposed_ir=None,
        canonical_patch=(),
        canonical_diff="",
        current_dax=current_dax,
        proposed_dax=None,
        dax_diff="",
        current_snowflake=current_snowflake,
        proposed_snowflake=None,
        snowflake_diff="",
        target_support=support,
        cross_target_valid=False,
        assumptions=_proposal_assumptions(request),
        diagnostics=tuple(diagnostics),
        risk_level=RiskLevel.HIGH,
        required_validation=REQUIRED_VALIDATION,
        status=ProposalStatus.MANUAL_REVIEW_REQUIRED,
        approval_state=ApprovalState.NOT_REQUESTED,
        validation_state=ValidationState.NOT_RUN,
        local_application_state=LocalApplicationState.NOT_REQUESTED,
        deployment_state=DeploymentState.NOT_REQUESTED,
    )


def propose_change(
    request: MetricChangeRequest,
    *,
    semantic_yaml_path: Path = DBT_SEMANTIC_YAML,
    manifest_path: Path = DBT_SEMANTIC_MANIFEST,
    powerbi_definition_dir: Path = PBI_DEFINITION_DIR,
    output_dir: Path = OUTPUT_DIR,
) -> ProposalRecord:
    request = MetricChangeRequest.from_dict(request.to_dict())
    snapshot = capture_source_snapshot(
        semantic_yaml_path=semantic_yaml_path,
        manifest_path=manifest_path,
        powerbi_definition_dir=powerbi_definition_dir,
        output_dir=output_dir,
    )
    canonical_yaml = load_yaml(semantic_yaml_path) or {}
    manifest = load_json(manifest_path)

    if request.intent is ChangeIntent.CREATE_METRIC:
        if request.canonical_metric_name != request.operation.value:
            raise ProposalInputError("CREATE_METRIC proposed_name must match canonical_metric_name.")
        try:
            resolve_metric(canonical_yaml, request.canonical_metric_name)
        except MetricNotFoundError:
            pass
        except MetricAmbiguousError as exc:
            raise ProposalInputError(str(exc)) from exc
        else:
            raise ProposalInputError(f"Metric already exists: {request.canonical_metric_name!r}")
        definition = request.operation.definition
        if definition is None:
            return _manual_record(
                request,
                snapshot,
                _proposal_diagnostic(
                    "CREATE_METRIC_TYPED_DEFINITION_REQUIRED",
                    "Legacy CREATE_METRIC requests remain readable but require a strict typed definition for application.",
                ),
                resolution={"requested": request.canonical_metric_name, "resolved_from": "UNRESOLVED_CREATE", "candidates": []},
                canonical_metric=request.canonical_metric_name,
            )
        if definition.public and set(request.affected_targets) != {
            TargetName.CANONICAL_DBT,
            TargetName.POWER_BI,
            TargetName.SNOWFLAKE,
        }:
            raise ProposalInputError(
                "Public metric creation must select canonical dbt, Power BI, and Snowflake targets."
            )
        patch = (
            {
                "operation": "insert_typed_metric",
                "metric_name": request.canonical_metric_name,
                "definition": definition.to_dict(),
            },
        )
        try:
            candidate_bytes = render_candidate(semantic_yaml_path.read_bytes(), patch)
            candidate_yaml = yaml.safe_load(candidate_bytes) or {}
        except (CanonicalPatchError, OSError, yaml.YAMLError) as exc:
            return _manual_record(
                request,
                snapshot,
                _proposal_diagnostic("CREATE_METRIC_INSERTION_REJECTED", str(exc)),
                resolution={"requested": request.canonical_metric_name, "resolved_from": "TYPED_CREATE", "candidates": []},
                canonical_metric=request.canonical_metric_name,
            )
        proposed_index = build_canonical_metric_ir_index(
            candidate_yaml,
            canonical_source=CANONICAL_SOURCE,
            trace_id=request.change_id,
        )
        proposed = proposed_index.get(request.canonical_metric_name)
        if proposed is None:
            raise ProposalInputError("Typed metric insertion did not produce the requested canonical metric.")
        if proposed.source_semantic_model != definition.semantic_model:
            return _manual_record(
                request,
                snapshot,
                _proposal_diagnostic(
                    "CREATE_METRIC_SOURCE_MODEL_MISMATCH",
                    "Typed metric references do not resolve to the selected existing semantic model.",
                ),
                resolution={"requested": request.canonical_metric_name, "resolved_from": "TYPED_CREATE", "candidates": []},
                canonical_metric=request.canonical_metric_name,
            )
        classification = classify_pattern(proposed)
        if classification.support is not SupportClassification.SUPPORTED_PATTERN:
            diagnostic = classification.diagnostics[0] if classification.diagnostics else None
            return _manual_record(
                request,
                snapshot,
                _proposal_diagnostic(
                    diagnostic.code if diagnostic else "PROPOSED_PATTERN_UNSUPPORTED",
                    diagnostic.message if diagnostic else "Typed metric creation requires manual review.",
                ),
                resolution={"requested": request.canonical_metric_name, "resolved_from": "TYPED_CREATE", "candidates": []},
                canonical_metric=request.canonical_metric_name,
            )
        dax_result = generate_dax_definition(proposed, proposed_index)
        snowflake_result = generate_snowflake_definition(proposed, proposed_index)
        cross_target = validate_cross_target(proposed, dax_result, snowflake_result)
        if not cross_target.valid or dax_result.definition is None or snowflake_result.definition is None:
            diagnostic = cross_target.diagnostics[0] if cross_target.diagnostics else None
            return _manual_record(
                request,
                snapshot,
                _proposal_diagnostic(
                    diagnostic.code if diagnostic else "CREATE_METRIC_CROSS_TARGET_INVALID",
                    diagnostic.message if diagnostic else "Typed metric creation is not valid across both targets.",
                ),
                resolution={"requested": request.canonical_metric_name, "resolved_from": "TYPED_CREATE", "candidates": []},
                canonical_metric=request.canonical_metric_name,
            )
        target_support_all = _target_support(
            TargetSupport.SUPPORTED_PATTERN,
            dax_result.support,
            snowflake_result.support,
        )
        target_support = {
            target.value: target_support_all[target.value] for target in request.affected_targets
        }
        return ProposalRecord(
            schema_version=PROPOSAL_SCHEMA_VERSION,
            change_id=request.change_id,
            created_at=request.created_at,
            original_request=request.to_dict(),
            intent=request.intent.value,
            mode=request.mode.value,
            operation=request.operation.to_dict(),
            resolution={
                "requested": request.canonical_metric_name,
                "canonical_metric": request.canonical_metric_name,
                "resolved_from": "TYPED_CREATE",
                "candidates": [],
            },
            canonical_metric=request.canonical_metric_name,
            canonical_file=CANONICAL_FILE,
            source_snapshot=snapshot,
            source_snapshot_hash=snapshot["aggregate_sha256"],
            current_ir=None,
            proposed_ir=semantic_ir_to_dict(proposed),
            canonical_patch=patch,
            canonical_diff=canonical_pseudo_diff(patch),
            current_dax=None,
            proposed_dax=dax_result.definition,
            dax_diff=text_diff(
                None,
                dax_result.definition,
                current_name="current/powerbi.dax",
                proposed_name="proposed/powerbi.dax",
            ),
            current_snowflake=None,
            proposed_snowflake=snowflake_result.definition,
            snowflake_diff=definition_diff(
                None,
                snowflake_result.definition,
                current_name="current/snowflake-metric.json",
                proposed_name="proposed/snowflake-metric.json",
            ),
            target_support=target_support,
            cross_target_valid=True,
            assumptions=_proposal_assumptions(request),
            diagnostics=(),
            risk_level=RiskLevel.MEDIUM,
            required_validation=REQUIRED_VALIDATION,
            status=ProposalStatus.PROPOSED,
            approval_state=ApprovalState.PENDING,
            validation_state=ValidationState.NOT_RUN,
            local_application_state=LocalApplicationState.NOT_REQUESTED,
            deployment_state=DeploymentState.NOT_REQUESTED,
            canonical_application_available=True,
        )

    try:
        resolved = resolve_metric(canonical_yaml, request.canonical_metric_name)
    except MetricNotFoundError as exc:
        raise ProposalInputError(str(exc)) from exc
    except MetricAmbiguousError as exc:
        return _manual_record(
            request,
            snapshot,
            _proposal_diagnostic("TARGET_MAPPING_AMBIGUOUS", str(exc)),
            resolution={
                "requested": request.canonical_metric_name,
                "resolved_from": "AMBIGUOUS_TARGET_ALIAS",
                "candidates": list(exc.candidates),
            },
            canonical_metric=None,
        )

    canonical_name = resolved.metric["name"]
    resolution = {
        "requested": request.canonical_metric_name,
        "canonical_metric": canonical_name,
        "resolved_from": resolved.resolved_from,
        "candidates": [],
    }
    index = build_metric_ir_index(
        manifest,
        canonical_yaml,
        canonical_source=CANONICAL_SOURCE,
        trace_id=request.change_id,
    )
    current = index.get(canonical_name)
    if current is None:
        raise ProposalInputError(f"Resolved metric is absent from the compiled manifest: {canonical_name!r}")
    if current.public and set(request.affected_targets) != {
        TargetName.CANONICAL_DBT,
        TargetName.POWER_BI,
        TargetName.SNOWFLAKE,
    }:
        raise ProposalInputError("Public metric proposals must select canonical dbt, Power BI, and Snowflake targets.")

    if request.intent in {ChangeIntent.RENAME_METRIC, ChangeIntent.DEPRECATE_METRIC}:
        if request.intent is ChangeIntent.RENAME_METRIC:
            try:
                resolve_metric(canonical_yaml, request.operation.value or "")
            except MetricNotFoundError:
                pass
            else:
                raise ProposalInputError(f"Proposed metric name already exists: {request.operation.value!r}")
        code = f"{request.intent.value}_NOT_IMPLEMENTED_M3"
        return _manual_record(
            request,
            snapshot,
            _proposal_diagnostic(
                code,
                f"{request.intent.value} is representable but is not supported for dependency-safe local application.",
            ),
            resolution=resolution,
            canonical_metric=canonical_name,
            current_ir=current,
            current_index=index,
        )

    classification = classify_pattern(current)
    if classification.support is not SupportClassification.SUPPORTED_PATTERN:
        diagnostics = classification.diagnostics
        message = diagnostics[0].message if diagnostics else "Current canonical pattern requires manual review."
        code = diagnostics[0].code if diagnostics else "CURRENT_PATTERN_UNSUPPORTED"
        return _manual_record(
            request,
            snapshot,
            _proposal_diagnostic(code, message),
            resolution=resolution,
            canonical_metric=canonical_name,
            current_ir=current,
            current_index=index,
        )

    try:
        proposed, operation_diagnostics = _apply_update(current, index, request, manifest)
    except ProposalManualReview as exc:
        return _manual_record(
            request,
            snapshot,
            _proposal_diagnostic(exc.diagnostic_code, str(exc)),
            resolution=resolution,
            canonical_metric=canonical_name,
            current_ir=current,
            current_index=index,
        )

    proposed_index = dict(index)
    proposed_index[canonical_name] = proposed
    proposed_classification = classify_pattern(proposed)
    if proposed_classification.support is not SupportClassification.SUPPORTED_PATTERN:
        diagnostic = proposed_classification.diagnostics[0] if proposed_classification.diagnostics else None
        return _manual_record(
            request,
            snapshot,
            _proposal_diagnostic(
                diagnostic.code if diagnostic else "PROPOSED_PATTERN_UNSUPPORTED",
                diagnostic.message if diagnostic else "Proposed canonical pattern requires manual review.",
            ),
            resolution=resolution,
            canonical_metric=canonical_name,
            current_ir=current,
            current_index=index,
        )

    current_dax_result = generate_dax_definition(current, index)
    current_snowflake_result = generate_snowflake_definition(current, index)
    proposed_dax_result = generate_dax_definition(proposed, proposed_index)
    proposed_snowflake_result = generate_snowflake_definition(proposed, proposed_index)
    cross_target = validate_cross_target(proposed, proposed_dax_result, proposed_snowflake_result)
    if not cross_target.valid:
        diagnostic = cross_target.diagnostics[0]
        return _manual_record(
            request,
            snapshot,
            _proposal_diagnostic(diagnostic.code, diagnostic.message),
            resolution=resolution,
            canonical_metric=canonical_name,
            current_ir=current,
            current_index=index,
        )

    patch = _canonical_patch(current, proposed, request, resolved.metric)
    canonical_diff = canonical_pseudo_diff(patch)
    current_dax = current_dax_result.definition
    proposed_dax = proposed_dax_result.definition
    current_snowflake = current_snowflake_result.definition
    proposed_snowflake = proposed_snowflake_result.definition
    dax_diff = text_diff(current_dax, proposed_dax, current_name="current/powerbi.dax", proposed_name="proposed/powerbi.dax")
    snowflake_diff = definition_diff(
        current_snowflake,
        proposed_snowflake,
        current_name="current/snowflake-metric.json",
        proposed_name="proposed/snowflake-metric.json",
    )
    no_op = current == proposed
    status = ProposalStatus.NO_OP if no_op else ProposalStatus.PROPOSED
    metadata_operation = request.operation.kind in {
        OperationKind.SET_LABEL,
        OperationKind.SET_DESCRIPTION,
        OperationKind.SET_FORMAT,
    }
    risk = RiskLevel.LOW if no_op or metadata_operation else RiskLevel.MEDIUM
    canonical_support = TargetSupport.METADATA_ONLY if metadata_operation else TargetSupport.SUPPORTED_PATTERN
    diagnostics = list(operation_diagnostics)
    if no_op and not diagnostics:
        diagnostics.append(
            _proposal_diagnostic(
                "REQUEST_ALREADY_SATISFIED",
                "The requested structured operation does not change normalized canonical semantics.",
                severity="INFO",
            )
        )
    return ProposalRecord(
        schema_version=PROPOSAL_SCHEMA_VERSION,
        change_id=request.change_id,
        created_at=request.created_at,
        original_request=request.to_dict(),
        intent=request.intent.value,
        mode=request.mode.value,
        operation=request.operation.to_dict(),
        resolution=resolution,
        canonical_metric=canonical_name,
        canonical_file=CANONICAL_FILE,
        source_snapshot=snapshot,
        source_snapshot_hash=snapshot["aggregate_sha256"],
        current_ir=semantic_ir_to_dict(current),
        proposed_ir=semantic_ir_to_dict(proposed),
        canonical_patch=patch,
        canonical_diff=canonical_diff,
        current_dax=current_dax,
        proposed_dax=proposed_dax,
        dax_diff=dax_diff,
        current_snowflake=current_snowflake,
        proposed_snowflake=proposed_snowflake,
        snowflake_diff=snowflake_diff,
        target_support=(
            {target.value: TargetSupport.METADATA_ONLY.value for target in TargetName}
            if metadata_operation
            else _target_support(
                canonical_support,
                proposed_dax_result.support,
                proposed_snowflake_result.support,
            )
        ),
        cross_target_valid=True,
        assumptions=_proposal_assumptions(request),
        diagnostics=tuple(diagnostics),
        risk_level=risk,
        required_validation=REQUIRED_VALIDATION,
        status=status,
        approval_state=(ApprovalState.NOT_REQUESTED if no_op else ApprovalState.PENDING),
        validation_state=ValidationState.NOT_RUN,
        local_application_state=LocalApplicationState.NOT_REQUESTED,
        deployment_state=DeploymentState.NOT_REQUESTED,
        canonical_application_available=not no_op,
    )
