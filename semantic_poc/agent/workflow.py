from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from semantic_poc.src.apply_powerbi_patch import apply_powerbi_patch
from semantic_poc.src.models import (
    CANONICAL_SOURCE,
    DBT_SEMANTIC_MANIFEST,
    DBT_SEMANTIC_YAML,
    OUTPUT_DIR,
    PBI_DEFINITION_DIR,
    POWERBI_PATCH_OUTPUT,
    PROJECT_ROOT,
    load_json,
    load_yaml,
    parse_tmdl_definition,
)
from semantic_poc.src.semantic_ir import build_metric_ir_index

from .canonical_apply import CanonicalPatchError, atomic_write, render_candidate, sha256_bytes, sha256_file
from .change_store import DEFAULT_CHANGE_DIR, ChangeStore, ChangeStoreError
from .proposal_engine import CORE_OUTPUT_NAMES, proposal_is_stale, semantic_ir_to_dict
from .powerbi_import import resolve_powerbi_model_dir
from .proposal_models import (
    LocalApplicationState,
    PROPOSAL_SCHEMA_VERSION,
    ProposalRecord,
    ProposalSource,
    ProposalStatus,
)
from .schemas import ApprovalState, DeploymentState, ValidationState, utc_timestamp, validate_change_id


KNOWN_RELATIONSHIP_FINDING = "fct_result.distance_id -> dim_distance.distance_id is missing in Power BI."
SAFE_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class WorkflowError(RuntimeError):
    code = "WORKFLOW_STATE_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class WorkflowInputError(WorkflowError):
    code = "INVALID_WORKFLOW_INPUT"


class WorkflowStateError(WorkflowError):
    code = "INVALID_STATE_TRANSITION"


class WorkflowManualReview(WorkflowError):
    code = "MANUAL_REVIEW_REQUIRED"


def validate_actor(actor: str) -> str:
    if not isinstance(actor, str) or not SAFE_ACTOR.fullmatch(actor):
        raise WorkflowInputError(
            "Actor must contain 1-64 letters, digits, periods, underscores, or hyphens and start with a letter or digit."
        )
    return actor


def _event(
    proposal: ProposalRecord,
    *,
    actor: str,
    action: str,
    to_status: ProposalStatus,
    result: str = "SUCCESS",
    details: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    value: dict[str, Any] = {
        "sequence": len(proposal.audit_events) + 1,
        "timestamp": utc_timestamp(),
        "actor": actor,
        "action": action,
        "from_status": proposal.status.value,
        "to_status": to_status.value,
        "result": result,
    }
    if details:
        value["details"] = dict(details)
    return value


def _require_status(proposal: ProposalRecord, allowed: set[ProposalStatus], action: str) -> None:
    if proposal.status not in allowed:
        raise WorkflowStateError(
            f"{action} is not allowed from status {proposal.status.value} for {proposal.change_id}."
        )


def _proposal_powerbi_definition(proposal: ProposalRecord) -> Path:
    if (
        proposal.proposal_source is ProposalSource.POWERBI_IMPORT
        and proposal.source_model_path
    ):
        return resolve_powerbi_model_dir(
            PROJECT_ROOT / proposal.source_model_path,
            PROJECT_ROOT,
        )
    return PBI_DEFINITION_DIR


def approve_change(store: ChangeStore, change_id: str, *, actor: str = "local-user") -> ProposalRecord:
    validate_change_id(change_id)
    actor = validate_actor(actor)

    def transition(proposal: ProposalRecord) -> ProposalRecord:
        _require_status(proposal, {ProposalStatus.PROPOSED}, "Approval")
        if proposal.deployment_state is not DeploymentState.NOT_REQUESTED:
            raise WorkflowStateError("Deployment must remain NOT_REQUESTED.")
        if not proposal.canonical_application_available or not proposal.cross_target_valid:
            raise WorkflowManualReview("Proposal is not eligible for deterministic canonical application.")
        if proposal_is_stale(
            proposal,
            powerbi_definition_dir=_proposal_powerbi_definition(proposal),
        ):
            raise WorkflowStateError("Proposal source snapshot is stale.", code="STALE_PROPOSAL")
        event = _event(proposal, actor=actor, action="APPROVE", to_status=ProposalStatus.APPROVED)
        return replace(
            proposal,
            schema_version=PROPOSAL_SCHEMA_VERSION,
            status=ProposalStatus.APPROVED,
            approval_state=ApprovalState.APPROVED,
            audit_events=proposal.audit_events + (event,),
        )

    return store.update_proposal(change_id, transition)


def reject_change(store: ChangeStore, change_id: str, *, actor: str = "local-user") -> ProposalRecord:
    validate_change_id(change_id)
    actor = validate_actor(actor)

    def transition(proposal: ProposalRecord) -> ProposalRecord:
        _require_status(
            proposal,
            {ProposalStatus.PROPOSED, ProposalStatus.APPROVED, ProposalStatus.NO_OP, ProposalStatus.MANUAL_REVIEW_REQUIRED},
            "Rejection",
        )
        event = _event(proposal, actor=actor, action="REJECT", to_status=ProposalStatus.REJECTED)
        return replace(
            proposal,
            schema_version=PROPOSAL_SCHEMA_VERSION,
            status=ProposalStatus.REJECTED,
            approval_state=ApprovalState.REJECTED,
            canonical_application_available=False,
            audit_events=proposal.audit_events + (event,),
        )

    return store.update_proposal(change_id, transition)


def _run(command: Sequence[str], *, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise WorkflowError(f"Required executable is unavailable: {command[0]}") from exc


def _require_clean_worktree() -> None:
    completed = _run(["git", "status", "--porcelain", "--untracked-files=all"])
    if completed.returncode != 0:
        raise WorkflowStateError("Git worktree status could not be inspected.")
    if completed.stdout.strip():
        raise WorkflowStateError("Canonical apply requires a clean worktree except ignored runtime files.")


def _tree_hash(path: Path) -> str:
    digest_parts: list[bytes] = []
    if path.is_dir():
        for item in sorted(
            (candidate for candidate in path.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(path).as_posix(),
        ):
            digest_parts.extend([item.relative_to(path).as_posix().encode("utf-8"), b"\0", item.read_bytes(), b"\0"])
    return sha256_bytes(b"".join(digest_parts))


def _proposal_snapshot_tree_hash(path: Path) -> str:
    """Match the protected-tree algorithm stored by the M3 proposal engine."""
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


def _managed_files(canonical_path: Path, output_dir: Path) -> tuple[Path, ...]:
    return (canonical_path,) + tuple(output_dir / name for name in CORE_OUTPUT_NAMES)


def _copy_tracked_workspace(destination: Path) -> None:
    listing = _run(["git", "ls-files", "-z"])
    if listing.returncode != 0:
        raise WorkflowStateError("Tracked repository files could not be enumerated safely.")
    destination.mkdir(parents=True, exist_ok=False)
    root = PROJECT_ROOT.resolve()
    for relative in (item for item in listing.stdout.split("\0") if item):
        source = (root / relative).resolve()
        target = (destination / relative).resolve()
        if root not in source.parents or destination.resolve() not in target.parents:
            raise WorkflowStateError("Tracked workspace path escapes its expected root.")
        if not source.is_file():
            raise WorkflowStateError(f"Tracked workspace file is missing: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (destination / ".tmp").mkdir(exist_ok=True)


def _backup_files(
    proposal: ProposalRecord,
    files: Sequence[Path],
    artifact_root: Path,
) -> Mapping[str, Any]:
    backup_dir = artifact_root / proposal.change_id / "pre-apply"
    if backup_dir.exists() or backup_dir.is_symlink():
        raise WorkflowStateError(f"Pre-apply backup already exists: {backup_dir}")
    backup_dir.mkdir(parents=True)
    entries: dict[str, Any] = {}
    for index, path in enumerate(files):
        resolved = path.resolve()
        if not resolved.is_file():
            raise WorkflowStateError(f"Managed apply file is missing: {resolved}")
        relative = resolved.relative_to(PROJECT_ROOT).as_posix()
        backup_path = backup_dir / f"{index:02d}-{resolved.name}"
        shutil.copy2(resolved, backup_path)
        entries[relative] = {
            "backup_path": backup_path.relative_to(PROJECT_ROOT).as_posix(),
            "pre_sha256": sha256_file(resolved),
        }
    manifest = {"directory": backup_dir.relative_to(PROJECT_ROOT).as_posix(), "files": entries}
    atomic_write(backup_dir / "manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return manifest


def _restore_backup(backup: Mapping[str, Any]) -> None:
    files = backup.get("files")
    if not isinstance(files, Mapping):
        raise WorkflowStateError("Proposal has no valid pre-apply backup manifest.")
    for relative, details in files.items():
        if not isinstance(relative, str) or not isinstance(details, Mapping):
            raise WorkflowStateError("Proposal backup manifest is malformed.")
        target = (PROJECT_ROOT / relative).resolve()
        source = (PROJECT_ROOT / str(details.get("backup_path"))).resolve()
        if PROJECT_ROOT.resolve() not in target.parents or PROJECT_ROOT.resolve() not in source.parents:
            raise WorkflowStateError("Proposal backup path escapes the repository.")
        if not source.is_file() or sha256_file(source) != details.get("pre_sha256"):
            raise WorkflowStateError(f"Proposal backup content is missing or corrupt: {relative}")
        atomic_write(target, source.read_bytes())


def _assert_proposed_ir(proposal: ProposalRecord, canonical_path: Path, manifest_path: Path) -> None:
    if proposal.canonical_metric is None or proposal.proposed_ir is None:
        raise WorkflowManualReview("Proposal has no complete proposed IR.")
    index = build_metric_ir_index(
        load_json(manifest_path),
        load_yaml(canonical_path),
        canonical_source=CANONICAL_SOURCE,
        trace_id=proposal.change_id,
    )
    actual = index.get(proposal.canonical_metric)
    if actual is None or semantic_ir_to_dict(actual) != dict(proposal.proposed_ir):
        raise WorkflowManualReview("Freshly parsed canonical IR does not equal the approved proposed IR.")


def _preflight_candidate_ir(proposal: ProposalRecord, candidate: bytes, store: ChangeStore) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".candidate-ir-", dir=store.root) as temporary:
        workspace = Path(temporary) / "workspace"
        _copy_tracked_workspace(workspace)
        candidate_path = workspace / DBT_SEMANTIC_YAML.relative_to(PROJECT_ROOT)
        atomic_write(candidate_path, candidate)
        parsed = _run(["dbt", "--no-version-check", "parse"], cwd=workspace)
        if parsed.returncode != 0:
            raise WorkflowManualReview(
                "Candidate canonical YAML did not compile in the isolated pre-apply workspace: "
                + parsed.stderr.strip()
            )
        _assert_proposed_ir(
            proposal,
            candidate_path,
            workspace / DBT_SEMANTIC_MANIFEST.relative_to(PROJECT_ROOT),
        )


def build_powerbi_operation_model(proposal: ProposalRecord) -> dict[str, Any]:
    if proposal.canonical_metric is None or proposal.proposed_ir is None:
        raise WorkflowManualReview("Proposal has no mapped canonical IR for Power BI operations.")
    current = {} if proposal.current_ir is None else dict(proposal.current_ir)
    proposed = dict(proposal.proposed_ir)
    mapping = dict(proposed.get("power_bi") or {})
    table = mapping.get("table")
    measure = mapping.get("measure")
    actual_measure: Mapping[str, Any] = {}
    if table and measure and PBI_DEFINITION_DIR.is_dir():
        actual_measure = (
            parse_tmdl_definition(PBI_DEFINITION_DIR)
            .get("tables", {})
            .get(table, {})
            .get("measures", {})
            .get(measure, {})
        )
    metadata: list[dict[str, Any]] = []
    definitions: list[dict[str, Any]] = []
    operation_kind = proposal.operation.get("kind")
    if operation_kind == "CREATE_METRIC" and not actual_measure:
        raise WorkflowManualReview(
            "Typed canonical creation requires the imported Power BI measure to exist exactly in the source copy."
        )
    common = {
        "change_id": proposal.change_id,
        "canonical_metric": proposal.canonical_metric,
        "canonical_source": proposal.canonical_file,
        "table": table,
        "measure": measure,
        "approval_state": proposal.approval_state.value,
        "validation_state": proposal.validation_state.value,
    }
    if table and measure and operation_kind == "CREATE_METRIC":
        if actual_measure.get("description", "") != proposed.get("description", ""):
            metadata.append({
                **common,
                "operation": "set_measure_description",
                "current": actual_measure.get("description") or "",
                "proposed": proposed.get("description") or "",
            })
        if actual_measure.get("format_string") != mapping.get("format_string"):
            metadata.append({
                **common,
                "operation": "set_measure_format",
                "current": actual_measure.get("format_string"),
                "proposed": mapping.get("format_string"),
            })
    if table and measure and operation_kind == "SET_DESCRIPTION" and current.get("description") != proposed.get("description"):
        metadata.append({
            **common,
            "operation": "set_measure_description",
            "current": actual_measure.get("description") or "",
            "proposed": proposed.get("description") or "",
        })
    if table and measure and operation_kind == "SET_FORMAT":
        old_mapping = dict(current.get("power_bi") or {})
        if old_mapping.get("format_string") != mapping.get("format_string"):
            metadata.append({
                **common,
                "operation": "set_measure_format",
                "current": actual_measure.get("format_string"),
                "proposed": mapping.get("format_string"),
            })
    effective_current_dax = (
        actual_measure.get("expression") if operation_kind == "CREATE_METRIC" else proposal.current_dax
    )
    if table and measure and effective_current_dax != proposal.proposed_dax:
        if proposal.target_support.get("POWER_BI") != "SUPPORTED_PATTERN":
            raise WorkflowManualReview("Unsupported Power BI definition cannot become applicable.")
        definitions.append({
            **common,
            "operation": "set_measure_expression",
            "current": effective_current_dax,
            "proposed": proposal.proposed_dax,
            "previous_dax": effective_current_dax,
            "proposed_dax": proposal.proposed_dax,
            "semantic_pattern": proposed.get("pattern"),
            "generator_version": "semantic-ir-v1",
            "support": "SUPPORTED_PATTERN",
        })
    return {
        "change_id": proposal.change_id,
        "canonical_source": proposal.canonical_file,
        "metadata_operations": metadata,
        "definition_operations": definitions,
    }


def _write_operation_model(proposal: ProposalRecord, output_path: Path) -> None:
    model = build_powerbi_operation_model(proposal)
    atomic_write(output_path, (json.dumps(model, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def apply_change(
    store: ChangeStore,
    change_id: str,
    *,
    canonical_path: Path = DBT_SEMANTIC_YAML,
    manifest_path: Path = DBT_SEMANTIC_MANIFEST,
    output_dir: Path = OUTPUT_DIR,
    artifact_root: Path | None = None,
    require_clean: bool = True,
) -> ProposalRecord:
    validate_change_id(change_id)
    proposal = store.load_proposal(change_id)
    _require_status(proposal, {ProposalStatus.APPROVED}, "Canonical apply")
    if proposal.approval_state is not ApprovalState.APPROVED:
        raise WorkflowStateError("Canonical apply requires explicit approval.")
    if proposal.deployment_state is not DeploymentState.NOT_REQUESTED:
        raise WorkflowStateError("Deployment must remain NOT_REQUESTED.")
    if proposal_is_stale(
        proposal,
        semantic_yaml_path=canonical_path,
        manifest_path=manifest_path,
        powerbi_definition_dir=_proposal_powerbi_definition(proposal),
        output_dir=output_dir,
    ):
        raise WorkflowStateError("Approved proposal source snapshot is stale.", code="STALE_PROPOSAL")
    if not proposal.canonical_patch or not proposal.canonical_application_available:
        raise WorkflowManualReview("Proposal has no lossless applicable canonical patch.")
    if require_clean:
        _require_clean_worktree()
    source = canonical_path.read_bytes()
    candidate = render_candidate(source, proposal.canonical_patch)
    yaml.safe_load(candidate)
    _preflight_candidate_ir(proposal, candidate, store)
    files = _managed_files(canonical_path, output_dir)
    artifacts = artifact_root or (store.root / "artifacts")
    backup = _backup_files(proposal, files, artifacts)
    planned = {
        "canonical_pre_sha256": sha256_bytes(source),
        "canonical_candidate_sha256": sha256_bytes(candidate),
    }

    def plan(current: ProposalRecord) -> ProposalRecord:
        _require_status(current, {ProposalStatus.APPROVED}, "Canonical apply planning")
        event = _event(
            current,
            actor="semantic-agent",
            action="APPLY_PLAN",
            to_status=ProposalStatus.APPROVED,
            details=planned,
        )
        return replace(current, planned_hashes=planned, backup=backup, audit_events=current.audit_events + (event,))

    proposal = store.update_proposal(change_id, plan)
    wrote = False
    regeneration_dir = artifacts / proposal.change_id / "regeneration-candidate"
    try:
        atomic_write(canonical_path, candidate)
        wrote = True
        parsed = _run(["dbt", "--no-version-check", "parse"])
        if parsed.returncode != 0:
            raise WorkflowError("dbt parse failed after canonical application: " + parsed.stderr.strip())
        _assert_proposed_ir(proposal, canonical_path, manifest_path)
        if regeneration_dir.exists():
            shutil.rmtree(regeneration_dir)
        generated = _run(
            [
                sys.executable,
                "semantic_poc/run_poc.py",
                "--skip-dbt-parse",
                "--output-dir",
                str(regeneration_dir),
            ]
        )
        if generated.returncode != 0:
            raise WorkflowError("Deterministic target regeneration failed: " + generated.stderr.strip())
        _write_operation_model(proposal, regeneration_dir / POWERBI_PATCH_OUTPUT.name)
        modified_files = [canonical_path]
        for name in CORE_OUTPUT_NAMES:
            generated_path = regeneration_dir / name
            destination = output_dir / name
            if not generated_path.is_file():
                raise WorkflowError(f"Deterministic regeneration omitted required artifact: {name}")
            generated_bytes = generated_path.read_bytes()
            if destination.read_bytes() != generated_bytes:
                atomic_write(destination, generated_bytes)
                modified_files.append(destination)
        applied_hashes = {
            path.resolve().relative_to(PROJECT_ROOT).as_posix(): sha256_file(path.resolve())
            for path in modified_files
        }

        def complete(current: ProposalRecord) -> ProposalRecord:
            event = _event(
                current,
                actor="semantic-agent",
                action="APPLY_LOCAL",
                to_status=ProposalStatus.APPLIED_LOCAL,
                details={"applied_hashes": applied_hashes},
            )
            return replace(
                current,
                status=ProposalStatus.APPLIED_LOCAL,
                local_application_state=LocalApplicationState.APPLIED,
                applied_hashes=applied_hashes,
                audit_events=current.audit_events + (event,),
            )

        return store.update_proposal(change_id, complete)
    except Exception as exc:
        if wrote:
            _restore_backup(backup)
            _run(["dbt", "--no-version-check", "parse"])

        def fail(current: ProposalRecord) -> ProposalRecord:
            event = _event(
                current,
                actor="semantic-agent",
                action="APPLY_LOCAL",
                to_status=ProposalStatus.FAILED,
                result="FAILED",
                details={"error": str(exc), "restored": wrote},
            )
            return replace(
                current,
                status=ProposalStatus.FAILED,
                validation_state=ValidationState.FAILED,
                audit_events=current.audit_events + (event,),
            )

        try:
            store.update_proposal(change_id, fail)
        except ChangeStoreError:
            pass
        if isinstance(exc, WorkflowError):
            raise
        if isinstance(exc, CanonicalPatchError):
            raise WorkflowManualReview(str(exc)) from exc
        raise WorkflowError(str(exc)) from exc
    finally:
        if regeneration_dir.is_dir():
            shutil.rmtree(regeneration_dir)


def _require_ignored_output(output_dir: Path) -> Path:
    resolved = output_dir.resolve()
    root = PROJECT_ROOT.resolve()
    if root not in resolved.parents:
        raise WorkflowInputError("Power BI output directory must be inside the repository.")
    if resolved.exists():
        raise WorkflowStateError(f"Power BI output directory already exists: {resolved}")
    if resolved == PBI_DEFINITION_DIR.resolve() or PBI_DEFINITION_DIR.resolve() in resolved.parents:
        raise WorkflowInputError("Power BI output must not overlap the source definition.")
    if resolved == DEFAULT_CHANGE_DIR.resolve() or DEFAULT_CHANGE_DIR.resolve() in resolved.parents:
        raise WorkflowInputError("Power BI output must be outside the change-store backup root.")
    relative = resolved.relative_to(root).as_posix()
    ignored = _run(["git", "check-ignore", "-q", "--", relative])
    if ignored.returncode != 0:
        raise WorkflowInputError("Power BI output directory must be covered by .gitignore.")
    return resolved


def apply_powerbi_copy_change(
    store: ChangeStore,
    change_id: str,
    *,
    output_dir: Path,
    allow_metadata: bool,
    allow_supported_definitions: bool,
    source_dir: Path = PBI_DEFINITION_DIR,
) -> ProposalRecord:
    proposal = store.load_proposal(change_id)
    _require_status(proposal, {ProposalStatus.APPLIED_LOCAL}, "Power BI copied-definition apply")
    destination = _require_ignored_output(output_dir)
    source_hash = _tree_hash(source_dir)
    model = build_powerbi_operation_model(proposal)
    artifact_dir = store.root / "artifacts" / proposal.change_id / "powerbi-copy"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    patch_path = artifact_dir / "powerbi-operations.json"
    atomic_write(patch_path, (json.dumps(model, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    result = apply_powerbi_patch(
        definition_dir=source_dir,
        patch_path=patch_path,
        output_dir=destination,
        allow_metadata=allow_metadata,
        allow_supported_definitions=allow_supported_definitions,
    )
    if not result.success:
        raise WorkflowStateError("Copied Power BI application failed: " + "; ".join(result.failures))
    if _tree_hash(source_dir) != source_hash:
        raise WorkflowStateError("Source Power BI definition changed during copied application.")
    copy_record = {
        "output_dir": destination.relative_to(PROJECT_ROOT).as_posix(),
        "source_tree_sha256": source_hash,
        "output_tree_sha256": _tree_hash(destination),
        "metadata_allowed": allow_metadata,
        "supported_definitions_allowed": allow_supported_definitions,
        "applied": list(result.applied),
        "protected_checks": list(result.protected_checks),
    }

    def record(current: ProposalRecord) -> ProposalRecord:
        event = _event(
            current,
            actor="semantic-agent",
            action="APPLY_POWERBI_COPY",
            to_status=ProposalStatus.APPLIED_LOCAL,
            details=copy_record,
        )
        return replace(current, powerbi_copy=copy_record, audit_events=current.audit_events + (event,))

    return store.update_proposal(change_id, record)


def _prepare_validation_test_workspace(
    proposal: ProposalRecord,
    workspace: Path,
) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    _copy_tracked_workspace(workspace)
    workspace_changes = workspace / "semantic_poc" / "changes"
    workspace_changes.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT_ROOT / "semantic_poc" / "changes" / ".gitkeep", workspace_changes / ".gitkeep")
    backup_files = proposal.backup.get("files")
    if not isinstance(backup_files, Mapping):
        raise WorkflowStateError("Validation test workspace requires the pre-apply backup.")
    for relative, details in backup_files.items():
        source = PROJECT_ROOT / str(details["backup_path"])
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _validation_commands(
    proposal: ProposalRecord,
    validation_output: Path,
    test_workspace: Path,
) -> list[tuple[str, list[str], bool, Path]]:
    copy_dir = proposal.powerbi_copy.get("output_dir")
    if not isinstance(copy_dir, str):
        raise WorkflowStateError("Validation requires an approved copied Power BI definition.")
    pbi = str(PROJECT_ROOT / copy_dir)
    output = str(validation_output)
    return [
        ("dbt_parse", ["dbt", "--no-version-check", "parse"], False, PROJECT_ROOT),
        (
            "baseline_fixture_dbt_parse",
            ["dbt", "--no-version-check", "parse"],
            False,
            test_workspace,
        ),
        (
            "focused_tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "semantic_poc/tests/test_agent_m4.py",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(test_workspace / ".tmp" / "pytest-focused"),
            ],
            False,
            test_workspace,
        ),
        (
            "full_tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "semantic_poc/tests",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(test_workspace / ".tmp" / "pytest-full"),
            ],
            False,
            test_workspace,
        ),
        (
            "quality_checks",
            [
                sys.executable,
                "semantic_poc/run_quality_checks.py",
                "--powerbi-definition-dir",
                pbi,
                "--output-dir",
                output,
                "--skip-tests",
            ],
            False,
            PROJECT_ROOT,
        ),
        (
            "strict_validation",
            [
                sys.executable,
                "semantic_poc/run_poc.py",
                "--strict",
                "--powerbi-definition-dir",
                pbi,
                "--output-dir",
                output,
            ],
            True,
            PROJECT_ROOT,
        ),
    ]


def validate_change(
    store: ChangeStore,
    change_id: str,
    *,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
) -> ProposalRecord:
    proposal = store.load_proposal(change_id)
    _require_status(proposal, {ProposalStatus.APPLIED_LOCAL}, "Validation")
    if _proposal_snapshot_tree_hash(PBI_DEFINITION_DIR) != proposal.source_snapshot.get(
        "power_bi_definition_tree_sha256"
    ):
        raise WorkflowStateError("Source Power BI definition is not byte-for-byte unchanged.")
    for relative, expected in proposal.applied_hashes.items():
        path = (PROJECT_ROOT / relative).resolve()
        if not path.is_file() or sha256_file(path) != expected:
            raise WorkflowStateError(f"Validation refused because an applied file changed: {relative}")
    copy_dir_value = proposal.powerbi_copy.get("output_dir")
    copy_hash_value = proposal.powerbi_copy.get("output_tree_sha256")
    if not isinstance(copy_dir_value, str) or _tree_hash(PROJECT_ROOT / copy_dir_value) != copy_hash_value:
        raise WorkflowStateError("Copied Power BI definition changed after controlled application.")
    _assert_proposed_ir(proposal, DBT_SEMANTIC_YAML, DBT_SEMANTIC_MANIFEST)
    protected_file_hashes: dict[str, str] = {}
    backup_files = proposal.backup.get("files")
    if not isinstance(backup_files, Mapping):
        raise WorkflowStateError("Validation requires a complete pre-apply backup manifest.")
    for relative, details in backup_files.items():
        expected = proposal.applied_hashes.get(relative, details.get("pre_sha256"))
        path = PROJECT_ROOT / relative
        actual = sha256_file(path)
        if actual != expected:
            raise WorkflowStateError(f"Validation refused because a protected file changed: {relative}")
        protected_file_hashes[relative] = actual
    validation_output = store.root / "artifacts" / proposal.change_id / "validation-output"
    if validation_output.exists():
        shutil.rmtree(validation_output)
    validation_output.mkdir(parents=True)
    test_workspace = store.root / "artifacts" / proposal.change_id / "validation-test-workspace"
    _prepare_validation_test_workspace(proposal, test_workspace)
    runner = command_runner or (lambda command: _run(command))
    results: dict[str, Any] = {
        "protected_state": {
            "accepted": True,
            "approved_ir_equal": True,
            "deployment_state": proposal.deployment_state.value,
            "protected_file_sha256": protected_file_hashes,
            "source_powerbi_snapshot_sha256": proposal.source_snapshot.get(
                "power_bi_definition_tree_sha256"
            ),
            "source_powerbi_copy_check_sha256": proposal.powerbi_copy.get("source_tree_sha256"),
            "copied_powerbi_sha256": proposal.powerbi_copy.get("output_tree_sha256"),
            "copied_definition_scope": list(proposal.powerbi_copy.get("protected_checks", ())),
        }
    }
    failed: list[str] = []
    hygiene = _run(["git", "status", "--porcelain", "--untracked-files=all"])
    allowed_paths = set(proposal.applied_hashes)
    changed_paths = {
        line[3:].strip().replace("\\", "/")
        for line in hygiene.stdout.splitlines()
        if len(line) >= 4
    }
    hygiene_accepted = hygiene.returncode == 0 and changed_paths <= allowed_paths
    results["repository_hygiene"] = {
        "command": ["git", "status", "--porcelain", "--untracked-files=all"],
        "exit_code": hygiene.returncode,
        "accepted": hygiene_accepted,
        "changed_paths": sorted(changed_paths),
        "allowed_paths": sorted(allowed_paths),
    }
    if not hygiene_accepted:
        failed.append("repository_hygiene")
    for name, command, allow_known_strict, cwd in _validation_commands(
        proposal,
        validation_output,
        test_workspace,
    ):
        completed = runner(command) if command_runner is not None else _run(command, cwd=cwd)
        accepted = completed.returncode == 0
        if allow_known_strict and completed.returncode == 1:
            compatibility = validation_output / "semantic_compatibility.md"
            content = compatibility.read_text(encoding="utf-8") if compatibility.is_file() else ""
            accepted = (
                KNOWN_RELATIONSHIP_FINDING in content
                and content.count(" is missing in Power BI.") == 1
                and "Power BI definition drift exists" not in completed.stderr
            )
        results[name] = {
            "command": list(command),
            "exit_code": completed.returncode,
            "accepted": accepted,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
        if not accepted:
            failed.append(name)
    if failed:
        def fail(current: ProposalRecord) -> ProposalRecord:
            event = _event(
                current,
                actor="semantic-agent",
                action="VALIDATE",
                to_status=ProposalStatus.FAILED,
                result="FAILED",
                details={"failed_gates": failed},
            )
            return replace(
                current,
                status=ProposalStatus.FAILED,
                validation_state=ValidationState.FAILED,
                validation_results=results,
                audit_events=current.audit_events + (event,),
            )
        store.update_proposal(change_id, fail)
        raise WorkflowStateError("Mandatory validation failed: " + ", ".join(failed))

    def pass_validation(current: ProposalRecord) -> ProposalRecord:
        event = _event(
            current,
            actor="semantic-agent",
            action="VALIDATE",
            to_status=ProposalStatus.VALIDATED,
            details={"gates": list(results)},
        )
        return replace(
            current,
            status=ProposalStatus.VALIDATED,
            validation_state=ValidationState.PASSED,
            validation_results=results,
            audit_events=current.audit_events + (event,),
            authority_state=(
                "CANONICAL_CONTRACT_ACCEPTED"
                if current.proposal_source is ProposalSource.POWERBI_IMPORT
                else current.authority_state
            ),
        )

    return store.update_proposal(change_id, pass_validation)


def rollback_change(store: ChangeStore, change_id: str) -> ProposalRecord:
    proposal = store.load_proposal(change_id)
    _require_status(
        proposal,
        {ProposalStatus.APPLIED_LOCAL, ProposalStatus.VALIDATED, ProposalStatus.FAILED},
        "Rollback",
    )
    if not proposal.backup or not proposal.applied_hashes:
        raise WorkflowStateError("Proposal has no eligible local-application backup.")
    for relative, expected in proposal.applied_hashes.items():
        path = (PROJECT_ROOT / relative).resolve()
        if not path.is_file() or sha256_file(path) != expected:
            raise WorkflowStateError(f"Rollback refused because an applied file changed: {relative}")
    _restore_backup(proposal.backup)
    parsed = _run(["dbt", "--no-version-check", "parse"])
    if parsed.returncode != 0:
        raise WorkflowError("dbt parse failed after rollback: " + parsed.stderr.strip())
    for relative, details in proposal.backup["files"].items():
        if sha256_file(PROJECT_ROOT / relative) != details["pre_sha256"]:
            raise WorkflowError(f"Rollback hash verification failed: {relative}")

    def complete(current: ProposalRecord) -> ProposalRecord:
        event = _event(
            current,
            actor="semantic-agent",
            action="ROLLBACK",
            to_status=ProposalStatus.ROLLED_BACK,
            details={"restored_files": sorted(current.backup.get("files", {}))},
        )
        return replace(
            current,
            status=ProposalStatus.ROLLED_BACK,
            local_application_state=LocalApplicationState.ROLLED_BACK,
            audit_events=current.audit_events + (event,),
            authority_state=(
                "CANONICALIZATION_PROPOSED"
                if current.proposal_source is ProposalSource.POWERBI_IMPORT
                else current.authority_state
            ),
        )

    return store.update_proposal(change_id, complete)
