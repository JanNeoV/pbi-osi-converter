"""Deterministic, proposal-only Power BI/Snowflake preview orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

import jsonschema
import yaml

from semantic_poc.src.models import (
    DBT_SEMANTIC_MANIFEST,
    DBT_SEMANTIC_YAML,
    OUTPUT_DIR,
    PBI_DEFINITION_DIR,
    PROJECT_ROOT,
    SNOWFLAKE_ENVIRONMENT,
    build_snowflake_semantic_view,
    load_json,
    load_yaml,
    normalize_dbt_semantics,
    parse_tmdl_definition,
)
from semantic_poc.src.semantic_ir import (
    SemanticMetricIR,
    SupportClassification,
    build_canonical_metric_ir_index,
    generate_dax_definition,
    generate_snowflake_definition,
    validate_cross_target,
)

from .canonical_apply import CanonicalPatchError, render_candidate
from .change_store import ChangeNotFoundError, ChangeStore, ChangeStoreError
from .import_models import canonical_json_text
from .import_store import ImportStore, ImportStoreError
from .import_workflow import find_import_proposal, verify_import_source
from .powerbi_import import resolve_powerbi_model_dir
from .proposal_engine import (
    capture_source_snapshot,
    semantic_ir_to_dict,
)
from .proposal_models import (
    ProposalRecord,
    ProposalSource,
    ProposalStatus,
)


PREVIEW_SCHEMA_VERSION = 1
QUEUE_SCHEMA_VERSION = 1
PORTABLE_POWERBI_MODEL = (
    PROJECT_ROOT
    / "semantic_poc"
    / "benchmark"
    / "pbi_trial_v2"
    / "fixtures"
    / "pbi_trial.SemanticModel"
)
PORTABLE_POWERBI_DEFINITION = PORTABLE_POWERBI_MODEL / "definition"
QUERY_PACK = (
    PROJECT_ROOT
    / "semantic_poc"
    / "benchmark"
    / "pbi_trial_v2"
    / "snowflake-query-pack.sql"
)
LEGACY_CONTRACT = PROJECT_ROOT / "semantic" / "triathlon_metric_contract.yml"
CAPTURED_SNOWFLAKE = PROJECT_ROOT / "pbit" / "snowflake_semantic_view" / "pbi_trial.yaml"
REVIEW_MEMORY_ACCEPTED = PROJECT_ROOT / "semantic_poc" / "review_memory" / "accepted"
TASK_MARKERS = PROJECT_ROOT / "semantic_poc" / "demo" / "guided_sync" / "task-markers"
PORTABLE_FIXTURE_MANIFEST = (
    PROJECT_ROOT
    / "semantic_poc"
    / "benchmark"
    / "pbi_trial_v2"
    / "fixtures"
    / "fixture-manifest.json"
)
QUEUE_SCHEMA = Path(__file__).with_name("preview_sync_validation_queue.schema.json")
RESULT_EVIDENCE_SCHEMA = Path(__file__).with_name("result_evidence.schema.json")
COMPILER_FILES = (
    "semantic_poc/src/semantic_ir.py",
    "semantic_poc/src/generate_powerbi_patch.py",
    "semantic_poc/src/generate_snowflake_yaml.py",
    "semantic_poc/src/models.py",
    "semantic_poc/src/snowflake_semantic_view.py",
    "semantic_poc/agent/canonical_apply.py",
)
FULL_FILES = frozenset(
    {
        "manifest.json",
        "canonical-candidate.yml",
        "powerbi-copy-plan.json",
        "snowflake-semantic-view.candidate.yml",
        "target-diff.json",
        "snowflake-candidate-diff.md",
        "validation-queue.json",
        "validation-queue.md",
        "cross-target-report.md",
    }
)
BLOCKED_FILES = frozenset(
    {
        "manifest.json",
        "blocked-preview.json",
        "validation-queue.json",
        "validation-queue.md",
        "cross-target-report.md",
    }
)
REVIEW_CLASSES = frozenset(
    {
        "FORMULA_REVIEW_REQUIRED",
        "MODEL_RELATIONSHIP_REVIEW_REQUIRED",
        "DATA_VALIDATION_REQUIRED",
        "METADATA_REVIEW_REQUIRED",
    }
)
TERMINAL_STATUSES = frozenset(
    {
        ProposalStatus.APPROVED,
        ProposalStatus.APPLIED_LOCAL,
        ProposalStatus.VALIDATED,
        ProposalStatus.REJECTED,
        ProposalStatus.FAILED,
        ProposalStatus.ROLLED_BACK,
        ProposalStatus.DISCARDED,
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PreviewSyncError(RuntimeError):
    exit_code = 1
    code = "PREVIEW_SYNC_FAILED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": str(self)}}


class PreviewInputError(PreviewSyncError):
    exit_code = 2
    code = "INVALID_PREVIEW_SYNC_INPUT"


class PreviewManualReview(PreviewSyncError):
    exit_code = 3
    code = "MANUAL_REVIEW_REQUIRED"


class PreviewStateError(PreviewSyncError):
    exit_code = 4
    code = "PREVIEW_SYNC_STATE_CONFLICT"


@dataclass(frozen=True)
class PreviewSyncResult:
    preview_id: str
    change_id: str
    status: str
    target_mode: str
    output_dir: str
    artifact_count: int
    result_evidence_status: str
    blocking_findings: int
    check: bool

    @property
    def exit_code(self) -> int:
        return 3 if self.blocking_findings else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_count": self.artifact_count,
            "blocking_findings": self.blocking_findings,
            "change_id": self.change_id,
            "check": self.check,
            "output_dir": self.output_dir,
            "preview_id": self.preview_id,
            "result_evidence_status": self.result_evidence_status,
            "status": self.status,
            "target_mode": self.target_mode,
        }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _tree_sha256(path: Path) -> str:
    if not path.is_dir():
        raise PreviewStateError(
            f"Protected tree is missing: {_relative_display(path)}",
            code="PROTECTED_INPUT_MISSING",
        )
    records = []
    for entry in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if entry.is_symlink():
            raise PreviewStateError(
                f"Protected tree contains a symbolic link: {_relative_display(entry)}",
                code="PROTECTED_INPUT_UNSAFE",
            )
        if entry.is_file():
            records.append(
                {
                    "path": entry.relative_to(path).as_posix(),
                    "sha256": _file_sha256(entry),
                }
            )
    return _sha256(_canonical_bytes(records))


def _canonical_tree_sha256(path: Path) -> str:
    records = []
    for entry in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if entry.is_symlink():
            raise PreviewStateError(
                f"Protected tree contains a symbolic link: {_relative_display(entry)}",
                code="PROTECTED_INPUT_UNSAFE",
            )
        if entry.is_file():
            records.append(
                {
                    "path": entry.relative_to(path).as_posix(),
                    "sha256": _file_sha256(entry),
                }
            )
    return _sha256(_canonical_bytes(records) + b"\n")


def _yaml_bytes(value: Mapping[str, Any], *, generated: bool = False) -> bytes:
    text = yaml.safe_dump(
        dict(value),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )
    if generated:
        text = (
            "# Generated file. Do not edit manually.\n"
            "# Canonical source: models/semantic/triathlon_semantic.yml\n"
            + text
        )
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _relative(path: Path) -> str:
    resolved = path.resolve(strict=True)
    root = PROJECT_ROOT.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise PreviewInputError("Preview paths must be repository-contained.")
    return resolved.relative_to(root).as_posix()


def _relative_display(path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _has_link_component(path: Path, *, stop: Path) -> bool:
    candidate = path.absolute()
    while candidate != stop and candidate != candidate.parent:
        if candidate.exists() and candidate.is_symlink():
            return True
        candidate = candidate.parent
    return False


def _safe_input(
    value: str | Path,
    *,
    label: str,
    kind: str = "file",
) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise PreviewInputError(f"{label} does not exist: {value}") from exc
    root = PROJECT_ROOT.resolve(strict=True)
    if (
        not resolved.is_relative_to(root)
        or requested.is_symlink()
        or _has_link_component(requested, stop=root)
    ):
        raise PreviewInputError(f"{label} must be repository-local and non-symlinked.")
    if kind == "file" and not resolved.is_file():
        raise PreviewInputError(f"{label} must be a regular file.")
    if kind == "dir" and not resolved.is_dir():
        raise PreviewInputError(f"{label} must be a directory.")
    return resolved


def _safe_output(
    value: str | Path,
    *,
    check: bool,
    protected: Sequence[Path],
) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    root = PROJECT_ROOT.resolve(strict=True)
    resolved = requested.resolve(strict=False)
    if not resolved.is_relative_to(root) or resolved == root:
        raise PreviewInputError("Preview output must be a repository-local subdirectory.")
    if _has_link_component(requested, stop=root):
        raise PreviewInputError("Preview output must not traverse a symbolic link.")
    for item in protected:
        protected_path = item.resolve(strict=False)
        if resolved == protected_path or resolved.is_relative_to(
            protected_path
        ) or protected_path.is_relative_to(resolved):
            raise PreviewInputError(
                f"Preview output overlaps protected input: {_relative_display(item)}"
            )
    if check:
        if not resolved.is_dir() or resolved.is_symlink():
            raise PreviewStateError(
                "Checked preview output must be an existing safe directory.",
                code="PREVIEW_BUNDLE_MISSING",
            )
    elif resolved.exists() or requested.is_symlink():
        raise PreviewStateError(
            "Preview output directory must not already exist.",
            code="PREVIEW_OUTPUT_EXISTS",
        )
    return resolved


def _compiler_fingerprint() -> tuple[list[dict[str, str]], str]:
    records = []
    for relative in sorted(COMPILER_FILES):
        path = PROJECT_ROOT / relative
        if not path.is_file():
            raise PreviewStateError(
                f"Compiler fingerprint source is missing: {relative}",
                code="COMPILER_FINGERPRINT_CONTRACT_INCOMPLETE",
            )
        records.append({"path": relative, "sha256": _file_sha256(path)})
    return records, _sha256(_canonical_bytes(records))


def _proposal_hash(proposal: ProposalRecord) -> str:
    return _sha256(_canonical_bytes(proposal.to_dict()))


def _resolve_proposal(
    change_id: str,
    *,
    change_store: ChangeStore,
    import_store: ImportStore,
) -> tuple[ProposalRecord, Any | None]:
    root_record: ProposalRecord | None = None
    try:
        root_record = change_store.load_proposal(change_id)
    except ChangeNotFoundError:
        pass
    except (ChangeStoreError, ValueError) as exc:
        raise PreviewInputError(str(exc), code="INVALID_PROPOSAL_RECORD") from exc

    imported_matches: list[tuple[Any, ProposalRecord]] = []
    blocked_matches: list[Any] = []
    batch_ids: set[str] = set()
    try:
        for run in import_store.list_runs():
            batch_ids.add(run.import_id)
            batch = import_store.try_load_proposal_batch(run.import_id)
            if batch is None:
                continue
            for raw in batch.proposals:
                if raw.get("change_id") == change_id:
                    imported_matches.append((run, ProposalRecord.from_dict(raw)))
            if change_id in batch.blocked_child_ids:
                blocked_matches.append(run)
    except (ImportStoreError, ValueError) as exc:
        raise PreviewInputError(str(exc), code="INVALID_IMPORT_CHILD") from exc
    if change_id in batch_ids:
        raise PreviewInputError(
            "Import batch IDs cannot be previewed; select one child change ID.",
            code="IMPORT_BATCH_NOT_PREVIEWABLE",
        )
    if len(imported_matches) + len(blocked_matches) > 1:
        raise PreviewInputError(
            "Change ID resolves to multiple import children.",
            code="AMBIGUOUS_IMPORT_CHILD",
        )
    if blocked_matches and root_record is None:
        raise PreviewInputError(
            "Import blocker child is missing from the root change store.",
            code="INVALID_IMPORT_CHILD",
        )
    if root_record is None and not imported_matches:
        raise PreviewInputError(
            f"Preview change ID does not exist: {change_id}",
            code="PREVIEW_CHANGE_NOT_FOUND",
        )
    imported_run = (
        imported_matches[0][0]
        if imported_matches
        else blocked_matches[0]
        if blocked_matches
        else None
    )
    imported_record = imported_matches[0][1] if imported_matches else None
    if root_record is not None and imported_record is not None:
        if _proposal_hash(root_record) != _proposal_hash(imported_record):
            raise PreviewInputError(
                "Change ID resolves to conflicting proposal records.",
                code="AMBIGUOUS_IMPORT_CHILD",
            )
        return root_record, imported_run
    proposal = root_record or imported_record
    assert proposal is not None
    if proposal.proposal_source is ProposalSource.POWERBI_IMPORT and imported_run is None:
        if proposal.import_run_id is None:
            raise PreviewInputError(
                "Import proposal has no import lineage.",
                code="IMPORT_LINEAGE_MISSING",
            )
        try:
            imported_run = import_store.load_run(proposal.import_run_id)
        except (ImportStoreError, ValueError) as exc:
            raise PreviewInputError(
                "Import proposal cannot resolve its immutable import run.",
                code="IMPORT_LINEAGE_MISSING",
            ) from exc
        batch = import_store.try_load_proposal_batch(imported_run.import_id)
        if batch is None or change_id not in batch.child_change_ids:
            raise PreviewInputError(
                "Import proposal is not bound to exactly one immutable proposal batch.",
                code="IMPORT_LINEAGE_MISSING",
            )
    if (
        proposal.proposal_source is not ProposalSource.POWERBI_IMPORT
        and imported_run is not None
    ):
        raise PreviewInputError(
            "Non-import proposal collides with an import child.",
            code="AMBIGUOUS_IMPORT_CHILD",
        )
    if proposal.proposal_source is ProposalSource.POWERBI_IMPORT:
        if proposal.status is ProposalStatus.MANUAL_REVIEW_REQUIRED:
            lineage = proposal.resolution.get("canonical_lineage_status")
            if (
                proposal.resolution.get("preview_input_kind")
                != "POWERBI_IMPORT_BLOCKER"
                or lineage not in {
                    "EXACT",
                    "UNRESOLVED_MANUAL_REVIEW_REQUIRED",
                }
                or (
                    lineage == "EXACT"
                    and (
                        not proposal.canonical_metric
                        or proposal.resolution.get("canonical_metric")
                        != proposal.canonical_metric
                    )
                )
                or (
                    lineage == "UNRESOLVED_MANUAL_REVIEW_REQUIRED"
                    and (
                        proposal.canonical_metric is not None
                        or not proposal.resolution.get("mapping_reason_codes")
                    )
                )
            ):
                raise PreviewInputError(
                    "Import blocker has malformed canonical lineage.",
                    code="IMPORT_LINEAGE_MISSING",
                )
    return proposal, imported_run


def _validate_lifecycle(proposal: ProposalRecord) -> None:
    if proposal.status in TERMINAL_STATUSES:
        raise PreviewStateError(
            f"Proposal lifecycle {proposal.status.value} cannot be previewed.",
            code="PREVIEW_LIFECYCLE_CONFLICT",
        )
    if proposal.status not in {
        ProposalStatus.PROPOSED,
        ProposalStatus.NO_OP,
        ProposalStatus.MANUAL_REVIEW_REQUIRED,
    }:
        raise PreviewStateError(
            f"Unsupported proposal lifecycle: {proposal.status.value}",
            code="PREVIEW_LIFECYCLE_CONFLICT",
        )


def _source_definition_and_hash(
    proposal: ProposalRecord,
    imported_run: Any | None,
) -> tuple[Path, str, str]:
    if proposal.proposal_source is ProposalSource.STRUCTURED_REQUEST:
        definition = _safe_input(
            PORTABLE_POWERBI_DEFINITION,
            label="Portable Power BI definition",
            kind="dir",
        )
        source_hash = str(
            proposal.source_snapshot.get("power_bi_definition_tree_sha256") or ""
        )
        expected = capture_source_snapshot(
            powerbi_definition_dir=definition,
        )
        if expected["aggregate_sha256"] != proposal.source_snapshot_hash:
            raise PreviewStateError(
                "Proposal is stale or was not bound to the portable Power BI baseline.",
                code="STALE_PROPOSAL",
            )
        fixture_manifest = load_json(
            _safe_input(
                PORTABLE_FIXTURE_MANIFEST,
                label="Task 01 portable fixture manifest",
            )
        )
        recorded_model_hash = fixture_manifest.get("fixture_model_tree_sha256")
        actual_model_hash = _canonical_tree_sha256(PORTABLE_POWERBI_MODEL)
        if (
            not isinstance(recorded_model_hash, str)
            or recorded_model_hash != actual_model_hash
        ):
            raise PreviewStateError(
                "Task 01 portable Power BI model hash is stale.",
                code="STALE_PROPOSAL",
            )
        return definition, actual_model_hash, _relative(PORTABLE_POWERBI_MODEL)

    if imported_run is None or proposal.import_run_id != imported_run.import_id:
        raise PreviewInputError(
            "Import child lineage does not resolve exactly.",
            code="IMPORT_LINEAGE_MISSING",
        )
    model = _safe_input(
        PROJECT_ROOT / imported_run.source_model_path,
        label="Imported Power BI source",
        kind="dir",
    )
    definition = resolve_powerbi_model_dir(model, PROJECT_ROOT)
    if (
        proposal.source_snapshot_sha256 != imported_run.source_snapshot_hash
        or proposal.source_model_id != imported_run.source_model_id
        or proposal.source_model_path != imported_run.source_model_path
        or not verify_import_source(imported_run)
    ):
        raise PreviewStateError(
            "Imported Power BI source snapshot is stale.",
            code="STALE_IMPORT_SOURCE",
        )
    current_snapshot = capture_source_snapshot(
        powerbi_definition_dir=definition,
    )
    if current_snapshot["aggregate_sha256"] != proposal.source_snapshot_hash:
        raise PreviewStateError(
            "Canonical/proposal snapshot is stale.",
            code="STALE_PROPOSAL",
        )
    return definition, imported_run.source_snapshot_hash, imported_run.source_model_path


def _canonical_candidates(
    proposal: ProposalRecord,
) -> tuple[bytes, Mapping[str, Any], dict[str, SemanticMetricIR], dict[str, SemanticMetricIR]]:
    baseline_bytes = DBT_SEMANTIC_YAML.read_bytes()
    try:
        candidate_bytes = (
            baseline_bytes
            if proposal.status in {ProposalStatus.NO_OP, ProposalStatus.MANUAL_REVIEW_REQUIRED}
            else render_candidate(baseline_bytes, proposal.canonical_patch)
        )
        baseline_yaml = yaml.safe_load(baseline_bytes) or {}
        candidate_yaml = yaml.safe_load(candidate_bytes) or {}
    except (OSError, yaml.YAMLError, CanonicalPatchError) as exc:
        raise PreviewInputError(
            f"Canonical proposal cannot be rendered: {exc}",
            code="UNREPRESENTABLE_CANONICAL_PATCH",
        ) from exc
    if not isinstance(candidate_yaml, Mapping):
        raise PreviewInputError("Canonical candidate must be a YAML object.")
    baseline_index = build_canonical_metric_ir_index(
        baseline_yaml,
        canonical_source="models/semantic/triathlon_semantic.yml",
        trace_id=proposal.change_id,
    )
    candidate_index = build_canonical_metric_ir_index(
        candidate_yaml,
        canonical_source="models/semantic/triathlon_semantic.yml",
        trace_id=proposal.change_id,
    )
    return candidate_bytes, candidate_yaml, baseline_index, candidate_index


def _changed_metrics(
    baseline: Mapping[str, SemanticMetricIR],
    candidate: Mapping[str, SemanticMetricIR],
) -> list[str]:
    return sorted(
        name
        for name in set(baseline) | set(candidate)
        if (
            name not in baseline
            or name not in candidate
            or semantic_ir_to_dict(baseline[name]) != semantic_ir_to_dict(candidate[name])
        )
    )


def _find_source_measure(
    powerbi: Mapping[str, Any],
    metric: SemanticMetricIR,
) -> tuple[str, str, Mapping[str, Any], str]:
    tables = powerbi.get("tables") or {}
    target_table = metric.power_bi.table
    target_measure = metric.power_bi.measure
    if target_table and target_measure:
        exact = (
            tables.get(target_table, {}).get("measures", {}).get(target_measure)
            if isinstance(tables, Mapping)
            else None
        )
        if isinstance(exact, Mapping):
            return target_table, target_measure, exact, "EXACT_CANONICAL_MAPPING"
    matches = []
    for table_name, table in tables.items():
        if not isinstance(table, Mapping):
            continue
        for measure_name, measure in (table.get("measures") or {}).items():
            if (
                target_measure
                and str(measure_name).casefold() == target_measure.casefold()
                and isinstance(measure, Mapping)
            ):
                matches.append((str(table_name), str(measure_name), measure))
    if len(matches) != 1:
        raise PreviewManualReview(
            f"Power BI mapping for {metric.canonical_name} does not resolve exactly.",
            code="POWERBI_MAPPING_UNRESOLVED",
        )
    table, measure_name, measure = matches[0]
    return table, measure_name, measure, "EXACT_UNIQUE_MEASURE_NAME"


def _powerbi_copy_plan(
    proposal: ProposalRecord,
    candidate_index: Mapping[str, SemanticMetricIR],
    changed: Sequence[str],
    definition: Path,
    *,
    source_path: str,
    source_hash: str,
    candidate_hash: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    powerbi = parse_tmdl_definition(definition, PROJECT_ROOT)
    operations: list[dict[str, Any]] = []
    metadata_operations: list[dict[str, Any]] = []
    metadata_findings: list[dict[str, Any]] = []
    if proposal.status is not ProposalStatus.NO_OP:
        for name in changed:
            metric = candidate_index.get(name)
            if metric is None or not metric.public:
                continue
            if metric.support is not SupportClassification.SUPPORTED_PATTERN:
                raise PreviewManualReview(
                    f"Candidate metric {name} is unsupported.",
                    code="CANDIDATE_PATTERN_UNSUPPORTED",
                )
            dax = generate_dax_definition(metric, candidate_index)
            snowflake = generate_snowflake_definition(metric, candidate_index)
            cross_target = validate_cross_target(metric, dax, snowflake)
            if dax.definition is None or snowflake.definition is None or not cross_target.valid:
                raise PreviewManualReview(
                    f"Candidate metric {name} is not cross-target valid.",
                    code="CROSS_TARGET_COMPILATION_BLOCKED",
                )
            table, measure_name, source_measure, resolution = _find_source_measure(
                powerbi, metric
            )
            if table != metric.power_bi.table:
                metadata_findings.append(
                    {
                        "canonical_metric": name,
                        "code": "POWERBI_SOURCE_TARGET_TABLE_DIFFER",
                        "question": (
                            "Does the recorded source-to-target table mapping "
                            "match the intended Power BI copy?"
                        ),
                        "source_id": table,
                        "source_table": table,
                        "target_ids": ["POWER_BI"],
                        "target_table": metric.power_bi.table,
                    }
                )
            for field, current, proposed in (
                (
                    "description",
                    source_measure.get("description"),
                    metric.description,
                ),
                (
                    "format_string",
                    source_measure.get("format_string"),
                    metric.power_bi.format_string,
                ),
                (
                    "display_folder",
                    source_measure.get("display_folder"),
                    metric.power_bi.display_folder,
                ),
            ):
                if proposed is not None and current != proposed:
                    metadata_operations.append(
                        {
                            "canonical_metric": name,
                            "canonical_source": proposal.canonical_file,
                            "change_id": proposal.change_id,
                            "current": current,
                            "field": field,
                            "measure": measure_name,
                            "operation": "set_measure_property",
                            "proposed": proposed,
                            "source_object_id": f"{table}[{measure_name}]",
                            "table": table,
                        }
                    )
            if metric.power_bi.display_folder is None:
                metadata_findings.append(
                    {
                        "canonical_metric": name,
                        "code": "POWERBI_DISPLAY_FOLDER_NOT_CANONICALLY_SPECIFIED",
                        "question": None,
                        "source_id": f"{table}[{measure_name}]",
                        "source_table": table,
                        "target_ids": ["POWER_BI"],
                    }
                )
            if metric.semantic_format is not None:
                metadata_findings.append(
                    {
                        "canonical_metric": name,
                        "code": "SNOWFLAKE_FORMAT_METADATA_NOT_REPRESENTABLE",
                        "question": None,
                        "source_id": name,
                        "source_table": table,
                        "target_ids": ["SNOWFLAKE"],
                    }
                )
            if source_measure.get("expression") != dax.definition:
                operations.append(
                    {
                        "canonical_metric": name,
                        "canonical_source": proposal.canonical_file,
                        "change_id": proposal.change_id,
                        "current": source_measure.get("expression"),
                        "mapping_resolution": resolution,
                        "measure": measure_name,
                        "operation": "set_measure_expression",
                        "proposed": dax.definition,
                        "source_object_id": f"{table}[{measure_name}]",
                        "table": table,
                        "target_mapping": {
                            "measure": metric.power_bi.measure,
                            "table": metric.power_bi.table,
                        },
                    }
                )
    plan = {
        "application_performed": False,
        "approval_state": proposal.approval_state.value,
        "canonical_candidate_sha256": candidate_hash,
        "canonical_source": proposal.canonical_file,
        "change_id": proposal.change_id,
        "definition_operations": operations,
        "deployment_performed": False,
        "metadata_operations": metadata_operations,
        "plan_kind": "POWERBI_COPY_PLAN",
        "read_only": True,
        "schema_version": 1,
        "source_model_path": source_path,
        "source_tree_sha256": source_hash,
        "validation_state": proposal.validation_state.value,
    }
    return plan, metadata_findings


def _environment() -> tuple[dict[str, Any], str]:
    path = _safe_input(
        SNOWFLAKE_ENVIRONMENT,
        label="Snowflake environment configuration",
    )
    value = load_yaml(path)
    if not isinstance(value, Mapping):
        raise PreviewInputError("Snowflake environment configuration must be an object.")
    required = {
        "database",
        "mart_schema",
        "semantic_schema",
        "semantic_view_name",
        "warehouse",
        "role",
    }
    if set(value) != required or any(
        not isinstance(value[key], str) or not value[key].strip() for key in required
    ):
        raise PreviewInputError(
            "Snowflake environment configuration has an invalid sanitized shape."
        )
    return dict(value), _file_sha256(path)


def _existing_target(
    target_mode: str,
    existing: str | Path | None,
    environment: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, str | None, Path | None]:
    if target_mode == "create":
        if existing is not None:
            raise PreviewInputError(
                "create mode forbids --existing-snowflake-yaml."
            )
        return None, None, None
    if target_mode != "update":
        raise PreviewInputError("target mode must be create or update.")
    if existing is None:
        raise PreviewInputError(
            "update mode requires --existing-snowflake-yaml."
        )
    path = _safe_input(existing, label="Existing Snowflake YAML")
    try:
        value = yaml.safe_load(path.read_bytes()) or {}
    except yaml.YAMLError as exc:
        raise PreviewInputError("Existing Snowflake YAML is malformed.") from exc
    if not isinstance(value, Mapping) or not isinstance(value.get("name"), str):
        raise PreviewInputError("Existing Snowflake YAML must declare a logical name.")
    if value["name"] != environment["semantic_view_name"]:
        raise PreviewStateError(
            "Existing Snowflake logical identity conflicts with configuration.",
            code="SNOWFLAKE_TARGET_IDENTITY_CONFLICT",
        )
    for table in value.get("tables", ()) or ():
        if not isinstance(table, Mapping):
            raise PreviewInputError("Existing Snowflake tables must be objects.")
        base = table.get("base_table") or {}
        if isinstance(base, Mapping) and (
            ("database" in base and base["database"] != environment["database"])
            or ("schema" in base and base["schema"] != environment["mart_schema"])
        ):
            raise PreviewStateError(
                "Existing Snowflake physical identity conflicts with configuration.",
                code="SNOWFLAKE_TARGET_IDENTITY_CONFLICT",
            )
    return value, _file_sha256(path), path


def _semantic_objects(view: Mapping[str, Any] | None) -> dict[str, Any]:
    if view is None:
        return {}
    result: dict[str, Any] = {
        f"semantic_view:{view.get('name')}": {
            key: view.get(key) for key in ("name", "description")
        }
    }
    for table in view.get("tables", ()) or ():
        if not isinstance(table, Mapping) or not isinstance(table.get("name"), str):
            continue
        table_name = str(table["name"])
        result[f"logical_table:{table_name}"] = {
            key: table.get(key)
            for key in ("name", "description", "base_table")
        }
        for collection, kind in (
            ("dimensions", "dimension"),
            ("time_dimensions", "time_dimension"),
            ("facts", "fact"),
            ("metrics", "metric"),
        ):
            for item in table.get(collection, ()) or ():
                if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                    result[f"{kind}:{table_name}.{item['name']}"] = dict(item)
    for item in view.get("metrics", ()) or ():
        if isinstance(item, Mapping) and isinstance(item.get("name"), str):
            result[f"metric:{item['name']}"] = dict(item)
    for item in view.get("relationships", ()) or ():
        if isinstance(item, Mapping) and isinstance(item.get("name"), str):
            result[f"relationship:{item['name']}"] = dict(item)
    return {key: result[key] for key in sorted(result)}


def _target_diff(
    existing: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    *,
    mode: str,
    no_op: bool,
) -> dict[str, Any]:
    before = {} if mode == "create" else _semantic_objects(existing)
    after = _semantic_objects(candidate)
    if no_op:
        before = after
    additions = [
        {"object_id": key, "after": after[key]}
        for key in sorted(set(after) - set(before))
    ]
    removals = [
        {"object_id": key, "before": before[key]}
        for key in sorted(set(before) - set(after))
    ]
    changes = []
    for key in sorted(set(before) & set(after)):
        if before[key] == after[key]:
            continue
        fields = []
        for field in sorted(set(before[key]) | set(after[key])):
            if before[key].get(field) != after[key].get(field):
                fields.append(
                    {
                        "after": after[key].get(field),
                        "before": before[key].get(field),
                        "field": field,
                    }
                )
        changes.append({"fields": fields, "object_id": key})
    return {
        "additions": additions,
        "changes": changes,
        "normalized": True,
        "removals": removals,
        "schema_version": 1,
        "summary": {
            "additions": len(additions),
            "changes": len(changes),
            "removals": len(removals),
        },
        "target_mode": mode,
    }


def _signature_payload(
    metric: SemanticMetricIR | None,
    canonical_yaml: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    keys = (
        "typed_expression",
        "typed_parameter_roles",
        "source_units",
        "target_units",
        "population",
        "grain",
        "filter_context",
        "relationship_context",
    )
    if metric is None:
        payload = {key: ({} if key == "typed_parameter_roles" else None) for key in keys}
        return payload, dict(payload), {}
    filters = [
        {
            "field": item.field,
            "keep_filters": item.keep_filters,
            "operator": item.operator.value,
            "value": item.value,
        }
        for item in metric.filters
    ]
    relationship_models = [
        model
        for model in canonical_yaml.get("semantic_models", ())
        if isinstance(model, Mapping)
        and model.get("name") == metric.source_semantic_model
    ]
    if len(relationship_models) != 1:
        raise PreviewStateError(
            f"Canonical relationship context is ambiguous for {metric.canonical_name}.",
            code="SIGNATURE_TOKENIZATION_INCOMPLETE",
        )
    relationship_value = list(
        (
            (relationship_models[0].get("config") or {}).get("meta") or {}
        ).get("semantic_contract", {}).get("relationships", ())
    )
    typed_expression = {
        "aggregation": metric.aggregation.value if metric.aggregation else None,
        "filters": filters,
        "metric_references": list(metric.metric_references),
        "pattern": metric.pattern.value if metric.pattern else None,
        "source_field": metric.source_field,
        "source_table": metric.source_physical_table,
    }
    unit = "COUNT" if metric.aggregation and metric.aggregation.value == "COUNT" else None
    concrete = {
        "typed_expression": typed_expression,
        "typed_parameter_roles": {},
        "source_units": unit,
        "target_units": unit,
        "population": filters,
        "grain": metric.source_entity,
        "filter_context": {
            "behavior": (
                "INTERSECT_EXISTING"
                if any(item.keep_filters for item in metric.filters)
                else "REPLACE_OR_ADD"
            ),
            "filters": filters,
        },
        "relationship_context": relationship_value,
    }
    roles: dict[str, Any] = {}
    bindings: dict[str, Any] = {}
    pattern_expression = json.loads(json.dumps(typed_expression))
    if metric.source_physical_table:
        token = "$SOURCE_TABLE_1"
        pattern_expression["source_table"] = token
        roles[token] = {
            "aggregation_role": "ROWSET",
            "allowed_binding_constraints": ["EXACT_SOURCE_CONTEXT"],
            "data_type": "TABLE",
            "semantic_role": "SOURCE_TABLE",
        }
        bindings[token] = {
            "canonical_object_id": metric.canonical_name,
            "source_location": metric.source.file,
            "source_object_id": metric.source_physical_table,
        }
    if metric.source_field and metric.source_field != "*":
        token = "$VALUE_FIELD_1"
        pattern_expression["source_field"] = token
        roles[token] = {
            "aggregation_role": (
                metric.aggregation.value if metric.aggregation else "VALUE"
            ),
            "allowed_binding_constraints": [
                "EXACT_DATA_TYPE",
                "EXACT_SEMANTIC_ROLE",
            ],
            "data_type": "UNKNOWN",
            "semantic_role": "VALUE_FIELD",
        }
        bindings[token] = {
            "canonical_object_id": (
                f"{metric.canonical_name}:{metric.source_field}"
            ),
            "source_location": metric.source.file,
            "source_object_id": metric.source_field,
        }
    token_by_field: dict[str, str] = {}
    for index, item in enumerate(metric.filters, 1):
        token = f"$FILTER_FIELD_{index}"
        token_by_field[item.field] = token
        pattern_expression["filters"][index - 1]["field"] = token
        roles[token] = {
            "aggregation_role": "FILTER",
            "allowed_binding_constraints": ["EXACT_TYPE", "EXACT_OPERATOR"],
            "data_type": (
                "BOOLEAN"
                if isinstance(item.value, bool)
                else "INTEGER"
                if isinstance(item.value, int)
                else "DECIMAL"
                if isinstance(item.value, float)
                else "STRING"
                if isinstance(item.value, str)
                else "UNKNOWN"
            ),
            "semantic_role": "FILTER_FIELD",
        }
        bindings[token] = {
            "canonical_object_id": f"{metric.canonical_name}:{item.field}",
            "source_location": metric.source.file,
            "source_object_id": item.field,
        }
    pattern_filter_context = json.loads(json.dumps(concrete["filter_context"]))
    for item in pattern_filter_context["filters"]:
        item["field"] = token_by_field.get(item["field"], item["field"])

    pattern_relationships = json.loads(json.dumps(relationship_value))
    relationship_names = sorted(
        {
            str(item["name"])
            for item in pattern_relationships
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }
    )
    table_names = sorted(
        {
            str(item[key])
            for item in pattern_relationships
            if isinstance(item, Mapping)
            for key in ("from_table", "to_table")
            if isinstance(item.get(key), str)
        }
    )
    column_names = sorted(
        {
            str(item[key])
            for item in pattern_relationships
            if isinstance(item, Mapping)
            for key in ("from_column", "to_column")
            if isinstance(item.get(key), str)
        }
    )
    relationship_tokens = {
        value: f"$RELATIONSHIP_{index}"
        for index, value in enumerate(relationship_names, 1)
    }
    table_tokens: dict[str, str] = {}
    related_index = 1
    for value in table_names:
        if value == metric.source_physical_table:
            table_tokens[value] = "$SOURCE_TABLE_1"
        else:
            table_tokens[value] = f"$RELATED_TABLE_{related_index}"
            related_index += 1
    column_tokens = {
        value: f"$RELATIONSHIP_FIELD_{index}"
        for index, value in enumerate(column_names, 1)
    }
    for value, token in relationship_tokens.items():
        roles[token] = {
            "aggregation_role": "RELATIONSHIP",
            "allowed_binding_constraints": ["EXACT_RELATIONSHIP_TOPOLOGY"],
            "data_type": "RELATIONSHIP",
            "semantic_role": "RELATIONSHIP_ID",
        }
        bindings[token] = {
            "canonical_object_id": f"{metric.source_semantic_model}:{value}",
            "source_location": metric.source.file,
            "source_object_id": value,
        }
    for value, token in table_tokens.items():
        if token in roles:
            continue
        roles[token] = {
            "aggregation_role": "RELATIONSHIP",
            "allowed_binding_constraints": ["EXACT_RELATIONSHIP_TOPOLOGY"],
            "data_type": "TABLE",
            "semantic_role": "RELATED_TABLE",
        }
        bindings[token] = {
            "canonical_object_id": f"{metric.source_semantic_model}:{value}",
            "source_location": metric.source.file,
            "source_object_id": value,
        }
    for value, token in column_tokens.items():
        roles[token] = {
            "aggregation_role": "RELATIONSHIP",
            "allowed_binding_constraints": [
                "EXACT_RELATIONSHIP_TOPOLOGY",
                "EXACT_COLUMN_ROLE",
            ],
            "data_type": "UNKNOWN",
            "semantic_role": "RELATIONSHIP_FIELD",
        }
        bindings[token] = {
            "canonical_object_id": f"{metric.source_semantic_model}:{value}",
            "source_location": metric.source.file,
            "source_object_id": value,
        }
    for item in pattern_relationships:
        if not isinstance(item, dict):
            raise PreviewStateError(
                "Canonical relationship context cannot be tokenized.",
                code="SIGNATURE_TOKENIZATION_INCOMPLETE",
            )
        for key, token_index in (
            ("name", relationship_tokens),
            ("from_table", table_tokens),
            ("to_table", table_tokens),
            ("from_column", column_tokens),
            ("to_column", column_tokens),
        ):
            value = item.get(key)
            if not isinstance(value, str) or value not in token_index:
                raise PreviewStateError(
                    "Canonical relationship context is incomplete.",
                    code="SIGNATURE_TOKENIZATION_INCOMPLETE",
                )
            item[key] = token_index[value]
    pattern = {
        **concrete,
        "typed_expression": pattern_expression,
        "typed_parameter_roles": roles,
        "filter_context": pattern_filter_context,
        "relationship_context": pattern_relationships,
    }
    token_values: set[str] = set()

    def collect_tokens(value: Any) -> None:
        if isinstance(value, str) and value.startswith("$"):
            token_values.add(value)
        elif isinstance(value, Mapping):
            for nested in value.values():
                collect_tokens(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_tokens(nested)

    collect_tokens(pattern)
    if (
        token_values != set(roles)
        or set(roles) != set(bindings)
        or any(
            set(role)
            != {
                "aggregation_role",
                "allowed_binding_constraints",
                "data_type",
                "semantic_role",
            }
            or not role["allowed_binding_constraints"]
            for role in roles.values()
        )
    ):
        raise PreviewStateError(
            "Typed-pattern signature tokenization is ambiguous or incomplete.",
            code="SIGNATURE_TOKENIZATION_INCOMPLETE",
        )
    return concrete, pattern, bindings


def _allowed_answers(
    finding_id: str,
    semantic_hash: str,
    *,
    evidence_allowed: bool,
    metadata_hash: str | None = None,
    unsupported: bool = False,
) -> dict[str, Any]:
    alternatives: list[dict[str, Any]] = []
    if not unsupported:
        if metadata_hash is not None:
            alternatives.append(
                {
                    "properties": {
                        "answer_id": {"const": "CONFIRM_METADATA"},
                        "parameters": {
                            "additionalProperties": False,
                            "properties": {
                                "value_sha256": {"const": metadata_hash},
                            },
                            "required": ["value_sha256"],
                            "type": "object",
                        },
                    },
                    "required": ["answer_id", "parameters"],
                    "type": "object",
                    "additionalProperties": False,
                }
            )
        else:
            alternatives.append(
                {
                    "properties": {
                        "answer_id": {"const": "CONFIRM_TYPED_SEMANTICS"},
                        "parameters": {
                            "additionalProperties": False,
                            "properties": {
                                "semantic_signature_sha256": {
                                    "const": semantic_hash
                                },
                            },
                            "required": ["semantic_signature_sha256"],
                            "type": "object",
                        },
                    },
                    "required": ["answer_id", "parameters"],
                    "type": "object",
                    "additionalProperties": False,
                }
            )
    if evidence_allowed:
        alternatives.append(
            {
                "additionalProperties": False,
                "properties": {
                    "answer_id": {"const": "SUPPLY_HASH_BOUND_EVIDENCE"},
                    "parameters": {
                        "additionalProperties": False,
                        "properties": {
                            "evidence_kind": {
                                "enum": ["DATA_RESULTS", "MODEL_METADATA"]
                            },
                            "evidence_path": {
                                "pattern": r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\x00\\]+$",
                                "type": "string",
                            },
                            "finding_id": {"const": finding_id},
                            "sha256": {
                                "pattern": "^[0-9a-f]{64}$",
                                "type": "string",
                            },
                        },
                        "required": [
                            "evidence_kind",
                            "evidence_path",
                            "finding_id",
                            "sha256",
                        ],
                        "type": "object",
                    },
                },
                "required": ["answer_id", "parameters"],
                "type": "object",
            }
        )
    alternatives.extend(
        [
            {
                "additionalProperties": False,
                "properties": {
                    "answer_id": {"const": "DEFER_MANUAL_REVIEW"},
                    "parameters": {
                        "additionalProperties": False,
                        "properties": {
                            "reason_code": {
                                "enum": [
                                    "EVIDENCE_NOT_AVAILABLE",
                                    "IMPLEMENTATION_SUPPORT_REQUIRED",
                                    "REVIEW_DEFERRED",
                                ]
                            }
                        },
                        "required": ["reason_code"],
                        "type": "object",
                    },
                },
                "required": ["answer_id", "parameters"],
                "type": "object",
            },
            {
                "additionalProperties": False,
                "properties": {
                    "answer_id": {"const": "REJECT_CHANGE"},
                    "parameters": {
                        "additionalProperties": False,
                        "properties": {
                            "reason_code": {
                                "enum": [
                                    "SEMANTIC_CHANGE_REJECTED",
                                    "UNSUPPORTED_CHANGE_REJECTED",
                                ]
                            }
                        },
                        "required": ["reason_code"],
                        "type": "object",
                    },
                },
                "required": ["answer_id", "parameters"],
                "type": "object",
            },
        ]
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "oneOf": alternatives,
    }


def _finding_id(
    preview_id: str,
    review_class: str,
    reason_code: str,
    canonical_object_id: str,
    target_ids: Sequence[str],
    dependency_ids: Sequence[str],
) -> tuple[str, dict[str, Any]]:
    payload = {
        "canonical_object_id": canonical_object_id,
        "dependency_ids": sorted(dependency_ids),
        "preview_id": preview_id,
        "primary_reason_code": reason_code,
        "review_class": review_class,
        "target_ids": sorted(target_ids),
    }
    return "fnd_" + _sha256(_canonical_bytes(payload))[:24], payload


def _queue_item(
    *,
    preview_id: str,
    review_class: str,
    reason_codes: Sequence[str],
    canonical_metric: str | None,
    source_id: str,
    source_location: str | None,
    dependency_ids: Sequence[str],
    target_ids: Sequence[str],
    bound_hashes: Mapping[str, str],
    metric: SemanticMetricIR | None,
    canonical_yaml: Mapping[str, Any],
    blocking: bool,
    status: str,
    evidence_available: Sequence[Mapping[str, Any]],
    evidence_required: Sequence[str],
    question: str | None,
    metadata_hash: str | None = None,
    unsupported: bool = False,
    signature_override: tuple[
        Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]
    ]
    | None = None,
) -> dict[str, Any]:
    canonical_id = canonical_metric or "UNRESOLVED"
    finding_id, identity = _finding_id(
        preview_id,
        review_class,
        reason_codes[0],
        canonical_id,
        target_ids,
        dependency_ids,
    )
    concrete, pattern, bindings = (
        signature_override
        if signature_override is not None
        else _signature_payload(metric, canonical_yaml)
    )
    concrete_hash = _sha256(_canonical_bytes(concrete))
    pattern_hash = _sha256(_canonical_bytes(pattern))
    result = {
        "affected_targets": sorted(target_ids),
        "allowed_answer_schema": _allowed_answers(
            finding_id,
            concrete_hash,
            evidence_allowed=review_class == "DATA_VALIDATION_REQUIRED",
            metadata_hash=metadata_hash,
            unsupported=unsupported,
        ),
        "blocking": blocking,
        "bound_input_hashes": dict(sorted(bound_hashes.items())),
        "canonical_metric": canonical_metric,
        "canonical_source_file": "models/semantic/triathlon_semantic.yml",
        "concrete_role_bindings": bindings,
        "current_disposition": (
            "BLOCKED" if blocking else "OPEN_NON_BLOCKING"
        ),
        "dependency_ids": sorted(dependency_ids),
        "evidence_available": [dict(item) for item in evidence_available],
        "evidence_required": sorted(evidence_required),
        "finding_id": finding_id,
        "finding_identity_payload": identity,
        "object_type": "METRIC" if canonical_metric else "POWERBI_OBJECT",
        "preview_id": preview_id,
        "reason_codes": list(reason_codes),
        "review_class": review_class,
        "semantic_signature_payload": concrete,
        "semantic_signature_sha256": concrete_hash,
        "source_identifier": source_id,
        "source_location": source_location,
        "status": status,
        "target_identifiers": sorted(target_ids),
        "typed_pattern_signature_payload": pattern,
        "typed_pattern_signature_sha256": pattern_hash,
    }
    if question is not None:
        result["human_question"] = question
    return result


def _import_unit_mismatch_signature(
    proposal: ProposalRecord,
    imported_run: Any | None,
) -> tuple[
    str,
    tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
] | None:
    if imported_run is None:
        return None
    inventory = imported_run.to_dict().get("inventory", {})
    measures = inventory.get("measures", [])
    matches = [
        item
        for item in measures
        if item.get("object_id") == proposal.source_object_id
    ]
    if len(matches) != 1:
        return None
    measure = matches[0]
    ast = (measure.get("analysis") or {}).get("ast")
    if not isinstance(ast, Mapping) or ast.get("kind") != "SCALED_SUM":
        return None
    table = ast.get("table")
    column = ast.get("column")
    divisor = ast.get("divisor")
    if not all(isinstance(item, str) and item for item in (table, column, divisor)):
        return None
    unit_tokens = {
        "second": "seconds",
        "seconds": "seconds",
        "minute": "minutes",
        "minutes": "minutes",
        "hour": "hours",
        "hours": "hours",
    }
    source_token = str(column).casefold().rsplit("_", 1)[-1]
    target_token = str(measure.get("name") or "").casefold().strip()
    source_unit = unit_tokens.get(source_token)
    target_unit = unit_tokens.get(target_token)
    if source_unit is None or target_unit is None or source_unit == target_unit:
        return None
    roles = {
        "$SOURCE_FIELD": {
            "aggregation_role": "SUM_INPUT",
            "allowed_binding_constraints": [
                "EXACT_NUMERIC_COLUMN_ROLE",
                "EXACT_SOURCE_CONTEXT",
            ],
            "data_type": "NUMERIC",
            "semantic_role": "SOURCE_FIELD",
        },
        "$SOURCE_TABLE": {
            "aggregation_role": "SOURCE_RELATION",
            "allowed_binding_constraints": ["EXACT_SOURCE_CONTEXT"],
            "data_type": "TABLE",
            "semantic_role": "SOURCE_TABLE",
        },
    }
    bindings = {
        "$SOURCE_FIELD": {"column": column, "table": table},
        "$SOURCE_TABLE": {"table": table},
    }
    concrete = {
        "filter_context": {"behavior": "PRESERVE_EXISTING"},
        "grain": {"table": table},
        "population": {"kind": "NON_NULL_SOURCE_VALUES"},
        "relationship_context": {
            "mode": "ACTIVE_ONLY",
            "relationships": [],
        },
        "source_units": source_unit,
        "target_units": target_unit,
        "typed_expression": {
            "aggregation": "SUM",
            "column": column,
            "divisor": divisor,
            "kind": "SCALED_SUM",
            "operator": "DIVIDE",
            "table": table,
        },
        "typed_parameter_roles": roles,
    }
    pattern = {
        **concrete,
        "grain": {"table": "$SOURCE_TABLE"},
        "typed_expression": {
            "aggregation": "SUM",
            "column": "$SOURCE_FIELD",
            "divisor": divisor,
            "kind": "SCALED_SUM",
            "operator": "DIVIDE",
            "table": "$SOURCE_TABLE",
        },
    }
    source_identifier = f"{measure.get('table')}[{measure.get('name')}]"
    return source_identifier, (concrete, pattern, bindings)


def _dependency_closure(
    metric_name: str,
    index: Mapping[str, SemanticMetricIR],
) -> tuple[list[str], list[str]]:
    dependencies: set[str] = set()
    stack = [metric_name]
    while stack:
        name = stack.pop()
        metric = index.get(name)
        if metric is None:
            continue
        for reference in (*metric.metric_references, metric.numerator, metric.denominator):
            if reference and reference not in dependencies:
                dependencies.add(reference)
                stack.append(reference)
    dependents: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, metric in index.items():
            references = set(metric.metric_references) | {
                item for item in (metric.numerator, metric.denominator) if item
            }
            if name not in dependents and references & ({metric_name} | dependents):
                dependents.add(name)
                changed = True
    return sorted(dependencies), sorted(dependents)


def _validation_queue(
    proposal: ProposalRecord,
    preview_id: str,
    candidate_index: Mapping[str, SemanticMetricIR],
    candidate_yaml: Mapping[str, Any],
    changed: Sequence[str],
    metadata_findings: Sequence[Mapping[str, Any]],
    bound_hashes: Mapping[str, str],
    result_status: str,
    runtime_evidence_required: bool,
    imported_run: Any | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if proposal.status is ProposalStatus.MANUAL_REVIEW_REQUIRED:
        resolution = proposal.resolution
        reason_codes = sorted(
            {
                item.code for item in proposal.diagnostics
            }
            | set(str(item) for item in resolution.get("unsupported_constructs", ()))
        )
        formula_codes = [
            code
            for code in reason_codes
            if "RELATIONSHIP" not in code
        ] or ["UNSUPPORTED_SEMANTIC_PATTERN"]
        relationship_codes = [
            code for code in reason_codes if "RELATIONSHIP" in code
        ]
        unit_adapter = _import_unit_mismatch_signature(proposal, imported_run)
        source_id = (
            unit_adapter[0]
            if unit_adapter is not None
            else proposal.source_object_id or "UNRESOLVED"
        )
        if unit_adapter is not None:
            formula_codes = sorted({"UNIT_CONVERSION_MISMATCH", *formula_codes})
        source_location_value = resolution.get("source_location")
        source_location = (
            str(source_location_value.get("file"))
            if isinstance(source_location_value, Mapping)
            else proposal.source_object_path
        )
        metric = (
            candidate_index.get(proposal.canonical_metric)
            if proposal.canonical_metric
            else None
        )
        items.append(
            _queue_item(
                preview_id=preview_id,
                review_class="FORMULA_REVIEW_REQUIRED",
                reason_codes=formula_codes,
                canonical_metric=proposal.canonical_metric,
                source_id=source_id,
                source_location=source_location,
                dependency_ids=(),
                target_ids=("POWER_BI", "SNOWFLAKE"),
                bound_hashes=bound_hashes,
                metric=metric,
                canonical_yaml=candidate_yaml,
                blocking=True,
                status="MANUAL_REVIEW_REQUIRED",
                evidence_available=(),
                evidence_required=("DETERMINISTIC_COMPILER_SUPPORT",),
                question=None,
                unsupported=True,
                signature_override=(
                    unit_adapter[1] if unit_adapter is not None else None
                ),
            )
        )
        if relationship_codes:
            items.append(
                _queue_item(
                    preview_id=preview_id,
                    review_class="MODEL_RELATIONSHIP_REVIEW_REQUIRED",
                    reason_codes=relationship_codes,
                    canonical_metric=proposal.canonical_metric,
                    source_id=source_id,
                    source_location=source_location,
                    dependency_ids=(),
                    target_ids=("POWER_BI", "SNOWFLAKE"),
                    bound_hashes=bound_hashes,
                    metric=metric,
                    canonical_yaml=candidate_yaml,
                    blocking=True,
                    status="MANUAL_REVIEW_REQUIRED",
                    evidence_available=(),
                    evidence_required=("DETERMINISTIC_RELATIONSHIP_SUPPORT",),
                    question=None,
                    unsupported=True,
                )
            )
    else:
        focus = list(changed)
        if not focus and proposal.canonical_metric:
            focus = [proposal.canonical_metric]
        for name in focus:
            metric = candidate_index.get(name)
            if metric is None:
                continue
            dependencies, dependents = _dependency_closure(name, candidate_index)
            target_ids = [
                f"POWER_BI:{metric.power_bi.table}.{metric.power_bi.measure}",
                f"SNOWFLAKE:{metric.snowflake.logical_table}.{metric.snowflake.metric_name}",
                *[f"CANONICAL_DEPENDENT:{item}" for item in dependents],
            ]
            evidence = (
                [{"kind": "RESULT_EVIDENCE", "status": result_status}]
                if result_status != "NOT_AVAILABLE"
                else []
            )
            blocking = result_status == "FAILED" or (
                runtime_evidence_required and result_status != "PASSED"
            )
            items.append(
                _queue_item(
                    preview_id=preview_id,
                    review_class="DATA_VALIDATION_REQUIRED",
                    reason_codes=(
                        ["RUNTIME_RESULT_MISMATCH"]
                        if result_status == "FAILED"
                        else ["REQUIRED_RUNTIME_EVIDENCE_MISSING"]
                        if runtime_evidence_required
                        and result_status != "PASSED"
                        else ["OPTIONAL_RUNTIME_EVIDENCE_NOT_AVAILABLE"]
                        if result_status == "NOT_AVAILABLE"
                        else ["RUNTIME_RESULT_EVIDENCE_PASSED"]
                    ),
                    canonical_metric=name,
                    source_id=name,
                    source_location=metric.source.file,
                    dependency_ids=dependencies,
                    target_ids=target_ids,
                    bound_hashes=bound_hashes,
                    metric=metric,
                    canonical_yaml=candidate_yaml,
                    blocking=blocking,
                    status=result_status,
                    evidence_available=evidence,
                    evidence_required=(
                        ["HASH_BOUND_DATA_RESULTS"]
                        if result_status != "PASSED"
                        else []
                    ),
                    question=(
                        "Can hash-bound Power BI and Snowflake results be supplied for this finding?"
                        if blocking
                        else None
                    ),
                )
            )
            for dependent_name in dependents:
                dependent = candidate_index.get(dependent_name)
                if (
                    dependent is None
                    or dependent.pattern is None
                    or dependent.pattern.value != "RATIO"
                ):
                    continue
                items.append(
                    _queue_item(
                        preview_id=preview_id,
                        review_class="DATA_VALIDATION_REQUIRED",
                        reason_codes=(
                            "BLANK_NULL_REPRESENTATION_JSON_NULL",
                            "TRANSITIVE_DEPENDENCY_RISK",
                        ),
                        canonical_metric=dependent_name,
                        source_id=dependent_name,
                        source_location=dependent.source.file,
                        dependency_ids=(name,),
                        target_ids=(
                            "POWER_BI:"
                            f"{dependent.power_bi.table}."
                            f"{dependent.power_bi.measure}",
                            "SNOWFLAKE:"
                            f"{dependent.snowflake.logical_table}."
                            f"{dependent.snowflake.metric_name}",
                        ),
                        bound_hashes=bound_hashes,
                        metric=dependent,
                        canonical_yaml=candidate_yaml,
                        blocking=False,
                        status="NOT_AVAILABLE",
                        evidence_available=(),
                        evidence_required=("HASH_BOUND_DATA_RESULTS",),
                        question=None,
                    )
                )
        for finding in metadata_findings:
            name = str(finding["canonical_metric"])
            metric = candidate_index.get(name)
            metadata_hash = _sha256(_canonical_bytes(finding))
            items.append(
                _queue_item(
                    preview_id=preview_id,
                    review_class="METADATA_REVIEW_REQUIRED",
                    reason_codes=[str(finding["code"])],
                    canonical_metric=name,
                    source_id=str(
                        finding.get("source_id") or finding["source_table"]
                    ),
                    source_location=metric.source.file if metric else None,
                    dependency_ids=(),
                    target_ids=tuple(
                        str(item)
                        for item in finding.get("target_ids", ("POWER_BI",))
                    ),
                    bound_hashes=bound_hashes,
                    metric=metric,
                    canonical_yaml=candidate_yaml,
                    blocking=False,
                    status="REVIEW_REQUIRED",
                    evidence_available=[
                        {
                            "kind": "PROPOSED_METADATA",
                            "sha256": metadata_hash,
                        }
                    ],
                    evidence_required=(),
                    question=(
                        str(finding["question"])
                        if finding.get("question")
                        else None
                    ),
                    metadata_hash=metadata_hash,
                )
            )
    ids: dict[str, bytes] = {}
    for item in items:
        payload = _canonical_bytes(item["finding_identity_payload"])
        previous = ids.setdefault(item["finding_id"], payload)
        if previous != payload:
            raise PreviewStateError(
                "Validation finding ID collision.",
                code="VALIDATION_FINDING_ID_COLLISION",
            )
    result = {
        "blocking_count": sum(bool(item["blocking"]) for item in items),
        "findings": sorted(items, key=lambda item: item["finding_id"]),
        "preview_id": preview_id,
        "review_classes": sorted(REVIEW_CLASSES),
        "schema_version": QUEUE_SCHEMA_VERSION,
    }
    try:
        schema = json.loads(QUEUE_SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(result)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        jsonschema.SchemaError,
        jsonschema.ValidationError,
    ) as exc:
        raise PreviewStateError(
            f"Validation queue failed its strict schema: {exc}",
            code="VALIDATION_QUEUE_INVALID",
        ) from exc
    return result


def _result_evidence(
    path_value: str | Path | None,
    expected_hashes: Mapping[str, str],
) -> tuple[str, str | None, Path | None]:
    if path_value is None:
        return "NOT_AVAILABLE", None, None
    path = _safe_input(path_value, label="Preview result evidence")
    payload = path.read_bytes()
    try:
        value = json.loads(payload.decode("utf-8"))
        schema = json.loads(RESULT_EVIDENCE_SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(value)
    except (
        UnicodeError,
        json.JSONDecodeError,
        OSError,
        jsonschema.ValidationError,
    ) as exc:
        raise PreviewInputError(
            f"Preview result evidence is invalid: {exc}",
            code="INVALID_RESULT_EVIDENCE",
        ) from exc
    if value.get("profile") != "PREVIEW_SYNC_V1":
        raise PreviewInputError(
            "Preview result evidence profile must be PREVIEW_SYNC_V1.",
            code="INVALID_RESULT_EVIDENCE_PROFILE",
        )
    if value.get("subject_hashes") != dict(expected_hashes):
        raise PreviewStateError(
            "Preview result evidence is stale.",
            code="STALE_RESULT_EVIDENCE",
        )
    status = "PASSED"
    if not value["results"]:
        status = "NOT_AVAILABLE"
    for result in value["results"]:
        endpoints = (result["power_bi"], result["snowflake"])
        if any(
            endpoint["status"] != "AVAILABLE" or not endpoint["complete"]
            for endpoint in endpoints
        ):
            if status != "FAILED":
                status = "NOT_AVAILABLE"
            continue
        for endpoint in endpoints:
            row_hash = _sha256(_canonical_bytes(endpoint["rows"]))
            if endpoint["result_sha256"] != row_hash:
                raise PreviewInputError(
                    "Result evidence row hash does not match canonical rows.",
                    code="INVALID_RESULT_EVIDENCE",
                )
        row_maps: list[dict[bytes, Any]] = []
        for endpoint in endpoints:
            normalized: dict[bytes, Any] = {}
            for row in endpoint["rows"]:
                identity = _canonical_bytes(row["coordinates"])
                if identity in normalized:
                    raise PreviewInputError(
                        "Result evidence contains duplicate coordinates.",
                        code="INVALID_RESULT_EVIDENCE",
                    )
                normalized[identity] = row["value"]
            row_maps.append(normalized)
        mismatch = set(row_maps[0]) != set(row_maps[1])
        if not mismatch:
            for identity in sorted(row_maps[0]):
                left = row_maps[0][identity]
                right = row_maps[1][identity]
                if left is None or right is None:
                    mismatch = left is not right
                elif result["value_type"] == "INTEGER":
                    mismatch = (
                        isinstance(left, bool)
                        or isinstance(right, bool)
                        or not isinstance(left, int)
                        or not isinstance(right, int)
                        or left != right
                    )
                else:
                    mismatch = (
                        isinstance(left, bool)
                        or isinstance(right, bool)
                        or not isinstance(left, (int, float))
                        or not isinstance(right, (int, float))
                        or not math.isclose(
                            float(left),
                            float(right),
                            rel_tol=1e-9,
                            abs_tol=1e-9,
                        )
                    )
                if mismatch:
                    break
        if mismatch:
            status = "FAILED"
    return status, _sha256(payload), path


def _preview_id(
    *,
    change_id: str,
    proposal_hash: str,
    target_mode: str,
    environment_hash: str,
    existing_hash: str | None,
    evidence_hash: str | None,
) -> str:
    payload = {
        "change_id": change_id,
        "environment_config_hash": environment_hash,
        "existing_target_hash": existing_hash,
        "proposal_hash": proposal_hash,
        "result_evidence_hash": evidence_hash,
        "target_mode": target_mode,
    }
    return "prv_" + _sha256(_canonical_bytes(payload))[:24]


def _protected_snapshot(
    source_definition: Path,
    *,
    existing_target: Path | None = None,
    result_evidence: Path | None = None,
) -> list[dict[str, Any]]:
    inputs: list[tuple[str, Path, str]] = [
        ("FILE", DBT_SEMANTIC_YAML, "CANONICAL_CONTRACT"),
        ("FILE", LEGACY_CONTRACT, "DEPRECATED_CONTRACT"),
        ("FILE", CAPTURED_SNOWFLAKE, "CAPTURED_SNOWFLAKE"),
        ("FILE", SNOWFLAKE_ENVIRONMENT, "SNOWFLAKE_ENVIRONMENT"),
        ("FILE", QUERY_PACK, "SNOWFLAKE_QUERY_PACK"),
        ("FILE", PORTABLE_FIXTURE_MANIFEST, "PORTABLE_FIXTURE_MANIFEST"),
        ("FILE", QUEUE_SCHEMA, "VALIDATION_QUEUE_SCHEMA"),
        ("FILE", RESULT_EVIDENCE_SCHEMA, "RESULT_EVIDENCE_SCHEMA"),
        ("TREE", PORTABLE_POWERBI_MODEL, "PORTABLE_POWERBI_FIXTURE"),
        ("TREE", OUTPUT_DIR, "EXISTING_GENERATED_OUTPUTS"),
        ("TREE", REVIEW_MEMORY_ACCEPTED, "ACCEPTED_REVIEW_MEMORY"),
        ("TREE", source_definition, "POWERBI_SOURCE"),
    ]
    if existing_target is not None:
        inputs.append(("FILE", existing_target, "EXISTING_SNOWFLAKE_TARGET"))
    if result_evidence is not None:
        inputs.append(("FILE", result_evidence, "RESULT_EVIDENCE"))
    result = []
    seen: set[tuple[str, str]] = set()
    for kind, path, label in inputs:
        relative = _relative(path)
        identity = (kind, relative)
        if identity in seen:
            continue
        seen.add(identity)
        digest = _file_sha256(path) if kind == "FILE" else _tree_sha256(path)
        result.append(
            {
                "after_sha256": digest,
                "before_sha256": digest,
                "kind": kind,
                "label": label,
                "path": relative,
            }
        )
    return sorted(result, key=lambda item: (item["path"], item["kind"]))


def _render_diff_markdown(diff: Mapping[str, Any]) -> bytes:
    lines = [
        "# Snowflake candidate semantic diff",
        "",
        f"- Target mode: `{diff['target_mode']}`",
        f"- Additions: `{diff['summary']['additions']}`",
        f"- Removals: `{diff['summary']['removals']}`",
        f"- Changes: `{diff['summary']['changes']}`",
        "",
    ]
    for collection, title in (
        ("additions", "Additions"),
        ("removals", "Removals"),
        ("changes", "Changes"),
    ):
        lines.extend([f"## {title}", ""])
        values = diff[collection]
        if not values:
            lines.append("- None")
        else:
            lines.extend(f"- `{item['object_id']}`" for item in values)
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _render_queue_markdown(queue: Mapping[str, Any]) -> bytes:
    lines = [
        "# Preview validation queue",
        "",
        f"- Preview ID: `{queue['preview_id']}`",
        f"- Blocking findings: `{queue['blocking_count']}`",
        "",
    ]
    if not queue["findings"]:
        lines.append("No validation findings.")
    for item in queue["findings"]:
        lines.extend(
            [
                f"## {item['finding_id']}",
                "",
                f"- Review class: `{item['review_class']}`",
                f"- Canonical metric: `{item['canonical_metric'] or 'UNRESOLVED'}`",
                f"- Reason codes: `{', '.join(item['reason_codes'])}`",
                f"- Blocking: `{str(item['blocking']).lower()}`",
                f"- Status: `{item['status']}`",
            ]
        )
        if "human_question" in item:
            lines.append(f"- Question: {item['human_question']}")
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _render_cross_target(
    proposal: ProposalRecord,
    preview_id: str,
    queue: Mapping[str, Any],
    diff: Mapping[str, Any] | None,
) -> bytes:
    lines = [
        "# Cross-target preview report",
        "",
        f"- Preview ID: `{preview_id}`",
        f"- Change ID: `{proposal.change_id}`",
        f"- Proposal status: `{proposal.status.value}`",
        f"- Canonical source: `{proposal.canonical_file}`",
        f"- Blocking findings: `{queue['blocking_count']}`",
        "- Approval performed: `false`",
        "- Application performed: `false`",
        "- Network contacted: `false`",
        "- Deployment performed: `false`",
    ]
    if diff is not None:
        lines.append(
            f"- Snowflake semantic changes: `{diff['summary']['changes']}`"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _tree_bytes_for_check(path: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        if item.is_symlink() or not item.is_file():
            raise PreviewStateError(
                "Regenerated preview contains an unsafe entry.",
                code="PREVIEW_BUNDLE_INVALID",
            )
        result[item.relative_to(path).as_posix()] = item.read_bytes()
    return result


def _write_bundle(
    output: Path,
    contents: Mapping[str, bytes],
    *,
    check: bool,
) -> None:
    expected = set(contents)
    if check:
        actual = {
            item.name
            for item in output.iterdir()
            if item.is_file() and not item.is_symlink()
        }
        if any(item.is_symlink() or not item.is_file() for item in output.iterdir()):
            raise PreviewStateError(
                "Preview bundle contains unsafe entries.",
                code="PREVIEW_BUNDLE_INVALID",
            )
        if actual != expected:
            raise PreviewStateError(
                "Preview bundle file set is incomplete or unexpected.",
                code="PREVIEW_BUNDLE_INVALID",
            )
        with tempfile.TemporaryDirectory(
            prefix=".preview-check-a-", dir=PROJECT_ROOT / ".tmp"
        ) as first, tempfile.TemporaryDirectory(
            prefix=".preview-check-b-", dir=PROJECT_ROOT / ".tmp"
        ) as second:
            first_path = Path(first)
            second_path = Path(second)
            for name, payload in contents.items():
                (first_path / name).write_bytes(payload)
                (second_path / name).write_bytes(payload)
            for name, payload in contents.items():
                if (
                    (first_path / name).read_bytes()
                    != (second_path / name).read_bytes()
                    or output.joinpath(name).read_bytes() != payload
                ):
                    raise PreviewStateError(
                        f"Preview bundle is stale: {name}",
                        code="PREVIEW_BUNDLE_STALE",
                    )
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=".preview-stage-", dir=output.parent)
    )
    try:
        for name in sorted(contents):
            path = stage / name
            with path.open("xb") as handle:
                handle.write(contents[name])
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(stage, output)
    except Exception:
        if stage.exists():
            for item in stage.iterdir():
                if item.is_file() and not item.is_symlink():
                    item.unlink()
            stage.rmdir()
        raise


def preview_sync(
    change_id: str,
    *,
    target_mode: str,
    output_dir: str | Path,
    existing_snowflake_yaml: str | Path | None = None,
    result_evidence: str | Path | None = None,
    check: bool = False,
    change_store: ChangeStore,
    import_store: ImportStore,
) -> PreviewSyncResult:
    if check:
        (PROJECT_ROOT / ".tmp").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".preview-regenerate-a-",
            dir=PROJECT_ROOT / ".tmp",
        ) as first_root, tempfile.TemporaryDirectory(
            prefix=".preview-regenerate-b-",
            dir=PROJECT_ROOT / ".tmp",
        ) as second_root:
            first_output = Path(first_root) / "bundle"
            second_output = Path(second_root) / "bundle"
            preview_sync(
                change_id,
                target_mode=target_mode,
                output_dir=first_output,
                existing_snowflake_yaml=existing_snowflake_yaml,
                result_evidence=result_evidence,
                check=False,
                change_store=change_store,
                import_store=import_store,
            )
            preview_sync(
                change_id,
                target_mode=target_mode,
                output_dir=second_output,
                existing_snowflake_yaml=existing_snowflake_yaml,
                result_evidence=result_evidence,
                check=False,
                change_store=change_store,
                import_store=import_store,
            )
            if _tree_bytes_for_check(first_output) != _tree_bytes_for_check(
                second_output
            ):
                raise PreviewStateError(
                    "Independent preview regenerations are not byte-identical.",
                    code="PREVIEW_NOT_REPRODUCIBLE",
                )
    proposal, imported_run = _resolve_proposal(
        change_id,
        change_store=change_store,
        import_store=import_store,
    )
    _validate_lifecycle(proposal)
    definition, source_hash, source_path = _source_definition_and_hash(
        proposal, imported_run
    )
    environment, environment_hash = _environment()
    existing, existing_hash, existing_path = _existing_target(
        target_mode, existing_snowflake_yaml, environment
    )
    candidate_bytes, candidate_yaml, baseline_index, candidate_index = (
        _canonical_candidates(proposal)
    )
    changed = _changed_metrics(baseline_index, candidate_index)
    if proposal.status is ProposalStatus.PROPOSED and not changed:
        raise PreviewStateError(
            "PROPOSED record produces no canonical semantic change.",
            code="PROPOSAL_BASELINE_CHANGED",
        )
    if proposal.status is ProposalStatus.NO_OP:
        changed = []
    proposal_hash = _proposal_hash(proposal)
    candidate_hash = _sha256(candidate_bytes)
    compiler_files, compiler_fingerprint = _compiler_fingerprint()
    protected = [
        DBT_SEMANTIC_YAML,
        LEGACY_CONTRACT,
        CAPTURED_SNOWFLAKE,
        PORTABLE_POWERBI_MODEL,
        OUTPUT_DIR,
        REVIEW_MEMORY_ACCEPTED,
        TASK_MARKERS,
        PORTABLE_FIXTURE_MANIFEST,
        definition,
        SNOWFLAKE_ENVIRONMENT,
        QUERY_PACK,
        change_store.root,
        import_store.root,
    ]
    if existing_path is not None:
        protected.append(existing_path)

    blocked = proposal.status is ProposalStatus.MANUAL_REVIEW_REQUIRED
    plan: dict[str, Any] | None = None
    plan_bytes: bytes | None = None
    snowflake_view: Mapping[str, Any] | None = None
    snowflake_bytes: bytes | None = None
    metadata_findings: list[dict[str, Any]] = []
    if not blocked:
        plan, metadata_findings = _powerbi_copy_plan(
            proposal,
            candidate_index,
            changed,
            definition,
            source_path=source_path,
            source_hash=source_hash,
            candidate_hash=candidate_hash,
        )
        plan_bytes = _json_bytes(plan)
        semantics = normalize_dbt_semantics(
            load_json(DBT_SEMANTIC_MANIFEST),
            candidate_yaml,
            semantic_yaml_path=DBT_SEMANTIC_YAML,
            semantic_manifest_path=DBT_SEMANTIC_MANIFEST,
        )
        snowflake_view = build_snowflake_semantic_view(
            semantics,
            environment,
            metric_ir_index=candidate_index,
        )
        if snowflake_view.get("unsupported_metrics"):
            affected_unsupported = {
                str(item.get("metric"))
                for item in snowflake_view["unsupported_metrics"]
                if isinstance(item, Mapping)
            } & set(changed)
            if affected_unsupported:
                raise PreviewManualReview(
                    "Changed candidate contains unsupported Snowflake metrics: "
                    + ", ".join(sorted(affected_unsupported)),
                    code="CANDIDATE_PATTERN_UNSUPPORTED",
                )
        snowflake_bytes = _yaml_bytes(snowflake_view, generated=True)

    evidence_subjects = {
        "proposal_sha256": proposal_hash,
        "canonical_baseline_sha256": _file_sha256(DBT_SEMANTIC_YAML),
        "canonical_candidate_sha256": candidate_hash,
        "powerbi_source_tree_sha256": source_hash,
        "powerbi_copy_plan_sha256": (
            _sha256(plan_bytes) if plan_bytes is not None else "0" * 64
        ),
        "snowflake_candidate_sha256": (
            _sha256(snowflake_bytes) if snowflake_bytes is not None else "0" * 64
        ),
        "snowflake_environment_sha256": environment_hash,
        "snowflake_query_pack_sha256": _file_sha256(QUERY_PACK),
    }
    result_status, evidence_hash, evidence_path = _result_evidence(
        result_evidence, evidence_subjects
    )
    if evidence_path is not None:
        protected.append(evidence_path)
    preview_id = _preview_id(
        change_id=proposal.change_id,
        proposal_hash=proposal_hash,
        target_mode=target_mode,
        environment_hash=environment_hash,
        existing_hash=existing_hash,
        evidence_hash=evidence_hash,
    )
    bound_hashes = {
        "canonical_baseline_sha256": _file_sha256(DBT_SEMANTIC_YAML),
        "canonical_candidate_sha256": candidate_hash,
        "proposal_sha256": proposal_hash,
        "powerbi_source_tree_sha256": source_hash,
        "snowflake_environment_sha256": environment_hash,
    }
    if existing_hash is not None:
        bound_hashes["existing_snowflake_sha256"] = existing_hash
    if evidence_hash is not None:
        bound_hashes["result_evidence_sha256"] = evidence_hash
    queue = _validation_queue(
        proposal,
        preview_id,
        candidate_index,
        candidate_yaml,
        changed,
        metadata_findings,
        bound_hashes,
        result_status,
        bool(proposal.resolution.get("runtime_evidence_required")),
        imported_run,
    )
    diff = (
        None
        if blocked
        else _target_diff(
            existing,
            snowflake_view or {},
            mode=target_mode,
            no_op=proposal.status is ProposalStatus.NO_OP,
        )
    )
    contents: dict[str, bytes]
    if blocked:
        blocked_envelope = {
            "bound_hashes": dict(sorted(bound_hashes.items())),
            "change_id": proposal.change_id,
            "executable_artifacts_emitted": False,
            "finding_ids": [
                item["finding_id"] for item in queue["findings"]
            ],
            "operations": [],
            "preview_id": preview_id,
            "reason_codes": sorted(
                {code for item in queue["findings"] for code in item["reason_codes"]}
            ),
            "schema_version": 1,
        }
        contents = {
            "blocked-preview.json": _json_bytes(blocked_envelope),
            "cross-target-report.md": _render_cross_target(
                proposal, preview_id, queue, None
            ),
            "validation-queue.json": _json_bytes(queue),
            "validation-queue.md": _render_queue_markdown(queue),
        }
    else:
        assert plan_bytes is not None
        assert snowflake_bytes is not None
        assert diff is not None
        contents = {
            "canonical-candidate.yml": candidate_bytes,
            "cross-target-report.md": _render_cross_target(
                proposal, preview_id, queue, diff
            ),
            "powerbi-copy-plan.json": plan_bytes,
            "snowflake-candidate-diff.md": _render_diff_markdown(diff),
            "snowflake-semantic-view.candidate.yml": snowflake_bytes,
            "target-diff.json": _json_bytes(diff),
            "validation-queue.json": _json_bytes(queue),
            "validation-queue.md": _render_queue_markdown(queue),
        }
    artifact_hashes = {
        name: _sha256(payload) for name, payload in sorted(contents.items())
    }
    stored_proposal_path = change_store.path_for(proposal.change_id)
    batch = (
        import_store.try_load_proposal_batch(imported_run.import_id)
        if imported_run is not None
        else None
    )
    powerbi_inventory_hash = _sha256(
        _canonical_bytes(
            imported_run.to_dict()["inventory"]
            if imported_run is not None
            else parse_tmdl_definition(definition, PROJECT_ROOT)
        )
    )
    protected_snapshot = _protected_snapshot(
        definition,
        existing_target=existing_path,
        result_evidence=evidence_path,
    )
    manifest = {
        "application_performed": False,
        "approval_performed": False,
        "artifact_hashes": artifact_hashes,
        "bundle_kind": "BLOCKED" if blocked else "FULL",
        "canonical_baseline_sha256": _file_sha256(DBT_SEMANTIC_YAML),
        "canonical_candidate_sha256": candidate_hash,
        "canonical_source": proposal.canonical_file,
        "change_id": proposal.change_id,
        "compiler_files": compiler_files,
        "compiler_fingerprint": compiler_fingerprint,
        "deployment_performed": False,
        "existing_snowflake_sha256": existing_hash,
        "import_batch_sha256": (
            _sha256(_canonical_bytes(batch.to_dict()))
            if batch is not None
            else None
        ),
        "import_run_sha256": (
            _sha256(_canonical_bytes(imported_run.to_dict()))
            if imported_run is not None
            else None
        ),
        "network_contacted": False,
        "preview_id": preview_id,
        "proposal_sha256": proposal_hash,
        "proposal_store_record_sha256": (
            _file_sha256(stored_proposal_path)
            if stored_proposal_path.is_file()
            else None
        ),
        "proposal_source": proposal.proposal_source.value,
        "proposal_status": proposal.status.value,
        "protected_inputs": protected_snapshot,
        "result_evidence_sha256": evidence_hash,
        "result_evidence_schema_sha256": _file_sha256(RESULT_EVIDENCE_SCHEMA),
        "result_evidence_status": result_status,
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "blocked_preview_sha256": artifact_hashes.get("blocked-preview.json"),
        "powerbi_copy_plan_sha256": artifact_hashes.get(
            "powerbi-copy-plan.json"
        ),
        "powerbi_inventory_sha256": powerbi_inventory_hash,
        "snowflake_candidate_sha256": artifact_hashes.get(
            "snowflake-semantic-view.candidate.yml"
        ),
        "snowflake_environment": {
            "database": environment["database"],
            "mart_schema": environment["mart_schema"],
            "semantic_schema": environment["semantic_schema"],
            "semantic_view_name": environment["semantic_view_name"],
        },
        "snowflake_environment_sha256": environment_hash,
        "snowflake_query_pack_sha256": _file_sha256(QUERY_PACK),
        "source_edit_performed": False,
        "source_powerbi_path": source_path,
        "source_powerbi_tree_sha256": source_hash,
        "target_mode": target_mode,
        "tool": "semantic-agent preview-sync",
        "tool_version": "preview-sync-v1",
        "validation_queue_schema_sha256": _file_sha256(QUEUE_SCHEMA),
    }
    contents["manifest.json"] = _json_bytes(manifest)
    expected_files = BLOCKED_FILES if blocked else FULL_FILES
    if set(contents) != expected_files:
        raise PreviewStateError(
            "Preview bundle file contract is incomplete.",
            code="PREVIEW_BUNDLE_INVALID",
        )
    output = _safe_output(
        output_dir,
        check=check,
        protected=protected,
    )
    _write_bundle(output, contents, check=check)
    after = _protected_snapshot(
        definition,
        existing_target=existing_path,
        result_evidence=evidence_path,
    )
    if protected_snapshot != after:
        raise PreviewStateError(
            "A protected source changed during preview generation.",
            code="PROTECTED_SOURCE_DRIFT",
        )
    return PreviewSyncResult(
        preview_id=preview_id,
        change_id=proposal.change_id,
        status=(
            "MANUAL_REVIEW_REQUIRED"
            if queue["blocking_count"]
            else "PREVIEW_CREATED"
        ),
        target_mode=target_mode,
        output_dir=output.relative_to(PROJECT_ROOT.resolve()).as_posix(),
        artifact_count=len(contents),
        result_evidence_status=result_status,
        blocking_findings=queue["blocking_count"],
        check=check,
    )


__all__ = [
    "PreviewInputError",
    "PreviewManualReview",
    "PreviewStateError",
    "PreviewSyncError",
    "PreviewSyncResult",
    "preview_sync",
]
