from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import yaml

from semantic_poc.demo import (
    DemoError,
    create_demo_bundle,
    finalize_demo_bundle,
    persist_demo_failure,
    render_demo_error,
)
from semantic_poc.canonical_drift import CanonicalDriftError, check_canonical_drift
from semantic_poc.src.models import DBT_SEMANTIC_MANIFEST, DBT_SEMANTIC_YAML, PBI_DEFINITION_DIR, PROJECT_ROOT, STATUS_MANUAL_REVIEW_REQUIRED

from .change_store import (
    DEFAULT_CHANGE_DIR,
    ChangeAlreadyExistsError,
    ChangeNotFoundError,
    ChangeProtectedError,
    ChangeStore,
    ChangeStoreError,
)
from .conversion_review import (
    ConversionStore,
    ConversionStoreError,
    accept_mapping,
    correct_mapping,
    create_conversion_review,
    finalize_conversion,
    reject_mapping,
)
from .inspection import MetricAmbiguousError, MetricInspectionError, MetricNotFoundError, inspect_metric
from .import_store import (
    DEFAULT_IMPORT_DIR,
    ImportAlreadyExistsError,
    ImportNotFoundError,
    ImportProtectedError,
    ImportStore,
    ImportStoreError,
)
from .import_workflow import (
    create_import_proposal_batch,
    create_import_run,
    find_import_proposal,
    verify_import_source,
)
from .powerbi_import import (
    ImportSupportClassification,
    MappingValidationError,
    PowerBIImportError,
    PowerBIPathError,
    resolve_powerbi_model_dir,
)
from .powerbi_snowflake_audit import (
    AuditInputError,
    AuditStateError,
    AuditStaleEvidenceError,
    audit_powerbi_snowflake,
    write_audit_output_directory,
)
from .preview_sync import PreviewSyncError, preview_sync
from .review_recording import (
    ReviewRecordingError,
    ReviewStateError,
    record_review,
    suggest_review,
)
from .proposal_engine import ProposalEngineError, ProposalInputError, proposal_is_stale, propose_change
from .proposal_models import ProposalRecord, ProposalSource, ProposalStatus
from .schemas import MetricChangeRequest
from .workflow import (
    WorkflowError,
    WorkflowInputError,
    WorkflowManualReview,
    WorkflowStateError,
    apply_change,
    apply_powerbi_copy_change,
    approve_change,
    reject_change,
    rollback_change,
    validate_actor,
    validate_change,
)


EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_INPUT = 2
EXIT_MANUAL_REVIEW = 3
EXIT_STATE = 4


class SemanticAgentParser(argparse.ArgumentParser):
    def parse_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        parsed = super().parse_args(args, namespace)
        if getattr(parsed, "command", None) == "preview-sync":
            existing = getattr(parsed, "existing_snowflake_yaml", None)
            if parsed.target_mode == "create" and existing is not None:
                self.error(
                    "preview-sync --target-mode create forbids "
                    "--existing-snowflake-yaml"
                )
            if parsed.target_mode == "update" and existing is None:
                self.error(
                    "preview-sync --target-mode update requires "
                    "--existing-snowflake-yaml"
                )
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = SemanticAgentParser(
        prog="semantic-agent",
        description="Controlled semantic contract workflow.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a canonical or exactly mapped metric name.")
    inspect_parser.add_argument("metric", help="Canonical dbt metric, Power BI measure, or Snowflake metric name.")
    inspect_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")
    inspect_parser.add_argument("--semantic-yaml", default=str(DBT_SEMANTIC_YAML), help="Canonical semantic YAML path.")
    inspect_parser.add_argument("--manifest", default=str(DBT_SEMANTIC_MANIFEST), help="Compiled dbt semantic manifest path.")
    inspect_parser.add_argument(
        "--powerbi-definition-dir",
        default=str(PBI_DEFINITION_DIR),
        help="Power BI SemanticModel definition directory.",
    )

    propose_parser = subparsers.add_parser("propose", help="Create a source-read-only structured proposal.")
    propose_input = propose_parser.add_mutually_exclusive_group(required=True)
    propose_input.add_argument("--request", help="Path to a schema-version-2 JSON request.")
    propose_input.add_argument("--text", help="Natural-language request interpreted through the optional OpenAI integration.")
    propose_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    show_parser = subparsers.add_parser("show", help="Show a persisted local proposal.")
    show_parser.add_argument("change_id")
    show_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    list_parser = subparsers.add_parser("list", help="List persisted local proposals.")
    list_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    discard_parser = subparsers.add_parser("discard", help="Discard an unapplied local proposal.")
    discard_parser.add_argument("change_id")
    discard_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    approve_parser = subparsers.add_parser("approve", help="Explicitly approve a current proposal.")
    approve_parser.add_argument("change_id")
    approve_parser.add_argument("--actor", default="local-user", help="Safe local audit actor identifier.")
    approve_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    reject_parser = subparsers.add_parser("reject", help="Reject a proposal while preserving its audit record.")
    reject_parser.add_argument("change_id")
    reject_parser.add_argument("--actor", default="local-user", help="Safe local audit actor identifier.")
    reject_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    apply_parser = subparsers.add_parser("apply", help="Apply one approved canonical proposal locally.")
    apply_parser.add_argument("change_id")
    apply_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    copy_parser = subparsers.add_parser(
        "apply-powerbi-copy",
        help="Apply approved metadata or supported DAX only to a fresh copied definition.",
    )
    copy_parser.add_argument("change_id")
    copy_parser.add_argument("--output-dir", required=True)
    copy_parser.add_argument("--allow-metadata", action="store_true")
    copy_parser.add_argument("--allow-supported-definitions", action="store_true")
    copy_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    validate_parser = subparsers.add_parser("validate", help="Run all mandatory local validation gates.")
    validate_parser.add_argument("change_id")
    validate_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    rollback_parser = subparsers.add_parser("rollback", help="Safely restore a recorded local application.")
    rollback_parser.add_argument("change_id")
    rollback_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    import_parser = subparsers.add_parser(
        "import-powerbi", help="Create a read-only Power BI PBIP/TMDL discovery inventory."
    )
    import_input = import_parser.add_mutually_exclusive_group(required=True)
    import_input.add_argument("--model-dir", help="Repository-local .pbip, .SemanticModel, or definition path.")
    import_input.add_argument("--text", help="Natural-language request selecting an allowlisted committed model.")
    import_parser.add_argument("--mapping-file", help="Optional repository-local schema-v1 exact mapping file.")
    import_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    show_import_parser = subparsers.add_parser("show-import", help="Show one immutable Power BI import run.")
    show_import_parser.add_argument("import_id")
    show_import_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    list_imports_parser = subparsers.add_parser("list-imports", help="List local Power BI import runs.")
    list_imports_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    propose_import_parser = subparsers.add_parser(
        "propose-import", help="Create deterministic canonicalization proposals for an import run."
    )
    propose_import_parser.add_argument("import_id")
    propose_import_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    discard_import_parser = subparsers.add_parser(
        "discard-import", help="Discard an unapplied import while retaining audit evidence."
    )
    discard_import_parser.add_argument("import_id")
    discard_import_parser.add_argument("--actor", default="local-user", help="Safe local audit actor identifier.")
    discard_import_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    explain_import_parser = subparsers.add_parser(
        "explain-import",
        help="Interpret a natural-language question against one immutable import run.",
    )
    explain_import_parser.add_argument("import_id")
    explain_import_parser.add_argument("--text", required=True)
    explain_import_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    review_conversion_parser = subparsers.add_parser(
        "review-conversion",
        help="Create deterministic conversion comparison and semantic-lint evidence.",
    )
    review_conversion_parser.add_argument("import_id")
    review_conversion_parser.add_argument(
        "--autopilot-yaml",
        help="Optional repository-local YAML exported from a Snowflake semantic view.",
    )
    review_conversion_parser.add_argument(
        "--result-evidence",
        help="Optional repository-local hash-bound golden result evidence JSON.",
    )
    review_conversion_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    accept_mapping_parser = subparsers.add_parser(
        "accept-mapping", help="Explicitly accept one exact canonical conversion mapping."
    )
    accept_mapping_parser.add_argument("finding_id")
    accept_mapping_parser.add_argument("--actor", default="local-user")
    accept_mapping_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    correct_mapping_parser = subparsers.add_parser(
        "correct-mapping", help="Resolve one finding through a structured canonical mapping."
    )
    correct_mapping_parser.add_argument("finding_id")
    correct_mapping_parser.add_argument("--mapping-file", required=True)
    correct_mapping_parser.add_argument("--actor", default="local-user")
    correct_mapping_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    reject_mapping_parser = subparsers.add_parser(
        "reject-mapping", help="Explicitly reject and exclude one generated mapping."
    )
    reject_mapping_parser.add_argument("finding_id")
    reject_mapping_parser.add_argument("--rationale", required=True)
    reject_mapping_parser.add_argument("--actor", default="local-user")
    reject_mapping_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    finalize_conversion_parser = subparsers.add_parser(
        "finalize-conversion",
        help="Finalize a fully reviewed conversion into ignored canonical-generated Snowflake YAML.",
    )
    finalize_conversion_parser.add_argument("import_id")
    finalize_conversion_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    audit_parser = subparsers.add_parser(
        "audit-powerbi-snowflake",
        help="Create a read-only, hash-bound Power BI-to-Snowflake conversion audit.",
    )
    audit_parser.add_argument(
        "--model-dir",
        required=True,
        help="Repository-local .pbip, .SemanticModel, or definition path.",
    )
    audit_parser.add_argument(
        "--snowflake-yaml",
        required=True,
        help="Repository-local exported Snowflake semantic-view YAML.",
    )
    audit_parser.add_argument(
        "--benchmark-spec",
        help="Optional repository-local measure oracle YAML.",
    )
    audit_parser.add_argument(
        "--snowflake-diagnostics",
        help="Optional repository-local sanitized, hash-bound diagnostics JSON or YAML.",
    )
    audit_parser.add_argument(
        "--result-evidence",
        help="Optional repository-local hash-bound differential result JSON.",
    )
    audit_parser.add_argument(
        "--output-dir",
        required=True,
        help="New or controlled repository-local audit output directory.",
    )
    audit_parser.add_argument(
        "--check",
        action="store_true",
        help="Verify output artifacts byte-for-byte without writing.",
    )
    audit_parser.add_argument(
        "--json", action="store_true", help="Write structured JSON output."
    )

    preview_parser = subparsers.add_parser(
        "preview-sync",
        help="Create or check a deterministic, source-read-only cross-target preview.",
    )
    preview_parser.add_argument("change_id")
    preview_parser.add_argument(
        "--target-mode",
        required=True,
        choices=("create", "update"),
    )
    preview_parser.add_argument("--existing-snowflake-yaml")
    preview_parser.add_argument("--result-evidence")
    preview_parser.add_argument("--output-dir", required=True)
    preview_parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate twice and verify the existing bundle byte-for-byte.",
    )
    preview_parser.add_argument(
        "--json", action="store_true", help="Write structured JSON output."
    )

    suggest_review_parser = subparsers.add_parser(
        "suggest-review",
        help="Read a current preview and suggest exact accepted review guidance.",
    )
    suggest_review_parser.add_argument("--preview-dir", required=True)
    suggest_review_parser.add_argument("--finding-id")
    suggest_review_parser.add_argument(
        "--json", action="store_true", help="Write structured JSON output."
    )

    record_review_parser = subparsers.add_parser(
        "record-review",
        help="Record one structured human answer without approving or applying it.",
    )
    record_review_parser.add_argument("--preview-dir", required=True)
    record_review_parser.add_argument("--decision-file", required=True)
    record_review_parser.add_argument("--output-dir", required=True)
    record_review_parser.add_argument(
        "--json", action="store_true", help="Write structured JSON output."
    )

    guide_parser = subparsers.add_parser(
        "guide-conversion",
        help="Run the resumable, proposal-only guided conversion workflow.",
    )
    guide_parser.add_argument("--model-dir", required=True, help="Repository-local PBIP or SemanticModel path.")
    guide_parser.add_argument("--session", help="Optional safe local session identifier.")
    guide_parser.add_argument("--resume", action="store_true", help="Resume a current bound session.")
    guide_parser.add_argument(
        "--answer-ref",
        help="With --resume, confirm one exact answer offered by the pending deterministic finding.",
    )
    guide_parser.add_argument("--provider", choices=("openai", "scripted"), default="openai")
    guide_parser.add_argument("--model", help="Optional safe OpenAI model override; defaults to SEMANTIC_AGENT_MODEL or gpt-5.6-sol.")
    guide_parser.add_argument("--json-events", action="store_true", help="Emit sanitized JSONL events.")

    demo_parser = subparsers.add_parser(
        "demo", help="Create a deterministic offline semantic-assurance demonstration bundle."
    )
    demo_input = demo_parser.add_mutually_exclusive_group(required=True)
    demo_input.add_argument("--project", help="Repository-local .pbip or .SemanticModel path.")
    demo_input.add_argument(
        "--fixture",
        nargs="?",
        const="semantic-trap",
        choices=("semantic-trap",),
        help="Use the committed semantic-trap fixture (default when the flag has no value).",
    )
    demo_parser.add_argument("--snowflake-yaml", help="Optional repository-local Snowflake YAML evidence.")
    demo_parser.add_argument("--output-dir", required=True, help="New repository-local output directory.")
    demo_parser.add_argument("--check", action="store_true", help="Verify repeatability and committed fixture expectations.")
    demo_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    demo_finalize_parser = subparsers.add_parser(
        "demo-finalize", help="Create reviewed candidate outputs without applying or deploying them."
    )
    demo_finalize_parser.add_argument("--demo-run", required=True, help="Completed semantic-trap demo bundle.")
    demo_finalize_parser.add_argument("--decisions", required=True, help="Hash-bound structured review decisions YAML.")
    demo_finalize_parser.add_argument("--output-dir", required=True, help="New repository-local candidate output directory.")
    demo_finalize_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")

    drift_parser = subparsers.add_parser(
        "check-canonical-drift",
        help="Compare two canonical dbt YAML snapshots and their deterministic target candidates.",
    )
    drift_parser.add_argument("--baseline", required=True, help="Accepted repository-local canonical YAML snapshot.")
    drift_parser.add_argument("--current", required=True, help="Current repository-local canonical YAML candidate.")
    drift_parser.add_argument("--json", action="store_true", help="Write structured JSON output.")
    return parser


def _store() -> ChangeStore:
    override = os.getenv("SEMANTIC_AGENT_CHANGE_DIR")
    if not override:
        return ChangeStore(DEFAULT_CHANGE_DIR)
    configured = Path(override)
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    resolved = configured.resolve()
    runtime_root = (PROJECT_ROOT / ".tmp").resolve()
    try:
        resolved.relative_to(runtime_root)
    except ValueError as exc:
        raise ChangeStoreError("SEMANTIC_AGENT_CHANGE_DIR must be below the ignored repository .tmp directory.") from exc
    return ChangeStore(resolved)


def _import_store() -> ImportStore:
    return ImportStore(DEFAULT_IMPORT_DIR)


def _conversion_store() -> ConversionStore:
    return ConversionStore(DEFAULT_IMPORT_DIR)


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))


def _print_inspection_human(data: dict[str, Any]) -> None:
    canonical = data["canonical"]
    power_bi = data["mappings"]["power_bi"]
    snowflake = data["mappings"]["snowflake"]
    print(f"Canonical metric: {canonical['name']}")
    print(f"Canonical source: {canonical['file']}")
    print(f"Resolved from: {data['resolved_from']}")
    print(f"Metric type: {canonical.get('type')}")
    print(f"Translation pattern: {data.get('translation_pattern')}")
    print(f"Power BI mapping: {power_bi.get('table')}.{power_bi.get('measure')}")
    print(f"Power BI target exists: {power_bi.get('exists')}")
    print(f"Snowflake mapping: {snowflake.get('logical_table')}.{snowflake.get('metric_name')}")
    print(f"Snowflake generated: {snowflake.get('generated')}")
    print(f"Compatibility status: {data.get('compatibility_status')}")
    if power_bi.get("actual_dax"):
        print(f"Actual DAX: {power_bi['actual_dax']}")
    for diagnostic in data.get("diagnostics", []):
        print(f"Diagnostic: {diagnostic}")


def _print_error(
    code: str,
    message: str,
    *,
    as_json: bool,
    candidates: Sequence[str] = (),
    requested_metric: str | None = None,
) -> None:
    if as_json:
        error: dict[str, Any] = {"error": {"code": code, "message": message}}
        if requested_metric is not None:
            error["error"]["requested_metric"] = requested_metric
        if candidates:
            error["error"]["candidates"] = list(candidates)
        _print_json(error)
    else:
        print(f"{code}: {message}", file=sys.stderr)
        if candidates:
            print("Candidates: " + ", ".join(candidates), file=sys.stderr)


def _print_proposal_human(
    proposal: ProposalRecord,
    *,
    stale: bool | None = None,
    include_details: bool = False,
) -> None:
    print(f"Change ID: {proposal.change_id}")
    print(f"Status: {proposal.status.value}")
    print(f"Canonical metric: {proposal.canonical_metric or 'UNRESOLVED'}")
    print(f"Canonical source: {proposal.canonical_file}")
    print(f"Operation: {proposal.operation.get('kind')}")
    print(f"Risk: {proposal.risk_level.value}")
    if stale is not None:
        print(f"Stale source snapshot: {str(stale).lower()}")
    if include_details:
        details = proposal.to_dict()
        print("Current IR:")
        print(json.dumps(details["current_ir"], indent=2, sort_keys=True, ensure_ascii=False))
        print("Proposed IR:")
        print(json.dumps(details["proposed_ir"], indent=2, sort_keys=True, ensure_ascii=False))
        print(f"Current DAX: {proposal.current_dax}")
        print(f"Proposed DAX: {proposal.proposed_dax}")
        print("Current Snowflake definition:")
        print(json.dumps(details["current_snowflake"], indent=2, sort_keys=True, ensure_ascii=False))
        print("Proposed Snowflake definition:")
        print(json.dumps(details["proposed_snowflake"], indent=2, sort_keys=True, ensure_ascii=False))
        for target, support in sorted(proposal.target_support.items()):
            print(f"Target support {target}: {support}")
    if proposal.canonical_diff:
        print("Canonical diff:")
        print(proposal.canonical_diff, end="")
    if proposal.dax_diff:
        print("Power BI DAX diff:")
        print(proposal.dax_diff, end="")
    if proposal.snowflake_diff:
        print("Snowflake diff:")
        print(proposal.snowflake_diff, end="")
    for assumption in proposal.assumptions:
        print(f"Assumption: {assumption}")
    for diagnostic in proposal.diagnostics:
        print(f"Diagnostic {diagnostic.code}: {diagnostic.message}")
    print("Required validation:")
    for command in proposal.required_validation:
        print(f"  {command}")


def run_inspect(args: argparse.Namespace) -> int:
    try:
        result = inspect_metric(
            args.metric,
            semantic_yaml_path=Path(args.semantic_yaml),
            manifest_path=Path(args.manifest),
            powerbi_definition_dir=Path(args.powerbi_definition_dir),
        )
    except MetricNotFoundError as exc:
        _print_error(
            exc.code,
            str(exc),
            as_json=args.json,
            candidates=exc.candidates,
            requested_metric=exc.requested_metric,
        )
        return EXIT_INPUT
    except (MetricAmbiguousError, MetricInspectionError) as exc:
        _print_error(
            exc.code,
            str(exc),
            as_json=args.json,
            candidates=exc.candidates,
            requested_metric=exc.requested_metric,
        )
        return EXIT_MANUAL_REVIEW
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        _print_error(
            "INSPECTION_FAILED",
            f"Inspection input could not be read or parsed: {exc}",
            as_json=args.json,
            requested_metric=args.metric,
        )
        return EXIT_MANUAL_REVIEW
    if args.json:
        _print_json(result)
    else:
        _print_inspection_human(result)
    return EXIT_MANUAL_REVIEW if result["compatibility_status"] == STATUS_MANUAL_REVIEW_REQUIRED else EXIT_OK


def _load_request(path: Path) -> MetricChangeRequest:
    if not path.is_file():
        raise ProposalInputError(f"Request JSON does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProposalInputError(f"Request JSON could not be read or parsed: {exc}") from exc
    try:
        return MetricChangeRequest.from_dict(data)
    except (TypeError, ValueError) as exc:
        raise ProposalInputError(str(exc)) from exc


def _propose_structured_request(request: MetricChangeRequest) -> ProposalRecord:
    if DBT_SEMANTIC_MANIFEST.is_file():
        return propose_change(request)
    runtime_root = PROJECT_ROOT / ".tmp" / "proposal-manifests"
    runtime_root.parent.mkdir(parents=True, exist_ok=True)
    if runtime_root.parent.is_symlink():
        raise ProposalInputError("Repository runtime directory must not be a symbolic link.")
    runtime_root.mkdir(parents=True, exist_ok=True)
    try:
        canonical = yaml.safe_load(DBT_SEMANTIC_YAML.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProposalInputError(f"Canonical semantic YAML could not be read: {exc}") from exc
    if not isinstance(canonical, dict):
        raise ProposalInputError("Canonical semantic YAML must contain an object.")
    manifest = {
        "semantic_models": canonical.get("semantic_models", []),
        "metrics": canonical.get("metrics", []),
    }
    with tempfile.TemporaryDirectory(prefix="structured-proposal-", dir=runtime_root) as temporary:
        manifest_path = Path(temporary) / "semantic_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return propose_change(request, manifest_path=manifest_path)


def run_propose(args: argparse.Namespace) -> int:
    try:
        if args.text is not None:
            try:
                from .natural_language import AgentManualReview, propose_from_text
                from .openai_provider import AgentConfigurationError
            except ImportError:
                _print_error(
                    "SEMANTIC_AGENT_CONFIGURATION_ERROR",
                    'Natural-language support requires the optional agent dependencies; install with python -m pip install -e ".[agent]".',
                    as_json=args.json,
                )
                return EXIT_INPUT
            try:
                proposal = propose_from_text(args.text)
            except AgentConfigurationError as exc:
                _print_error(exc.code, str(exc), as_json=args.json)
                return EXIT_INPUT
            except AgentManualReview as exc:
                _print_error(exc.code, str(exc), as_json=args.json)
                return EXIT_MANUAL_REVIEW
        else:
            request = _load_request(Path(args.request))
            proposal = _propose_structured_request(request)
        path = _store().save_proposal(proposal)
    except ProposalInputError as exc:
        _print_error(exc.code, str(exc), as_json=args.json)
        return EXIT_INPUT
    except ChangeAlreadyExistsError as exc:
        _print_error("PROPOSAL_ALREADY_EXISTS", str(exc), as_json=args.json)
        return EXIT_STATE
    except (ProposalEngineError, ChangeStoreError, OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        _print_error("PROPOSAL_FAILED", str(exc), as_json=args.json)
        return EXIT_UNEXPECTED
    output = proposal.to_dict()
    output["record_path"] = str(path)
    if args.json:
        _print_json(output)
    else:
        _print_proposal_human(proposal)
        print(f"Local record: {path}")
    return EXIT_MANUAL_REVIEW if proposal.status is ProposalStatus.MANUAL_REVIEW_REQUIRED else EXIT_OK


def run_show(args: argparse.Namespace) -> int:
    try:
        promoted = True
        try:
            proposal = _store().load_proposal(args.change_id)
            import_run = (
                _import_store().load_run(proposal.import_run_id)
                if proposal.proposal_source is ProposalSource.POWERBI_IMPORT and proposal.import_run_id
                else None
            )
        except ChangeNotFoundError:
            imported = find_import_proposal(_import_store(), args.change_id)
            if imported is None:
                raise
            import_run, proposal = imported
            promoted = False
        stale = proposal_is_stale(proposal) or (
            import_run is not None and not verify_import_source(import_run)
        )
    except (ValueError, ChangeNotFoundError, ImportNotFoundError) as exc:
        _print_error("PROPOSAL_NOT_FOUND", str(exc), as_json=args.json)
        return EXIT_INPUT
    except (
        ChangeStoreError,
        ImportStoreError,
        ProposalEngineError,
        OSError,
        KeyError,
        TypeError,
        yaml.YAMLError,
    ) as exc:
        _print_error("PROPOSAL_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    if args.json:
        output = proposal.to_dict()
        output["stale"] = stale
        output["promoted_to_change_store"] = promoted
        _print_json(output)
    else:
        _print_proposal_human(proposal, stale=stale, include_details=True)
        if proposal.proposal_source is ProposalSource.POWERBI_IMPORT:
            print(f"Promoted to normal proposal store: {str(promoted).lower()}")
    return EXIT_STATE if stale else EXIT_OK


def run_list(args: argparse.Namespace) -> int:
    try:
        promoted_proposals = _store().list_proposals()
        promoted_ids = {proposal.change_id for proposal in promoted_proposals}
        proposals_with_origin = [(proposal, True) for proposal in promoted_proposals]
        for run in _import_store().list_runs():
            batch = _import_store().try_load_proposal_batch(run.import_id)
            if batch is None:
                continue
            proposals_with_origin.extend(
                (ProposalRecord.from_dict(raw), False)
                for raw in batch.proposals
                if raw.get("change_id") not in promoted_ids
            )
        proposals_with_origin.sort(key=lambda item: (item[0].created_at, item[0].change_id))
    except (ChangeStoreError, ImportStoreError, ValueError) as exc:
        _print_error("PROPOSAL_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    summaries = [
        {
            "change_id": proposal.change_id,
            "created_at": proposal.created_at,
            "canonical_metric": proposal.canonical_metric,
            "intent": proposal.intent,
            "operation": proposal.operation.get("kind"),
            "risk_level": proposal.risk_level.value,
            "status": proposal.status.value,
            "proposal_source": proposal.proposal_source.value,
            "import_run_id": proposal.import_run_id,
            "promoted_to_change_store": promoted,
        }
        for proposal, promoted in proposals_with_origin
    ]
    if args.json:
        _print_json({"proposals": summaries})
    elif not summaries:
        print("No local proposals.")
    else:
        for item in summaries:
            print(
                f"{item['change_id']}  {item['status']}  {item['canonical_metric'] or 'UNRESOLVED'}  {item['operation']}"
            )
    return EXIT_OK


def run_discard(args: argparse.Namespace) -> int:
    try:
        proposal = _store().discard_proposal(args.change_id)
    except (ValueError, ChangeNotFoundError) as exc:
        _print_error("PROPOSAL_NOT_FOUND", str(exc), as_json=args.json)
        return EXIT_INPUT
    except ChangeProtectedError as exc:
        _print_error("PROPOSAL_PROTECTED", str(exc), as_json=args.json)
        return EXIT_STATE
    except ChangeStoreError as exc:
        _print_error("PROPOSAL_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    output = {"change_id": args.change_id, "discarded": True, "status": proposal.status.value}
    if args.json:
        _print_json(output)
    else:
        print(f"Discarded local proposal: {args.change_id}")
    return EXIT_OK


def _classification_counts(run: Any) -> dict[str, int]:
    counts = Counter(str(item["classification"]) for item in run.classifications)
    return dict(sorted(counts.items()))


def _inventory_counts(run: Any) -> dict[str, int]:
    inventory = run.inventory
    dependency_graph = inventory.get("dependency_graph") or {}
    return {
        "columns": len(inventory.get("columns", ())),
        "dependency_edges": len(dependency_graph.get("edges", ())),
        "measures": len(inventory.get("measures", ())),
        "partitions": len(inventory.get("partitions", ())),
        "relationships": len(inventory.get("relationships", ())),
        "tables": len(inventory.get("tables", ())),
    }


def _object_classification_counts(run: Any) -> dict[str, int]:
    records = run.inventory.get("object_support_records", ())
    counts = Counter(str(item["classification"]) for item in records)
    return {
        classification.value: counts[classification.value]
        for classification in ImportSupportClassification
    }


def _effective_import_children(
    import_store: ImportStore,
    change_store: ChangeStore,
    run: Any,
) -> dict[str, Any]:
    batch = import_store.try_load_proposal_batch(run.import_id)
    children: list[dict[str, Any]] = []
    if batch is not None:
        for raw in batch.proposals:
            promoted = True
            try:
                proposal = change_store.load_proposal(str(raw["change_id"]))
            except ChangeNotFoundError:
                proposal = ProposalRecord.from_dict(raw)
                promoted = False
            children.append(
                {
                    "change_id": proposal.change_id,
                    "canonical_metric": proposal.canonical_metric,
                    "status": proposal.status.value,
                    "authority_state": proposal.authority_state,
                    "promoted_to_change_store": promoted,
                }
            )
    status_counts = Counter(str(item["status"]) for item in children)
    authority_counts = Counter(
        str(item["authority_state"])
        for item in children
        if item["authority_state"] is not None
    )
    accepted = authority_counts["CANONICAL_CONTRACT_ACCEPTED"]
    return {
        "batch": batch,
        "children": children,
        "child_status_counts": dict(sorted(status_counts.items())),
        "child_authority_counts": dict(sorted(authority_counts.items())),
        "accepted_child_count": accepted,
    }


def _import_summary(
    store: ImportStore,
    change_store: ChangeStore,
    run: Any,
) -> dict[str, Any]:
    effective = _effective_import_children(store, change_store, run)
    batch = effective["batch"]
    tombstone = store.try_load_discard(run.import_id)
    authority = (
        "CANONICAL_CONTRACT_ACCEPTED"
        if effective["accepted_child_count"]
        else batch.authority_state.value
        if batch is not None
        else run.authority_state.value
    )
    return {
        "import_id": run.import_id,
        "created_at": run.created_at,
        "authority_state": authority,
        "discard_state": "DISCARDED" if tombstone is not None else "ACTIVE",
        "source_model_id": run.source_model_id,
        "source_model_path": run.source_model_path,
        "source_snapshot_hash": run.source_snapshot_hash,
        "semantic_content_hash": run.semantic_content_hash,
        "source_stale": not verify_import_source(run),
        "inventory_counts": _inventory_counts(run),
        "classification_counts": _classification_counts(run),
        "object_classification_counts": _object_classification_counts(run),
        "proposal_count": len(batch.proposals) if batch is not None else 0,
        "blocked_child_count": len(batch.blocked_child_ids) if batch is not None else 0,
        "manual_review_count": len(batch.manual_review_items) if batch is not None else 0,
        "unsupported_count": len(batch.unsupported_items) if batch is not None else 0,
        "child_status_counts": effective["child_status_counts"],
        "child_authority_counts": effective["child_authority_counts"],
        "accepted_child_count": effective["accepted_child_count"],
    }


def run_import_powerbi(args: argparse.Namespace) -> int:
    try:
        store = _import_store()
        if args.text is not None:
            try:
                from .natural_language import AgentManualReview, import_powerbi_from_text
                from .openai_provider import AgentConfigurationError
            except ImportError:
                _print_error(
                    "SEMANTIC_AGENT_CONFIGURATION_ERROR",
                    'Natural-language support requires the optional agent dependencies; install with python -m pip install -e ".[agent]".',
                    as_json=args.json,
                )
                return EXIT_INPUT
            try:
                run = import_powerbi_from_text(
                    args.text,
                    store=store,
                    mapping_file=args.mapping_file,
                )
            except AgentConfigurationError as exc:
                _print_error(exc.code, str(exc), as_json=args.json)
                return EXIT_INPUT
            except AgentManualReview as exc:
                _print_error(exc.code, str(exc), as_json=args.json)
                return EXIT_MANUAL_REVIEW
        else:
            run = create_import_run(
                args.model_dir,
                store=store,
                mapping_file=args.mapping_file,
            )
        output = run.to_dict()
        output["record_path"] = str(store.run_path(run.import_id))
        output["inventory_counts"] = _inventory_counts(run)
        output["classification_counts"] = _classification_counts(run)
        output["object_classification_counts"] = _object_classification_counts(run)
        output["child_status_counts"] = {}
        output["child_authority_counts"] = {}
        output["accepted_child_count"] = 0
    except (PowerBIPathError, MappingValidationError, ImportNotFoundError, ValueError) as exc:
        _print_error("INVALID_POWERBI_IMPORT_INPUT", str(exc), as_json=args.json)
        return EXIT_INPUT
    except ImportAlreadyExistsError as exc:
        _print_error("IMPORT_ALREADY_EXISTS", str(exc), as_json=args.json)
        return EXIT_STATE
    except PowerBIImportError as exc:
        _print_error("POWERBI_IMPORT_MANUAL_REVIEW", str(exc), as_json=args.json)
        return EXIT_MANUAL_REVIEW
    except (ImportStoreError, OSError, KeyError, TypeError, RuntimeError, yaml.YAMLError) as exc:
        _print_error("POWERBI_IMPORT_FAILED", str(exc), as_json=args.json)
        return EXIT_UNEXPECTED
    if args.json:
        _print_json(output)
    else:
        print(f"Import ID: {run.import_id}")
        print(f"Source model: {run.source_model_path}")
        for name, count in output["inventory_counts"].items():
            print(f"Inventory {name}: {count}")
        for name, count in output["classification_counts"].items():
            print(f"Classification {name}: {count}")
        print(f"Local record: {output['record_path']}")
    return EXIT_OK


def run_show_import(args: argparse.Namespace) -> int:
    try:
        store = _import_store()
        change_store = _store()
        run = store.load_run(args.import_id)
        effective = _effective_import_children(store, change_store, run)
        batch = effective["batch"]
        tombstone = store.try_load_discard(args.import_id)
        output = run.to_dict()
        output["effective_authority_state"] = (
            "CANONICAL_CONTRACT_ACCEPTED"
            if effective["accepted_child_count"]
            else batch.authority_state.value
            if batch is not None
            else run.authority_state.value
        )
        output["effective_discard_state"] = "DISCARDED" if tombstone is not None else "ACTIVE"
        output["source_stale"] = not verify_import_source(run)
        output["inventory_counts"] = _inventory_counts(run)
        output["classification_counts"] = _classification_counts(run)
        output["object_classification_counts"] = _object_classification_counts(run)
        output["proposal_batch"] = batch.to_dict() if batch is not None else None
        output["discard_tombstone"] = tombstone.to_dict() if tombstone is not None else None
        output["effective_children"] = effective["children"]
        output["child_status_counts"] = effective["child_status_counts"]
        output["child_authority_counts"] = effective["child_authority_counts"]
        output["accepted_child_count"] = effective["accepted_child_count"]
    except (ValueError, ImportNotFoundError) as exc:
        _print_error("IMPORT_NOT_FOUND", str(exc), as_json=args.json)
        return EXIT_INPUT
    except (ImportStoreError, ChangeStoreError, OSError) as exc:
        _print_error("IMPORT_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    if args.json:
        _print_json(output)
    else:
        print(f"Import ID: {run.import_id}")
        print(f"Source model: {run.source_model_path}")
        print(f"Authority: {output['effective_authority_state']}")
        print(f"Discard state: {output['effective_discard_state']}")
        print(f"Stale source snapshot: {str(output['source_stale']).lower()}")
        print(f"Proposal children: {len(batch.proposals) if batch is not None else 0}")
    return EXIT_STATE if output["source_stale"] else EXIT_OK


def run_list_imports(args: argparse.Namespace) -> int:
    try:
        store = _import_store()
        change_store = _store()
        summaries = [
            _import_summary(store, change_store, run) for run in store.list_runs()
        ]
    except (ImportStoreError, ChangeStoreError, OSError, ValueError) as exc:
        _print_error("IMPORT_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    if args.json:
        _print_json({"imports": summaries})
    elif not summaries:
        print("No local Power BI imports.")
    else:
        for item in summaries:
            print(
                f"{item['import_id']}  {item['discard_state']}  "
                f"{item['authority_state']}  {item['source_model_path']}"
            )
    return EXIT_OK


def run_propose_import(args: argparse.Namespace) -> int:
    try:
        batch = create_import_proposal_batch(
            args.import_id,
            store=_import_store(),
            change_store=_store(),
        )
    except ImportNotFoundError as exc:
        _print_error("IMPORT_NOT_FOUND", str(exc), as_json=args.json)
        return EXIT_INPUT
    except ImportAlreadyExistsError as exc:
        _print_error("IMPORT_PROPOSALS_ALREADY_EXIST", str(exc), as_json=args.json)
        return EXIT_STATE
    except ValueError as exc:
        code = "IMPORT_STATE_ERROR" if any(
            word in str(exc).casefold() for word in ("discarded", "already exists", "snapshot changed")
        ) else "INVALID_IMPORT_ID"
        _print_error(code, str(exc), as_json=args.json)
        return EXIT_STATE if code == "IMPORT_STATE_ERROR" else EXIT_INPUT
    except (ImportProtectedError, ImportStoreError, ProposalEngineError, OSError, KeyError, TypeError, RuntimeError) as exc:
        _print_error("IMPORT_PROPOSAL_FAILED", str(exc), as_json=args.json)
        return EXIT_STATE
    output = batch.to_dict()
    output["proposal_count"] = len(batch.proposals)
    output["manual_review_count"] = len(batch.manual_review_items)
    output["unsupported_count"] = len(batch.unsupported_items)
    output["blocked_child_count"] = len(batch.blocked_child_ids)
    output["m4_applicable_count"] = sum(
        bool(item.get("canonical_application_available")) for item in batch.proposals
    )
    run = _import_store().load_run(batch.import_id)
    output["object_classification_counts"] = _object_classification_counts(run)
    output["child_status_counts"] = dict(
        sorted(Counter(str(item["status"]) for item in batch.proposals).items())
    )
    output["child_authority_counts"] = dict(
        sorted(
            Counter(
                str(item["authority_state"])
                for item in batch.proposals
                if item.get("authority_state") is not None
            ).items()
        )
    )
    output["accepted_child_count"] = 0
    if args.json:
        _print_json(output)
    else:
        print(f"Import ID: {batch.import_id}")
        print(f"Proposal children: {output['proposal_count']}")
        print(f"M4-applicable children: {output['m4_applicable_count']}")
        print(f"Draft-only manual review items: {output['manual_review_count']}")
        print(f"Unsupported items: {output['unsupported_count']}")
        print(f"Blocked preview children: {output['blocked_child_count']}")
    return EXIT_MANUAL_REVIEW if batch.manual_review_items or batch.unsupported_items else EXIT_OK


def run_discard_import(args: argparse.Namespace) -> int:
    try:
        validate_actor(args.actor)
        import_store = _import_store()
        change_store = _store()
        batch = import_store.try_load_proposal_batch(args.import_id)
        promoted_states = []
        if batch is not None:
            for change_id in batch.child_change_ids:
                try:
                    promoted_states.append(change_store.load_proposal(change_id))
                except ChangeNotFoundError:
                    pass
        tombstone = import_store.discard_run(
            args.import_id,
            actor=args.actor,
            proposal_states=promoted_states,
        )
    except (ValueError, ImportNotFoundError) as exc:
        _print_error("IMPORT_NOT_FOUND", str(exc), as_json=args.json)
        return EXIT_INPUT
    except ImportAlreadyExistsError as exc:
        _print_error("IMPORT_ALREADY_DISCARDED", str(exc), as_json=args.json)
        return EXIT_STATE
    except (ImportProtectedError, ChangeProtectedError) as exc:
        _print_error("IMPORT_PROTECTED", str(exc), as_json=args.json)
        return EXIT_STATE
    except (ImportStoreError, ChangeStoreError, OSError) as exc:
        _print_error("IMPORT_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    if args.json:
        _print_json(tombstone.to_dict())
    else:
        print(f"Discarded import with retained audit evidence: {args.import_id}")
    return EXIT_OK


def run_explain_import(args: argparse.Namespace) -> int:
    try:
        try:
            from .natural_language import AgentManualReview, review_import_from_text
            from .openai_provider import AgentConfigurationError
        except ImportError:
            _print_error(
                "SEMANTIC_AGENT_CONFIGURATION_ERROR",
                'Natural-language support requires the optional agent dependencies; install with python -m pip install -e ".[agent]".',
                as_json=args.json,
            )
            return EXIT_INPUT
        try:
            output = review_import_from_text(
                args.import_id,
                args.text,
                store=_import_store(),
            )
        except AgentConfigurationError as exc:
            _print_error(exc.code, str(exc), as_json=args.json)
            return EXIT_INPUT
        except AgentManualReview as exc:
            _print_error(exc.code, str(exc), as_json=args.json)
            return EXIT_MANUAL_REVIEW
    except (ImportNotFoundError, ValueError) as exc:
        _print_error("IMPORT_REVIEW_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    except (ImportStoreError, OSError, KeyError, TypeError, RuntimeError) as exc:
        _print_error("IMPORT_REVIEW_FAILED", str(exc), as_json=args.json)
        return EXIT_UNEXPECTED
    if args.json:
        _print_json(output)
    elif output["review_action"] in {"LIST_SUPPORTED_EXACT", "LIST_MANUAL_REVIEW"}:
        print(f"Import ID: {output['import_id']}")
        print(f"Review action: {output['review_action']}")
        for item in output["measures"]:
            print(
                f"{item['classification']}  {item['source_table']}[{item['source_measure']}]  "
                f"{item['canonical_metric'] or 'UNRESOLVED'}"
            )
    elif output["review_action"] == "EXPLAIN_RELATIONSHIP":
        finding = output["relationship_finding"]
        print(f"{finding['code']}: {finding['message']}")
    else:
        print(f"Import ID: {output['import_id']}")
        print(f"Review action: {output['review_action']}")
        if "proposal_count" in output:
            print(f"Proposals: {output['proposal_count']}")
        if "measure_count" in output:
            print(f"Measures: {output['measure_count']}")
    return EXIT_MANUAL_REVIEW if output.get("manual_review_count", 0) else EXIT_OK


def run_review_conversion(args: argparse.Namespace) -> int:
    try:
        review = create_conversion_review(
            args.import_id,
            autopilot_yaml=args.autopilot_yaml,
            result_evidence=args.result_evidence,
            import_store=_import_store(),
            conversion_store=_conversion_store(),
        )
    except (ValueError, ImportNotFoundError, MappingValidationError) as exc:
        _print_error("CONVERSION_REVIEW_INPUT_ERROR", str(exc), as_json=args.json)
        return EXIT_INPUT
    except (ImportStoreError, ConversionStoreError, OSError, yaml.YAMLError) as exc:
        _print_error("CONVERSION_REVIEW_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    if args.json:
        _print_json(review.to_dict())
    else:
        print(f"Import ID: {review.import_id}")
        print(f"Findings: {len(review.findings)}")
        print(f"Blocking findings: {len(review.unresolved_blockers)}")
        print(f"Autopilot evidence: {review.autopilot_status}")
        print(f"Golden result evidence: {review.result_evidence_status}")
    return EXIT_MANUAL_REVIEW if review.unresolved_blockers else EXIT_OK


def _run_mapping_decision(args: argparse.Namespace, operation: Any) -> int:
    try:
        decision = operation()
    except (ValueError, ImportNotFoundError, ChangeNotFoundError) as exc:
        _print_error("CONVERSION_DECISION_INPUT_ERROR", str(exc), as_json=args.json)
        return EXIT_INPUT
    except (ImportStoreError, ConversionStoreError, ChangeStoreError, OSError) as exc:
        _print_error("CONVERSION_DECISION_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    if args.json:
        _print_json(decision.to_dict())
    else:
        print(
            f"{decision.action.value}  {decision.finding_id}  "
            f"{decision.canonical_metric or 'EXCLUDED'}"
        )
    return EXIT_OK


def run_accept_mapping(args: argparse.Namespace) -> int:
    return _run_mapping_decision(
        args,
        lambda: accept_mapping(
            args.finding_id,
            actor=args.actor,
            conversion_store=_conversion_store(),
        ),
    )


def run_correct_mapping(args: argparse.Namespace) -> int:
    return _run_mapping_decision(
        args,
        lambda: correct_mapping(
            args.finding_id,
            mapping_file=args.mapping_file,
            actor=args.actor,
            conversion_store=_conversion_store(),
            change_store=_store(),
        ),
    )


def run_reject_mapping(args: argparse.Namespace) -> int:
    return _run_mapping_decision(
        args,
        lambda: reject_mapping(
            args.finding_id,
            rationale=args.rationale,
            actor=args.actor,
            conversion_store=_conversion_store(),
        ),
    )


def run_finalize_conversion(args: argparse.Namespace) -> int:
    try:
        review = finalize_conversion(
            args.import_id,
            import_store=_import_store(),
            conversion_store=_conversion_store(),
        )
    except ValueError as exc:
        _print_error("CONVERSION_FINALIZATION_BLOCKED", str(exc), as_json=args.json)
        return EXIT_MANUAL_REVIEW
    except ImportNotFoundError as exc:
        _print_error("CONVERSION_REVIEW_NOT_FOUND", str(exc), as_json=args.json)
        return EXIT_INPUT
    except (ImportStoreError, ConversionStoreError, ChangeStoreError, OSError) as exc:
        _print_error("CONVERSION_FINALIZATION_STATE_ERROR", str(exc), as_json=args.json)
        return EXIT_STATE
    if args.json:
        _print_json(review.to_dict())
    else:
        print(f"Finalized conversion: {review.import_id}")
        print(f"Accepted Snowflake SHA-256: {review.accepted_output_sha256}")
        print("Deployment: NOT_PERFORMED")
    return EXIT_OK


def run_audit_powerbi_snowflake(args: argparse.Namespace) -> int:
    try:
        audit = audit_powerbi_snowflake(
            model_dir=args.model_dir,
            snowflake_yaml=args.snowflake_yaml,
            benchmark_spec=args.benchmark_spec,
            snowflake_diagnostics=args.snowflake_diagnostics,
            result_evidence=args.result_evidence,
            repository_root=PROJECT_ROOT,
        )
        artifacts = write_audit_output_directory(
            audit,
            output_dir=args.output_dir,
            repository_root=PROJECT_ROOT,
            check=args.check,
        )
    except AuditStaleEvidenceError as exc:
        _print_error("AUDIT_EVIDENCE_STALE", str(exc), as_json=args.json)
        return EXIT_STATE
    except AuditStateError as exc:
        _print_error("AUDIT_STATE_CONFLICT", str(exc), as_json=args.json)
        return EXIT_STATE
    except AuditInputError as exc:
        _print_error("AUDIT_INPUT_INVALID", str(exc), as_json=args.json)
        return EXIT_INPUT
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as exc:
        _print_error("AUDIT_FAILED", str(exc), as_json=args.json)
        return EXIT_UNEXPECTED
    result = {
        "audit_id": audit.audit_id,
        "audit_kind": "RECONCILE_TARGET_DRIFT",
        "summary": dict(audit.summary),
        "artifacts": artifacts,
        "check": bool(args.check),
    }
    if args.json:
        _print_json(result)
    else:
        print(f"Audit ID: {audit.audit_id}")
        print(
            f"Coverage: {audit.summary['matched_measure_count']}/"
            f"{audit.summary['source_measure_count']} measures emitted"
        )
        print(f"Verdict: {audit.summary['executive_verdict']}")
        print("Artifacts:")
        for path in artifacts.values():
            print(f"  {path}")
    return EXIT_MANUAL_REVIEW if audit.has_blockers else EXIT_OK


def run_preview_sync(args: argparse.Namespace) -> int:
    try:
        result = preview_sync(
            args.change_id,
            target_mode=args.target_mode,
            existing_snowflake_yaml=args.existing_snowflake_yaml,
            result_evidence=args.result_evidence,
            output_dir=args.output_dir,
            check=args.check,
            change_store=_store(),
            import_store=_import_store(),
        )
    except PreviewSyncError as exc:
        _print_error(exc.code, str(exc), as_json=args.json)
        return exc.exit_code
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as exc:
        _print_error("PREVIEW_SYNC_FAILED", str(exc), as_json=args.json)
        return EXIT_UNEXPECTED
    payload = result.to_dict()
    if args.json:
        _print_json(payload)
    else:
        print(f"Preview ID: {result.preview_id}")
        print(f"Status: {result.status}")
        print(f"Output: {result.output_dir}")
        print(f"Artifacts: {result.artifact_count}")
        print("Approval: NOT_PERFORMED")
        print("Application: NOT_PERFORMED")
        print("Deployment: NOT_PERFORMED")
    return result.exit_code


def run_suggest_review(args: argparse.Namespace) -> int:
    try:
        result = suggest_review(
            args.preview_dir,
            finding_id=args.finding_id,
        )
    except ReviewRecordingError as exc:
        _print_error(exc.code, str(exc), as_json=args.json)
        return exc.exit_code
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as exc:
        _print_error("REVIEW_SUGGESTION_FAILED", str(exc), as_json=args.json)
        return ReviewStateError.exit_code
    payload = result.to_dict()
    if args.json:
        _print_json(payload)
    else:
        print(f"Preview ID: {result.preview_id}")
        for item in result.evaluations:
            print(
                f"{item['finding_id']}: {item['match_status']} "
                f"({item['reason_code']})"
            )
        print("Human confirmation: REQUIRED")
        print("Approval: NOT_REQUESTED")
        print("Application: NOT_REQUESTED")
    return result.exit_code


def run_record_review(args: argparse.Namespace) -> int:
    try:
        result = record_review(
            args.preview_dir,
            decision_file=args.decision_file,
            output_dir=args.output_dir,
        )
    except ReviewRecordingError as exc:
        _print_error(exc.code, str(exc), as_json=args.json)
        return exc.exit_code
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as exc:
        _print_error("REVIEW_RECORDING_FAILED", str(exc), as_json=args.json)
        return ReviewStateError.exit_code
    payload = result.to_dict()
    if args.json:
        _print_json(payload)
    else:
        print(f"Decision ID: {result.decision_id}")
        print(f"Finding ID: {result.finding_id}")
        print(f"Status: {result.status}")
        print(f"Output: {result.output_dir}")
        print("Approval: NOT_REQUESTED")
        print("Application: NOT_REQUESTED")
        print("Deployment authorized: false")
    return result.exit_code


def run_demo(args: argparse.Namespace) -> int:
    try:
        result = create_demo_bundle(
            project=args.project,
            fixture=args.fixture,
            snowflake_yaml=args.snowflake_yaml,
            output_dir=args.output_dir,
            check=args.check,
        )
    except DemoError as exc:
        persist_demo_failure(exc, args.output_dir)
        if args.json:
            _print_json({"verdict": "POC_DEMO_NOT_ACCEPTED", "error": exc.to_dict()})
        else:
            print("POC_DEMO_NOT_ACCEPTED", file=sys.stderr)
            print(render_demo_error(exc), file=sys.stderr)
        if any(token in exc.code for token in ("MISMATCH", "NOT_DETERMINISTIC", "SOURCE_CHANGED")):
            return EXIT_UNEXPECTED
        return EXIT_INPUT
    if args.json:
        _print_json(result)
    else:
        print(f"Demo output: {result['output_dir']}")
        print(f"Semantic status: {result['semantic_status']}")
        print(f"Deployment: {result['deployment_status']}")
        if result.get("verdict"):
            print(result["verdict"])
    if args.check and result.get("verdict") == "POC_DEMO_ACCEPTED":
        return EXIT_OK
    return EXIT_MANUAL_REVIEW if result["semantic_status"] == "BLOCKED_PENDING_REVIEW" else EXIT_OK


def run_demo_finalize(args: argparse.Namespace) -> int:
    try:
        result = finalize_demo_bundle(
            demo_run=args.demo_run,
            decisions=args.decisions,
            output_dir=args.output_dir,
        )
    except DemoError as exc:
        _print_error(exc.code, str(exc), as_json=args.json)
        return EXIT_INPUT
    if args.json:
        _print_json(result)
    else:
        print(f"Finalization candidate: {result['output_dir']}")
        print(f"Status: {result['status']}")
        print("Deployment: NOT_REQUESTED")
    return EXIT_OK


def run_check_canonical_drift(args: argparse.Namespace) -> int:
    try:
        result = check_canonical_drift(args.baseline, args.current)
    except CanonicalDriftError as exc:
        _print_error("CANONICAL_DRIFT_INPUT_INVALID", str(exc), as_json=args.json)
        return EXIT_INPUT
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        _print_error("CANONICAL_DRIFT_CHECK_FAILED", str(exc), as_json=args.json)
        return EXIT_UNEXPECTED
    if args.json:
        _print_json(result)
    else:
        print(f"Canonical changes:             {result['canonical_changes']}")
        print(f"Expected Power BI changes:     {result['expected_power_bi_changes']}")
        print(f"Expected Snowflake changes:    {result['expected_snowflake_changes']}")
        print(f"Unexpected target-only drift:  {result['unexpected_target_only_drift']}")
        print(f"Unrelated object changes:      {result['unrelated_object_changes']}")
        print(f"Synchronization status:        {result['synchronization_status']}")
    return EXIT_OK if result["synchronization_status"] == "ALIGNED" else EXIT_MANUAL_REVIEW


def _run_workflow(args: argparse.Namespace, operation: Any) -> int:
    try:
        proposal = operation()
    except (ValueError, ChangeNotFoundError, WorkflowInputError) as exc:
        code = exc.code if isinstance(exc, WorkflowError) else "PROPOSAL_NOT_FOUND"
        _print_error(code, str(exc), as_json=args.json)
        return EXIT_INPUT
    except WorkflowManualReview as exc:
        _print_error(exc.code, str(exc), as_json=args.json)
        return EXIT_MANUAL_REVIEW
    except (WorkflowStateError, ChangeProtectedError) as exc:
        code = exc.code if isinstance(exc, WorkflowError) else "PROPOSAL_PROTECTED"
        _print_error(code, str(exc), as_json=args.json)
        return EXIT_STATE
    except (WorkflowError, ChangeStoreError, ImportStoreError, OSError, yaml.YAMLError) as exc:
        code = exc.code if isinstance(exc, WorkflowError) else "WORKFLOW_FAILED"
        _print_error(code, str(exc), as_json=args.json)
        return EXIT_UNEXPECTED
    if args.json:
        _print_json(proposal.to_dict())
    else:
        _print_proposal_human(proposal, include_details=False)
    return EXIT_OK


def _promote_import_child(
    change_store: ChangeStore,
    change_id: str,
    *,
    require_applicable: bool,
) -> ProposalRecord:
    try:
        return change_store.load_proposal(change_id)
    except ChangeNotFoundError:
        import_store = _import_store()
        imported = find_import_proposal(import_store, change_id)
        if imported is None:
            raise
        run, proposal = imported
        if import_store.try_load_discard(run.import_id) is not None:
            raise WorkflowStateError("Discarded import proposals cannot be promoted.")
        import_definition = resolve_powerbi_model_dir(
            PROJECT_ROOT / run.source_model_path,
            PROJECT_ROOT,
        )
        if not verify_import_source(run) or proposal_is_stale(
            proposal,
            powerbi_definition_dir=import_definition,
        ):
            raise WorkflowStateError(
                "Import or canonical source snapshot is stale; re-import and re-propose.",
                code="STALE_PROPOSAL",
            )
        if require_applicable and (
            proposal.status is not ProposalStatus.PROPOSED
            or not proposal.canonical_application_available
            or not proposal.cross_target_valid
        ):
            raise WorkflowManualReview(
                "Import child is reconciliation-only or draft-only and cannot enter M4 apply."
            )
        change_store.save_proposal(proposal)
        return proposal


def run_approve(args: argparse.Namespace) -> int:
    def approve_or_promote() -> ProposalRecord:
        change_store = _store()
        _promote_import_child(
            change_store,
            args.change_id,
            require_applicable=True,
        )
        return approve_change(change_store, args.change_id, actor=args.actor)

    return _run_workflow(args, approve_or_promote)


def run_reject(args: argparse.Namespace) -> int:
    def reject_or_promote() -> ProposalRecord:
        change_store = _store()
        _promote_import_child(
            change_store,
            args.change_id,
            require_applicable=False,
        )
        return reject_change(change_store, args.change_id, actor=args.actor)

    return _run_workflow(args, reject_or_promote)


def run_apply(args: argparse.Namespace) -> int:
    return _run_workflow(args, lambda: apply_change(_store(), args.change_id))


def run_powerbi_copy(args: argparse.Namespace) -> int:
    return _run_workflow(
        args,
        lambda: apply_powerbi_copy_change(
            _store(),
            args.change_id,
            output_dir=Path(args.output_dir),
            allow_metadata=args.allow_metadata,
            allow_supported_definitions=args.allow_supported_definitions,
        ),
    )


def run_validate(args: argparse.Namespace) -> int:
    return _run_workflow(args, lambda: validate_change(_store(), args.change_id))


def run_rollback(args: argparse.Namespace) -> int:
    return _run_workflow(args, lambda: rollback_change(_store(), args.change_id))


def run_guide_conversion(args: argparse.Namespace) -> int:
    from .guided_conversion import run_guide_conversion

    return run_guide_conversion(args)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "inspect":
        return run_inspect(args)
    if args.command == "propose":
        return run_propose(args)
    if args.command == "show":
        return run_show(args)
    if args.command == "list":
        return run_list(args)
    if args.command == "discard":
        return run_discard(args)
    if args.command == "approve":
        return run_approve(args)
    if args.command == "reject":
        return run_reject(args)
    if args.command == "apply":
        return run_apply(args)
    if args.command == "apply-powerbi-copy":
        return run_powerbi_copy(args)
    if args.command == "validate":
        return run_validate(args)
    if args.command == "rollback":
        return run_rollback(args)
    if args.command == "import-powerbi":
        return run_import_powerbi(args)
    if args.command == "show-import":
        return run_show_import(args)
    if args.command == "list-imports":
        return run_list_imports(args)
    if args.command == "propose-import":
        return run_propose_import(args)
    if args.command == "discard-import":
        return run_discard_import(args)
    if args.command == "explain-import":
        return run_explain_import(args)
    if args.command == "review-conversion":
        return run_review_conversion(args)
    if args.command == "accept-mapping":
        return run_accept_mapping(args)
    if args.command == "correct-mapping":
        return run_correct_mapping(args)
    if args.command == "reject-mapping":
        return run_reject_mapping(args)
    if args.command == "finalize-conversion":
        return run_finalize_conversion(args)
    if args.command == "audit-powerbi-snowflake":
        return run_audit_powerbi_snowflake(args)
    if args.command == "preview-sync":
        return run_preview_sync(args)
    if args.command == "suggest-review":
        return run_suggest_review(args)
    if args.command == "record-review":
        return run_record_review(args)
    if args.command == "guide-conversion":
        return run_guide_conversion(args)
    if args.command == "demo":
        return run_demo(args)
    if args.command == "demo-finalize":
        return run_demo_finalize(args)
    if args.command == "check-canonical-drift":
        return run_check_canonical_drift(args)
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
