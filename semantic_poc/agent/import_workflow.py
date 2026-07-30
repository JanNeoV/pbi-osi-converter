"""Power BI Brownfield import orchestration.

Discovery is source-read-only.  Proposal generation consumes an immutable
ImportRun and uses the established canonical IR compilers for final target
definitions; it never applies canonical or target changes.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from semantic_poc.src.models import (
    DBT_SEMANTIC_MANIFEST,
    DBT_SEMANTIC_YAML,
    OUTPUT_DIR,
    PBI_DEFINITION_DIR,
    PROJECT_ROOT,
    load_json,
    load_yaml,
)
from semantic_poc.src.semantic_ir import (
    PowerBIMapping,
    SnowflakeMapping,
    SupportClassification,
    build_metric_ir_index,
    generate_dax_definition,
    generate_snowflake_definition,
    validate_cross_target,
)

from .diff_models import text_diff
from .import_models import ImportProposalBatch, ImportRun
from .powerbi_import import (
    EXTRACTION_VERSION,
    ImportMetricCandidate,
    ImportSupportClassification,
    PowerBIRegenerationComparison,
    PowerBIModelInventory,
    analyze_import_relationships,
    build_import_metric_candidates,
    build_object_support_records,
    extract_powerbi_inventory,
    inventory_json_bytes,
    load_import_mapping_file,
    render_inventory_markdown,
    resolve_import_mappings,
    resolve_powerbi_model_dir,
)
from .proposal_engine import REQUIRED_VALIDATION, capture_source_snapshot, propose_change, semantic_ir_to_dict
from .proposal_models import (
    PROPOSAL_SCHEMA_VERSION,
    LocalApplicationState,
    ProposalDiagnostic,
    ProposalRecord,
    ProposalSource,
    ProposalStatus,
    RiskLevel,
)
from .schemas import (
    ApprovalState,
    ChangeIntent,
    DeploymentState,
    FilterInput,
    MetricChangeRequest,
    MetricOperation,
    OperationKind,
    RequestFilterOperator,
    TargetName,
    TargetSupport,
    ValidationState,
)


SUPPORTED_IMPORT_CLASSIFICATIONS = frozenset(
    {
        ImportSupportClassification.SUPPORTED_EXACT,
        ImportSupportClassification.SUPPORTED_WITH_MAPPING,
        ImportSupportClassification.SUPPORTED_WITH_ASSUMPTIONS,
    }
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _contained_optional_mapping(path: str | Path | None, root: Path) -> Mapping[str, Any] | None:
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)) or not resolved.is_file():
        raise ValueError("Power BI import mapping file must be a repository-contained regular file.")
    return load_import_mapping_file(resolved)


def _classification_counts(candidates: Sequence[ImportMetricCandidate | Mapping[str, Any]]) -> dict[str, int]:
    values = []
    for item in candidates:
        value = item.classification.value if isinstance(item, ImportMetricCandidate) else str(item["classification"])
        values.append(value)
    return dict(sorted(Counter(values).items()))


def render_import_report(
    inventory: PowerBIModelInventory,
    candidates: Sequence[ImportMetricCandidate],
    relationship_findings: Sequence[Mapping[str, Any] | Any],
) -> str:
    counts = _classification_counts(candidates)
    supported = sum(counts.get(item.value, 0) for item in SUPPORTED_IMPORT_CLASSIFICATIONS)
    manual = counts.get(ImportSupportClassification.MANUAL_REVIEW_REQUIRED.value, 0)
    unsupported = counts.get(ImportSupportClassification.UNSUPPORTED.value, 0)
    findings = [item.to_dict() if hasattr(item, "to_dict") else dict(item) for item in relationship_findings]
    object_counts = dict(
        sorted(
            Counter(
                item.classification.value for item in inventory.object_support_records
            ).items()
        )
    )
    lines = [
        "# Power BI Brownfield Import Report",
        "",
        f"- Model: `{inventory.model.name}`",
        f"- Source snapshot SHA-256: `{inventory.model.source_tree_hash}`",
        f"- Inventory semantic SHA-256: `{inventory.semantic_hash}`",
        f"- Tables / columns / measures: {len(inventory.tables)} / {len(inventory.columns)} / {len(inventory.measures)}",
        f"- Relationships / partitions: {len(inventory.relationships)} / {len(inventory.partitions)}",
        f"- Dependency nodes / edges: {len(inventory.measures)} / {len(inventory.dependency_edges)}",
        f"- Supported candidates: {supported}",
        f"- Manual review / unsupported: {manual} / {unsupported}",
        "",
        "## Measure classifications",
        "",
    ]
    for name, count in counts.items():
        lines.append(f"- `{name}`: {count}")
    lines.extend(["", "## Object classifications", ""])
    for name, count in object_counts.items():
        lines.append(f"- `{name}`: {count}")
    lines.extend(["", "## Relationship findings", ""])
    for finding in findings:
        qualifier = "informational" if finding.get("informational") else "governed"
        lines.append(f"- `{finding.get('code')}` ({qualifier}): {finding.get('message')}")
    lines.extend(
        [
            "",
            "Import is read-only. No canonical definition, Power BI source file, relationship, or deployment was changed.",
            "",
        ]
    )
    return "\n".join(lines)


def create_import_run(
    model_dir: str | Path,
    *,
    store: Any,
    mapping_file: str | Path | None = None,
    repository_root: Path = PROJECT_ROOT,
    canonical_yaml_path: Path = DBT_SEMANTIC_YAML,
    now: datetime | None = None,
    entropy: str | None = None,
) -> ImportRun:
    root = repository_root.resolve(strict=True)
    definition = resolve_powerbi_model_dir(model_dir, root)
    before_hash = _source_tree_hash(definition)
    inventory = extract_powerbi_inventory(definition, root)
    explicit_mapping = _contained_optional_mapping(mapping_file, root)
    canonical = load_yaml(canonical_yaml_path) or {}
    decisions = resolve_import_mappings(inventory, canonical, explicit_mapping)
    candidates = build_import_metric_candidates(inventory, canonical, explicit_mapping=explicit_mapping)
    relationships = analyze_import_relationships(inventory, canonical)
    inventory = replace(
        inventory,
        object_support_records=build_object_support_records(
            inventory, candidates, relationships
        ),
    )
    after_hash = _source_tree_hash(definition)
    if before_hash != after_hash or inventory.model.source_tree_hash != before_hash:
        raise RuntimeError("Power BI source changed during read-only import.")
    run = ImportRun.create(
        extraction_version=EXTRACTION_VERSION,
        source_model_id=inventory.model.object_id,
        source_model_path=inventory.model.definition_path,
        source_snapshot_hash=before_hash,
        inventory=inventory.to_dict(),
        mapping_decisions=[item.to_dict() for item in decisions],
        relationship_findings=[item.to_dict() for item in relationships],
        classifications=[item.to_dict() for item in candidates],
        now=now,
        entropy=entropy,
    )
    artifacts = {
        "inventory.json": inventory_json_bytes(inventory),
        "inventory.md": render_inventory_markdown(inventory),
        "report.md": render_import_report(inventory, candidates, relationships),
    }
    store.save_run(run, artifacts=artifacts)
    if _source_tree_hash(definition) != before_hash:
        raise RuntimeError("Power BI source changed while publishing import artifacts.")
    return run


def _source_tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for entry in path.rglob("*"):
        if entry.is_symlink():
            raise ValueError(f"Power BI source tree must not contain symbolic links: {entry}")
    for item in sorted((entry for entry in path.rglob("*") if entry.is_file()), key=lambda entry: entry.as_posix()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        payload = item.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def verify_import_source(run: ImportRun, repository_root: Path = PROJECT_ROOT) -> bool:
    try:
        root = repository_root.resolve(strict=True)
        configured_path = root / run.source_model_path
        path = configured_path.resolve(strict=True)
    except OSError:
        return False
    if not path.is_relative_to(root) or not path.is_dir() or configured_path.is_symlink():
        return False
    if any(item.is_symlink() for item in path.rglob("*")):
        return False
    return _source_tree_hash(path) == run.source_snapshot_hash


def _change_id(run: ImportRun, source_object_id: str) -> str:
    compact = run.created_at.replace("-", "").replace(":", "")
    entropy = hashlib.sha256(f"{run.import_id}\0{source_object_id}".encode("utf-8")).hexdigest()[:8]
    return f"chg_{compact}_{entropy}"


def _blocked_change_id(
    run: ImportRun,
    source_object_id: str,
    finding_id: str,
) -> str:
    compact = run.created_at.replace("-", "").replace(":", "")
    entropy = hashlib.sha256(
        f"{run.import_id}\0{source_object_id}\0{finding_id}".encode("utf-8")
    ).hexdigest()[:8]
    return f"chg_{compact}_{entropy}"


def _import_blocker_record(
    run: ImportRun,
    candidate: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    finding: Any,
    relationship_findings: Sequence[Mapping[str, Any]],
    ir_index: Mapping[str, Any],
) -> ProposalRecord:
    source_object_id = str(candidate["source_object_id"])
    canonical_metric_value = (candidate.get("mapping") or {}).get("canonical_metric")
    canonical_metric = (
        str(canonical_metric_value) if canonical_metric_value in ir_index else None
    )
    diagnostic_values = [
        ProposalDiagnostic(
            str(item.get("code", "POWERBI_IMPORT_DIAGNOSTIC")),
            str(item.get("message", "Power BI import diagnostic.")),
            str(item.get("severity", "ERROR")),
        )
        for item in candidate.get("diagnostics", ())
        if isinstance(item, Mapping)
    ]
    diagnostic_codes = {item.code for item in diagnostic_values}
    related_relationships: list[Mapping[str, Any]] = []
    if "DAX_INACTIVE_RELATIONSHIP_DEPENDENCY" in diagnostic_codes:
        related_relationships = [
            item
            for item in relationship_findings
            if item.get("code") == "PBI_INACTIVE_RELATIONSHIP_DEPENDENCY"
        ]
        for item in related_relationships:
            diagnostic_values.append(
                ProposalDiagnostic(
                    str(item["code"]),
                    str(item.get("message") or "Inactive relationship requires review."),
                    "ERROR",
                    "POWER_BI",
                )
            )
    mapping_reason = sorted(
        {
            str(item.get("code"))
            for item in candidate.get("diagnostics", ())
            if isinstance(item, Mapping)
            and str(item.get("code", "")).startswith("IMPORT_MAPPING")
        }
    )
    lineage = (
        {
            "canonical_lineage_status": "EXACT",
            "canonical_metric": canonical_metric,
            "canonical_source_file": "models/semantic/triathlon_semantic.yml",
            "mapping_reason_codes": mapping_reason,
        }
        if canonical_metric is not None
        else {
            "canonical_lineage_status": "UNRESOLVED_MANUAL_REVIEW_REQUIRED",
            "canonical_metric": None,
            "canonical_source_file": "models/semantic/triathlon_semantic.yml",
            "mapping_reason_codes": mapping_reason or ["IMPORT_MAPPING_MISSING"],
        }
    )
    finding_id = str(finding.finding_id)
    change_id = _blocked_change_id(run, source_object_id, finding_id)
    original_request = {
        "schema_version": 1,
        "change_id": change_id,
        "created_at": run.created_at,
        "requested_action": "CANONICALIZE_METRIC",
        "import_run_id": run.import_id,
        "source_model_path": run.source_model_path,
        "source_model_id": run.source_model_id,
        "source_object_path": str(candidate["source_object_path"]),
        "source_object_id": source_object_id,
    }
    return ProposalRecord(
        schema_version=PROPOSAL_SCHEMA_VERSION,
        change_id=change_id,
        created_at=run.created_at,
        original_request=original_request,
        intent="RECONCILE_TARGET_DRIFT",
        mode="PROPOSE",
        operation={"kind": "IMPORT_BLOCKED_UNSUPPORTED"},
        resolution={
            "preview_input_kind": "POWERBI_IMPORT_BLOCKER",
            "finding_id": finding_id,
            "finding_rule_id": str(finding.rule_id),
            "finding_category": finding.category.value,
            "source_location": candidate.get("source_location"),
            "source_measure": candidate.get("source_measure"),
            "source_table": candidate.get("source_table"),
            "import_classification": candidate.get("classification"),
            "import_candidate_semantic_hash": candidate.get("semantic_hash"),
            "unsupported_constructs": sorted(
                str(item) for item in candidate.get("unsupported_constructs", ())
            ),
            "relationship_findings": [
                dict(item)
                for item in sorted(
                    related_relationships,
                    key=lambda item: (
                        str(item.get("code")),
                        str(item.get("relationship_id") or ""),
                    ),
                )
            ],
            **lineage,
        },
        canonical_metric=canonical_metric,
        canonical_file="models/semantic/triathlon_semantic.yml",
        source_snapshot=snapshot,
        source_snapshot_hash=str(snapshot["aggregate_sha256"]),
        current_ir=(
            semantic_ir_to_dict(ir_index[canonical_metric])
            if canonical_metric is not None
            else None
        ),
        proposed_ir=None,
        canonical_patch=(),
        canonical_diff="",
        current_dax=None,
        proposed_dax=None,
        dax_diff="",
        current_snowflake=None,
        proposed_snowflake=None,
        snowflake_diff="",
        target_support={
            TargetName.CANONICAL_DBT.value: TargetSupport.MANUAL_REVIEW_REQUIRED.value,
            TargetName.POWER_BI.value: TargetSupport.MANUAL_REVIEW_REQUIRED.value,
            TargetName.SNOWFLAKE.value: TargetSupport.MANUAL_REVIEW_REQUIRED.value,
        },
        cross_target_valid=False,
        assumptions=tuple(str(item) for item in candidate.get("assumptions", ())),
        diagnostics=tuple(diagnostic_values),
        risk_level=RiskLevel.HIGH,
        required_validation=REQUIRED_VALIDATION,
        status=ProposalStatus.MANUAL_REVIEW_REQUIRED,
        approval_state=ApprovalState.NOT_REQUESTED,
        validation_state=ValidationState.NOT_RUN,
        local_application_state=LocalApplicationState.NOT_REQUESTED,
        deployment_state=DeploymentState.NOT_REQUESTED,
        canonical_application_available=False,
        proposal_source=ProposalSource.POWERBI_IMPORT,
        import_run_id=run.import_id,
        source_model_path=run.source_model_path,
        source_model_id=run.source_model_id,
        source_object_path=str(candidate["source_object_path"]),
        source_object_id=source_object_id,
        source_snapshot_sha256=run.source_snapshot_hash,
        authority_state="CANONICALIZATION_PROPOSED",
        extraction_version=run.extraction_version,
        semantic_content_sha256=None,
    )


def _metadata_differences(measure: Any, current: Any) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    expected = {
        "format_string": current.power_bi.format_string,
        "display_folder": current.power_bi.display_folder,
    }
    actual = {"format_string": measure.format_string, "display_folder": measure.display_folder}
    for field in ("format_string", "display_folder"):
        if expected[field] != actual[field]:
            differences.append({"field": field, "source": actual[field], "canonical_target": expected[field]})
    return differences


def _description_comparison(measure: Any, current: Any) -> dict[str, Any]:
    source = (measure.description or "").strip()
    canonical = (current.description or "").strip()
    if source == canonical:
        result = "MATCH"
    elif canonical and source.startswith(canonical):
        result = "TARGET_EXTENSION"
    else:
        result = "DIFFERENT"
    return {"result": result, "source": source, "canonical": canonical}


def _mapping_ir(current: Any, candidate: Mapping[str, Any], measure: Any) -> Any:
    mapping = candidate["mapping"]
    if candidate["classification"] == ImportSupportClassification.SUPPORTED_EXACT.value:
        return current
    return replace(
        current,
        power_bi=PowerBIMapping(
            table=measure.table,
            measure=measure.name,
            format_string=measure.format_string,
            display_folder=measure.display_folder,
        ),
        snowflake=SnowflakeMapping(
            logical_table=mapping.get("snowflake_logical_table") or current.source_logical_table,
            metric_name=mapping.get("snowflake_metric") or current.canonical_name,
            synonyms=current.snowflake.synonyms,
        ),
    )


def _proposal_record(
    run: ImportRun,
    candidate: Mapping[str, Any],
    inventory: PowerBIModelInventory,
    ir_index: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> ProposalRecord:
    canonical_metric = str(candidate["mapping"]["canonical_metric"])
    current = ir_index[canonical_metric]
    measure = next(item for item in inventory.measures if item.object_id == candidate["source_object_id"])
    proposed = _mapping_ir(current, candidate, measure)
    generated_dax = generate_dax_definition(proposed, {**ir_index, canonical_metric: proposed})
    generated_snowflake = generate_snowflake_definition(proposed, {**ir_index, canonical_metric: proposed})
    current_snowflake = generate_snowflake_definition(current, ir_index).definition
    cross_target = validate_cross_target(proposed, generated_dax, generated_snowflake)
    raw_comparison = candidate.get("comparison")
    if not isinstance(raw_comparison, Mapping):
        raise RuntimeError(
            f"Supported candidate has no persisted Power BI comparison: {measure.table}[{measure.name}]"
        )
    comparison = PowerBIRegenerationComparison.from_dict(raw_comparison)
    if candidate.get("regenerated_powerbi") != generated_dax.definition:
        raise RuntimeError(
            f"Supported candidate compiler output changed before proposal generation: {measure.table}[{measure.name}]"
        )
    classification = ImportSupportClassification(str(candidate["classification"]))
    exact = classification is ImportSupportClassification.SUPPORTED_EXACT
    status = ProposalStatus.NO_OP if exact else ProposalStatus.MANUAL_REVIEW_REQUIRED
    canonical_support = TargetSupport.SUPPORTED_PATTERN if exact else TargetSupport.MANUAL_REVIEW_REQUIRED
    current_mapping = {
        "power_bi": semantic_ir_to_dict(current)["power_bi"],
        "snowflake": semantic_ir_to_dict(current)["snowflake"],
    }
    proposed_mapping = {
        "power_bi": semantic_ir_to_dict(proposed)["power_bi"],
        "snowflake": semantic_ir_to_dict(proposed)["snowflake"],
    }
    canonical_diff = "" if current_mapping == proposed_mapping else text_diff(
        _stable_json(current_mapping),
        _stable_json(proposed_mapping),
        current_name=f"canonical/{canonical_metric}",
        proposed_name=f"powerbi-import/{canonical_metric}",
    )
    # Only configured canonical mappings can constitute target metadata drift.
    # Unconfigured target metadata belongs to the draft mapping decision and is
    # not counted as drift against a canonical target definition that does not
    # yet exist.
    metadata = _metadata_differences(measure, current) if exact else []
    diagnostics = [
        ProposalDiagnostic(
            str(item.get("code", "POWERBI_IMPORT_DIAGNOSTIC")),
            str(item.get("message", "Power BI import diagnostic.")),
            str(item.get("severity", "ERROR")),
        )
        for item in candidate.get("diagnostics", [])
    ]
    if not exact:
        diagnostics.append(
            ProposalDiagnostic(
                "IMPORT_STRUCTURAL_CANONICAL_DRAFT",
                "The candidate requires mapping-block insertion or a publication decision; M4 scalar apply is unavailable.",
            )
        )
    if not comparison.semantic_equivalent or generated_dax.support is not SupportClassification.SUPPORTED_PATTERN:
        raise RuntimeError(f"Supported candidate lost deterministic Power BI equivalence: {measure.table}[{measure.name}]")
    if generated_snowflake.support is not SupportClassification.SUPPORTED_PATTERN or not cross_target.valid:
        raise RuntimeError(f"Supported candidate lost deterministic Snowflake equivalence: {measure.table}[{measure.name}]")
    change_id = _change_id(run, measure.object_id)
    original_request = {
        "schema_version": 1,
        "change_id": change_id,
        "created_at": run.created_at,
        "requested_action": "CANONICALIZE_METRIC",
        "import_run_id": run.import_id,
        "source_model_path": run.source_model_path,
        "source_model_id": run.source_model_id,
        "source_object_path": str(candidate["source_object_path"]),
        "source_object_id": measure.object_id,
    }
    return ProposalRecord(
        schema_version=PROPOSAL_SCHEMA_VERSION,
        change_id=change_id,
        created_at=run.created_at,
        original_request=original_request,
        intent="RECONCILE_TARGET_DRIFT",
        mode="PROPOSE",
        operation={"kind": "IMPORT_CANONICALIZE"},
        resolution={
            "requested": f"{measure.table}[{measure.name}]",
            "source_location": candidate.get("source_location"),
            "canonical_metric": canonical_metric,
            "resolved_from": candidate["mapping"]["method"],
            "import_classification": classification.value,
            "import_candidate_semantic_hash": candidate.get("semantic_hash"),
            "recognized_pattern": candidate.get("recognized_pattern"),
            "required_mappings": candidate["mapping"].get("required_mappings", []),
            "metadata_differences": metadata,
            "description_comparison": _description_comparison(measure, current),
            "power_bi_comparison": comparison.to_dict(),
            "canonical_draft": candidate.get("canonical_draft"),
            "publication_decision": "UNCHANGED" if current.public else "UNDECIDED_PRIVATE_DEFAULT",
        },
        canonical_metric=canonical_metric,
        canonical_file="models/semantic/triathlon_semantic.yml",
        source_snapshot=snapshot,
        source_snapshot_hash=str(snapshot["aggregate_sha256"]),
        current_ir=semantic_ir_to_dict(current),
        proposed_ir=semantic_ir_to_dict(proposed),
        canonical_patch=(),
        canonical_diff=canonical_diff,
        current_dax=measure.expression,
        proposed_dax=generated_dax.definition,
        dax_diff="" if comparison.semantic_equivalent else text_diff(
            measure.expression, generated_dax.definition or "", current_name="powerbi/source", proposed_name="powerbi/regenerated"
        ),
        current_snowflake=current_snowflake,
        proposed_snowflake=generated_snowflake.definition,
        snowflake_diff=(
            ""
            if current_snowflake == generated_snowflake.definition
            else text_diff(
                _stable_json(current_snowflake),
                _stable_json(generated_snowflake.definition),
                current_name="snowflake/current",
                proposed_name="snowflake/proposed",
            )
        ),
        target_support={
            TargetName.CANONICAL_DBT.value: canonical_support.value,
            TargetName.POWER_BI.value: TargetSupport.SUPPORTED_PATTERN.value,
            TargetName.SNOWFLAKE.value: TargetSupport.SUPPORTED_PATTERN.value,
        },
        cross_target_valid=cross_target.valid,
        assumptions=tuple(str(item) for item in candidate.get("assumptions", [])),
        diagnostics=tuple(diagnostics),
        risk_level=RiskLevel.LOW if exact else RiskLevel.HIGH,
        required_validation=REQUIRED_VALIDATION,
        status=status,
        approval_state=ApprovalState.NOT_REQUESTED,
        validation_state=ValidationState.NOT_RUN,
        local_application_state=LocalApplicationState.NOT_REQUESTED,
        deployment_state=DeploymentState.NOT_REQUESTED,
        canonical_application_available=False,
        proposal_source=ProposalSource.POWERBI_IMPORT,
        import_run_id=run.import_id,
        source_model_path=run.source_model_path,
        source_model_id=run.source_model_id,
        source_object_path=str(candidate["source_object_path"]),
        source_object_id=measure.object_id,
        source_snapshot_sha256=run.source_snapshot_hash,
        authority_state="CANONICALIZATION_PROPOSED",
        extraction_version=run.extraction_version,
        semantic_content_sha256=None,
    )


def _candidate_scalar_operation(
    candidate: Mapping[str, Any],
    measure: Any,
    current: Any,
    classifications: Sequence[Mapping[str, Any]],
) -> MetricOperation | None:
    analysis = measure.analysis
    if not analysis.supported or analysis.ast is None:
        return None
    current_pattern = current.pattern.value
    if analysis.pattern in {"COUNT", "FILTERED_COUNT"} and current_pattern in {
        "COUNT",
        "FILTERED_COUNT",
    }:
        source_table = str(analysis.ast.get("table", ""))
        if not current.source_physical_table or source_table.casefold() != current.source_physical_table.casefold():
            return None
        current_filters = list(current.filters)
        source_filters = list(analysis.filters)
        if any(item.table.casefold() != source_table.casefold() for item in source_filters):
            return None
        if len({item.field for item in current_filters}) != len(current_filters):
            return None
        if len({item.column for item in source_filters}) != len(source_filters):
            return None
        current_by_field = {item.field: item for item in current_filters}
        source_by_field = {item.column: item for item in source_filters}
        removed = [current_by_field[name] for name in sorted(set(current_by_field) - set(source_by_field))]
        added = [source_by_field[name] for name in sorted(set(source_by_field) - set(current_by_field))]
        changed = [
            (current_by_field[name], source_by_field[name])
            for name in sorted(set(current_by_field) & set(source_by_field))
            if type(current_by_field[name].value) is not type(source_by_field[name].value)
            or current_by_field[name].value != source_by_field[name].value
        ]
        if changed:
            if len(changed) != 1 or removed or added:
                return None
            old, new = changed[0]
            return MetricOperation(
                OperationKind.REPLACE_FILTER,
                current=FilterInput(old.field, RequestFilterOperator.EQ, old.value),
                proposed=FilterInput(new.column, RequestFilterOperator.EQ, new.value),
            )
        if len(removed) == 1 and len(added) == 1:
            old, new = removed[0], added[0]
            return MetricOperation(
                OperationKind.REPLACE_FILTER,
                current=FilterInput(old.field, RequestFilterOperator.EQ, old.value),
                proposed=FilterInput(new.column, RequestFilterOperator.EQ, new.value),
            )
        if len(added) == 1 and not removed:
            new = added[0]
            return MetricOperation(
                OperationKind.ADD_FILTER,
                predicate=FilterInput(new.column, RequestFilterOperator.EQ, new.value),
            )
        if len(removed) == 1 and not added:
            old = removed[0]
            return MetricOperation(
                OperationKind.REMOVE_FILTER,
                predicate=FilterInput(old.field, RequestFilterOperator.EQ, old.value),
            )
        return None
    if analysis.pattern == "RATIO" and current_pattern == "RATIO":
        mapped_references: dict[str, set[str]] = {}
        for item in classifications:
            mapping = item.get("mapping") or {}
            canonical_metric = mapping.get("canonical_metric")
            source_measure = item.get("source_measure")
            if canonical_metric and source_measure:
                mapped_references.setdefault(str(source_measure).casefold(), set()).add(
                    str(canonical_metric)
                )

        def resolve_reference(source_name: str) -> str | None:
            matches = mapped_references.get(source_name.casefold(), set())
            return next(iter(matches)) if len(matches) == 1 else None

        numerator = resolve_reference(str(analysis.ast.get("numerator", "")))
        denominator = resolve_reference(str(analysis.ast.get("denominator", "")))
        if numerator is None or denominator is None:
            return None
        differences = []
        if numerator != current.numerator:
            differences.append(MetricOperation(OperationKind.SET_NUMERATOR, value=numerator))
        if denominator != current.denominator:
            differences.append(MetricOperation(OperationKind.SET_DENOMINATOR, value=denominator))
        return differences[0] if len(differences) == 1 else None
    return None


def _scalar_import_proposal(
    run: ImportRun,
    candidate: Mapping[str, Any],
    inventory: PowerBIModelInventory,
    ir_index: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    canonical_yaml_path: Path,
    manifest_path: Path,
    powerbi_definition_dir: Path,
    output_dir: Path,
) -> ProposalRecord | None:
    canonical_metric = candidate.get("mapping", {}).get("canonical_metric")
    if not canonical_metric or canonical_metric not in ir_index:
        return None
    measure = next(
        item for item in inventory.measures if item.object_id == candidate["source_object_id"]
    )
    operation = _candidate_scalar_operation(
        candidate,
        measure,
        ir_index[str(canonical_metric)],
        run.classifications,
    )
    if operation is None:
        return None
    entropy = hashlib.sha256(
        f"{run.import_id}\0{measure.object_id}".encode("utf-8")
    ).hexdigest()[:8]
    created = datetime.strptime(run.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    targets = (TargetName.CANONICAL_DBT, TargetName.POWER_BI, TargetName.SNOWFLAKE)
    request = MetricChangeRequest.create(
        user_request=f"Canonicalize imported Power BI object {measure.object_id} from {run.import_id}.",
        intent=ChangeIntent.UPDATE_METRIC,
        canonical_metric_name=str(canonical_metric),
        requested_semantic_change=f"Apply one proven scalar operation: {operation.kind.value}.",
        operation=operation,
        affected_targets=targets,
        target_support={target: TargetSupport.SUPPORTED_PATTERN for target in targets},
        now=created,
        entropy=entropy,
        assumptions=tuple(str(item) for item in candidate.get("assumptions", [])),
    )
    base = propose_change(
        request,
        semantic_yaml_path=canonical_yaml_path,
        manifest_path=manifest_path,
        powerbi_definition_dir=powerbi_definition_dir,
        output_dir=output_dir,
    )
    if (
        base.status is not ProposalStatus.PROPOSED
        or not base.canonical_application_available
        or base.source_snapshot_hash != snapshot["aggregate_sha256"]
    ):
        return None
    original_request = {
        "schema_version": 1,
        "change_id": base.change_id,
        "created_at": base.created_at,
        "requested_action": "CANONICALIZE_METRIC",
        "import_run_id": run.import_id,
        "source_model_path": run.source_model_path,
        "source_model_id": run.source_model_id,
        "source_object_path": str(candidate["source_object_path"]),
        "source_object_id": measure.object_id,
    }
    return replace(
        base,
        original_request=original_request,
        resolution={
            **dict(base.resolution),
            "import_classification": candidate["classification"],
            "import_candidate_semantic_hash": candidate.get("semantic_hash"),
            "source_location": candidate.get("source_location"),
            "power_bi_comparison": candidate.get("comparison"),
            "canonical_draft": candidate.get("canonical_draft"),
        },
        proposal_source=ProposalSource.POWERBI_IMPORT,
        import_run_id=run.import_id,
        source_model_path=run.source_model_path,
        source_model_id=run.source_model_id,
        source_object_path=str(candidate["source_object_path"]),
        source_object_id=measure.object_id,
        source_snapshot_sha256=run.source_snapshot_hash,
        authority_state="CANONICALIZATION_PROPOSED",
        extraction_version=run.extraction_version,
        semantic_content_sha256=None,
    )


def create_import_proposal_batch(
    import_id: str,
    *,
    store: Any,
    change_store: Any | None = None,
    included_classifications: frozenset[ImportSupportClassification] | None = None,
    canonical_yaml_path: Path = DBT_SEMANTIC_YAML,
    manifest_path: Path = DBT_SEMANTIC_MANIFEST,
    powerbi_definition_dir: Path | None = None,
    output_dir: Path = OUTPUT_DIR,
    now: datetime | None = None,
) -> ImportProposalBatch:
    if included_classifications is None:
        selected_classifications = SUPPORTED_IMPORT_CLASSIFICATIONS
    else:
        try:
            selected_classifications = frozenset(
                ImportSupportClassification(item) for item in included_classifications
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Import proposal selection contains an invalid classification.") from exc
    if not selected_classifications:
        raise ValueError("At least one supported import classification must be selected.")
    unsupported_selection = selected_classifications - SUPPORTED_IMPORT_CLASSIFICATIONS
    if unsupported_selection:
        names = ", ".join(sorted(item.value for item in unsupported_selection))
        raise ValueError(f"Import proposal selection contains unsupported classifications: {names}.")
    run = store.load_run(import_id)
    if store.try_load_discard(import_id) is not None:
        raise ValueError(f"Import run is discarded: {import_id}")
    if store.try_load_proposal_batch(import_id) is not None:
        raise ValueError(f"Import proposal batch already exists: {import_id}")
    if not verify_import_source(run):
        raise ValueError("Power BI source snapshot changed; create a new import run.")
    proposal_powerbi_definition = (
        resolve_powerbi_model_dir(
            PROJECT_ROOT / run.source_model_path,
            PROJECT_ROOT,
        )
        if powerbi_definition_dir is None
        else powerbi_definition_dir
    )
    inventory = PowerBIModelInventory.from_dict(run.to_dict()["inventory"])
    canonical = load_yaml(canonical_yaml_path) or {}
    manifest = load_json(manifest_path)
    ir_index = build_metric_ir_index(
        manifest,
        canonical,
        canonical_source="models/semantic/triathlon_semantic.yml",
    )
    snapshot = capture_source_snapshot(
        semantic_yaml_path=canonical_yaml_path,
        manifest_path=manifest_path,
        powerbi_definition_dir=proposal_powerbi_definition,
        output_dir=output_dir,
    )
    proposals: list[dict[str, Any]] = []
    manual: list[Mapping[str, Any]] = []
    unsupported: list[Mapping[str, Any]] = []
    for candidate in run.classifications:
        classification = ImportSupportClassification(str(candidate["classification"]))
        if classification in selected_classifications:
            proposals.append(_proposal_record(run, candidate, inventory, ir_index, snapshot).to_dict())
        elif classification in SUPPORTED_IMPORT_CLASSIFICATIONS:
            # The immutable import inventory retains the candidate.  A scoped
            # batch deliberately omits it without changing its classification.
            continue
        elif classification is ImportSupportClassification.UNSUPPORTED:
            unsupported.append(candidate)
        else:
            scalar = _scalar_import_proposal(
                run,
                candidate,
                inventory,
                ir_index,
                snapshot,
                canonical_yaml_path=canonical_yaml_path,
                manifest_path=manifest_path,
                powerbi_definition_dir=proposal_powerbi_definition,
                output_dir=output_dir,
            )
            if scalar is None:
                manual.append(candidate)
            else:
                proposals.append(scalar.to_dict())
    blocker_records: list[ProposalRecord] = []
    if change_store is not None and (manual or unsupported):
        # Imported lazily to avoid a module cycle: conversion_review imports
        # verify_import_source from this module.
        from .conversion_review import build_conversion_findings

        primary_findings: dict[str, Any] = {}
        for finding in build_conversion_findings(run):
            if not str(finding.rule_id).startswith("COMPARE_"):
                continue
            source_id = str(finding.source_object_id)
            if source_id in primary_findings:
                raise RuntimeError(
                    f"Import object has ambiguous primary conversion findings: {source_id}"
                )
            primary_findings[source_id] = finding
        for candidate in (*manual, *unsupported):
            source_id = str(candidate["source_object_id"])
            finding = primary_findings.get(source_id)
            if finding is None:
                raise RuntimeError(
                    f"Non-executable import candidate has no primary finding: {source_id}"
                )
            blocker_records.append(
                _import_blocker_record(
                    run,
                    candidate,
                    snapshot,
                    finding=finding,
                    relationship_findings=run.relationship_findings,
                    ir_index=ir_index,
                )
            )
        blocker_records.sort(key=lambda item: item.change_id)
        for record in blocker_records:
            if change_store.path_for(record.change_id).exists():
                raise ValueError(
                    f"Import blocker change ID already exists: {record.change_id}"
                )
    batch = ImportProposalBatch.for_run(
        run,
        proposals=proposals,
        manual_review_items=manual,
        unsupported_items=unsupported,
        blocked_child_ids=[item.change_id for item in blocker_records],
        now=now,
    )
    for record in blocker_records:
        change_store.save_proposal(record)
    store.save_proposal_batch(batch)
    return batch


def find_import_proposal(store: Any, change_id: str) -> tuple[ImportRun, ProposalRecord] | None:
    for run in store.list_runs():
        batch = store.try_load_proposal_batch(run.import_id)
        if batch is None:
            continue
        for raw in batch.proposals:
            if raw.get("change_id") == change_id:
                return run, ProposalRecord.from_dict(raw)
    return None
