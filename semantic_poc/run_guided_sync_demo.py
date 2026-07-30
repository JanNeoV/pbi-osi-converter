"""Checked, offline Task 05 guided-sync demonstration.

The runner is deliberately an orchestration layer: it invokes the deterministic
Task 01 audit, proposal, preview, review-memory, and review-recording modules.
It never generates executable DAX or Snowflake SQL itself.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from semantic_poc.agent import cli
from semantic_poc.agent.change_store import ChangeStore
from semantic_poc.agent.import_store import ImportStore
from semantic_poc.agent.import_workflow import (
    create_import_proposal_batch,
    create_import_run,
)
from semantic_poc.agent.pbi_trial_v2_demo import prepare_demo
from semantic_poc.agent.powerbi_snowflake_audit import audit_powerbi_snowflake
from semantic_poc.agent.preview_sync import PORTABLE_POWERBI_DEFINITION, preview_sync
from semantic_poc.agent.proposal_engine import propose_change
from semantic_poc.agent.proposal_models import ProposalStatus
from semantic_poc.agent.review_recording import ReviewStateError, record_review, suggest_review
from semantic_poc.agent.schemas import MetricChangeRequest
from semantic_poc.agent.tool_contract.harness import run_conformance
from semantic_poc.run_pbi_trial_v2_audit import main as task01_checked_main
from semantic_poc.src.models import PROJECT_ROOT
from semantic_poc.validate_guided_sync_receipt import sha256_file, sha256_tree


ROOT = PROJECT_ROOT
GOLDEN = ROOT / "semantic_poc" / "demo" / "guided_sync" / "golden"
SCRATCH = ROOT / ".tmp" / "guided-sync-demo"
REQUEST = ROOT / "semantic_poc" / "examples" / "requests" / "valid_sbr_finishers_add_filter.json"
TASK01_MODEL = ROOT / "semantic_poc" / "benchmark" / "pbi_trial_v2" / "fixtures" / "pbi_trial.SemanticModel"
TASK01_TARGET = ROOT / "pbit" / "snowflake_semantic_view" / "pbi_trial.yaml"
TASK01_SPEC = ROOT / "semantic_poc" / "benchmark" / "pbi_trial_v2" / "measure-cases.yml"
HOURS_MODEL = ROOT / "semantic_poc" / "tests" / "fixtures" / "conversion_benchmark" / "b_semantic_traps.SemanticModel"
UNSUPPORTED_MODEL = ROOT / "semantic_poc" / "tests" / "fixtures" / "conversion_benchmark" / "c_unsupported.SemanticModel"
RULE = ROOT / "semantic_poc" / "review_memory" / "accepted" / "unit_conversion_seconds_to_hours.yml"
RECEIPTS = [
    ROOT / "semantic_poc" / "demo" / "guided_sync" / "task-markers" / name
    for name in (
        "01-conversion-gap-demo.json",
        "02-guided-sync-contract.json",
        "03-preview-sync-poc.json",
        "04-guided-review-and-skill.json",
    )
]
PROTECTED = [
    ROOT / "models" / "semantic" / "triathlon_semantic.yml",
    ROOT / "semantic" / "triathlon_metric_contract.yml",
    TASK01_TARGET,
    ROOT / "semantic_poc" / "output",
]
TOP_LEVEL = {
    "conversion-gap-summary.json",
    "create-preview",
    "update-preview",
    "blocked-preview",
    "recorded-review",
    "stale-decision.json",
    "immutability-report.json",
    "executive-summary.md",
    "manifest.json",
}
FULL_PREVIEW_FILES = {
    "canonical-candidate.yml",
    "cross-target-report.md",
    "manifest.json",
    "powerbi-copy-plan.json",
    "snowflake-candidate-diff.md",
    "snowflake-semantic-view.candidate.yml",
    "target-diff.json",
    "validation-queue.json",
    "validation-queue.md",
}
BLOCKED_PREVIEW_FILES = {
    "blocked-preview.json",
    "cross-target-report.md",
    "manifest.json",
    "validation-queue.json",
    "validation-queue.md",
}
REVIEW_FILES = {"manifest.json", "review-decision.yml", "review-session.md"}
FIXED_RECORDED_AT = "2026-07-18T19:10:00Z"
FIXED_ACTOR = "poc-demo-fixture"
FIXED_DECISION_ID = "review_decision_guided_sync_demo_v1"


class DemoError(RuntimeError):
    def __init__(self, stage: str, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.stage = stage
        self.reason = reason
        self.detail = detail


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(_json_bytes(value))


def _write_yaml(path: Path, value: Any) -> None:
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=True), encoding="utf-8", newline="\n")


def _sha(path: Path) -> str:
    return sha256_file(path) if path.is_file() else sha256_tree(path)


def _tree(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file() and not item.is_symlink()
    }


def _tree_difference(actual: Mapping[str, bytes], expected: Mapping[str, bytes]) -> str:
    differences = []
    for relative in sorted(set(actual) | set(expected)):
        actual_bytes = actual.get(relative)
        expected_bytes = expected.get(relative)
        if actual_bytes == expected_bytes:
            continue
        if actual_bytes is None:
            differences.append(f"{relative}:missing")
        elif expected_bytes is None:
            differences.append(f"{relative}:unexpected")
        else:
            context = _content_difference(relative, actual_bytes, expected_bytes)
            differences.append(
                f"{relative}:"
                f"{hashlib.sha256(actual_bytes).hexdigest()}!="
                f"{hashlib.sha256(expected_bytes).hexdigest()}"
                f"{context}"
            )
    return ", ".join(differences)


def _content_difference(relative: str, actual: bytes, expected: bytes) -> str:
    try:
        actual_text = actual.decode("utf-8").splitlines()
        expected_text = expected.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return ""
    for line_number, (actual_line, expected_line) in enumerate(
        zip(actual_text, expected_text), start=1
    ):
        if actual_line != expected_line:
            return (
                f"[line={line_number},"
                f"actual={json.dumps(actual_line[:160], ensure_ascii=True)},"
                f"expected={json.dumps(expected_line[:160], ensure_ascii=True)}]"
            )
    if len(actual_text) != len(expected_text):
        return f"[lines={len(actual_text)}!={len(expected_text)}]"
    return ""


def _assert_file_set(path: Path, expected: set[str], label: str) -> None:
    actual = {item.name for item in path.iterdir()}
    if actual != expected:
        raise DemoError("BUNDLE", "UNEXPECTED_FILE_SET", f"{label}: expected {sorted(expected)}, got {sorted(actual)}")


def _snapshot_protected() -> dict[str, str]:
    return {path.relative_to(ROOT).as_posix(): _sha(path) for path in PROTECTED}


def _assert_protected(before: Mapping[str, str]) -> dict[str, str]:
    after = _snapshot_protected()
    if dict(before) != after:
        changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        raise DemoError("IMMUTABILITY", "PROTECTED_INPUT_CHANGED", ", ".join(changed))
    return after


def _run_main(main: Any, argv: Sequence[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(list(argv))
    return int(code), stdout.getvalue(), stderr.getvalue()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _copy_preview(source: Path, destination: Path, expected: set[str], label: str) -> None:
    _assert_file_set(source, expected, label)
    shutil.copytree(source, destination)
    _assert_file_set(destination, expected, label)


def _task01_evidence(workspace: Path) -> dict[str, Any]:
    checked_code, stdout, stderr = _run_main(task01_checked_main, ["--check", "--json"])
    if checked_code != 0:
        raise DemoError("CONVERSION_GAP", "TASK01_CHECK_FAILED", stderr or stdout)
    checked = json.loads(stdout)
    audit = audit_powerbi_snowflake(
        model_dir=TASK01_MODEL,
        snowflake_yaml=TASK01_TARGET,
        benchmark_spec=TASK01_SPEC,
        repository_root=ROOT,
    )
    demo = prepare_demo(audit, repository_root=ROOT, check=True)
    generic_dir = workspace / "generic-audit"
    code, stdout, stderr = _run_main(
        cli.main,
        [
            "audit-powerbi-snowflake", "--model-dir", _relative(TASK01_MODEL),
            "--snowflake-yaml", _relative(TASK01_TARGET), "--benchmark-spec", _relative(TASK01_SPEC),
            "--output-dir", _relative(generic_dir), "--json",
        ],
    )
    if code != 3:
        raise DemoError("CONVERSION_GAP", "GENERIC_AUDIT_EXIT", f"expected 3, got {code}: {stderr or stdout}")
    summary = dict(audit.summary)
    if summary.get("source_measure_count") != 46 or summary.get("matched_measure_count") != 21:
        raise DemoError("CONVERSION_GAP", "TASK01_COUNTS_CHANGED", json.dumps(summary, sort_keys=True))
    return {
        "checked_runner_exit_code": checked_code,
        "checked_runner": checked,
        "generic_audit_exit_code": 3,
        "generic_audit_verdict": summary["executive_verdict"],
        "summary": summary,
        "claims": {
            "PROVEN": {
                "confirmed_incorrect": demo["confirmed_incorrect"],
                "proven_caught": demo["proven_caught"],
                "proven_not_caught": demo["proven_not_caught"],
            },
            "OBSERVED": {
                "source_measures": demo["source_measures"],
                "emitted_metrics": demo["emitted_metrics"],
                "omitted_measures": demo["omitted_measures"],
                "potentially_incorrect": demo["potentially_incorrect"],
            },
            "NOT_PROVEN": {"count": demo["not_proven"], "runtime_status": demo["runtime_status"]},
        },
    }


def _supported_previews(stage: Path, workspace: Path) -> dict[str, Any]:
    changes, imports = ChangeStore(workspace / "supported-changes"), ImportStore(workspace / "supported-imports")
    request = MetricChangeRequest.from_dict(json.loads(REQUEST.read_text(encoding="utf-8")))
    proposal = propose_change(request, powerbi_definition_dir=PORTABLE_POWERBI_DEFINITION)
    if proposal.status is not ProposalStatus.PROPOSED:
        raise DemoError("SUPPORTED_PREVIEW", "PROPOSAL_NOT_SUPPORTED", proposal.status.value)
    changes.save_proposal(proposal)
    create_temp, update_temp = workspace / "create-preview", workspace / "update-preview"
    create = preview_sync(proposal.change_id, target_mode="create", output_dir=create_temp, change_store=changes, import_store=imports)
    update = preview_sync(
        proposal.change_id, target_mode="update", existing_snowflake_yaml=ROOT / "semantic_poc" / "output" / "snowflake_semantic_view.yml",
        output_dir=update_temp, change_store=changes, import_store=imports,
    )
    if create.exit_code != 0 or update.exit_code != 0:
        raise DemoError("SUPPORTED_PREVIEW", "PREVIEW_FAILED", f"create={create.exit_code}, update={update.exit_code}")
    _assert_file_set(create_temp, FULL_PREVIEW_FILES, "create preview")
    _assert_file_set(update_temp, FULL_PREVIEW_FILES, "update preview")
    create_yaml = (create_temp / "snowflake-semantic-view.candidate.yml").read_bytes()
    update_yaml = (update_temp / "snowflake-semantic-view.candidate.yml").read_bytes()
    if create_yaml != update_yaml:
        raise DemoError("SUPPORTED_PREVIEW", "MODE_SEMANTIC_DRIFT", "create/update Snowflake candidates differ")
    update_diff = json.loads((update_temp / "target-diff.json").read_text(encoding="utf-8"))
    changes_list = update_diff.get("changes", [])
    if (len(changes_list) != 1 or changes_list[0].get("object_id") != "metric:results.valid_sbr_finishers" or update_diff.get("additions") or update_diff.get("removals")):
        raise DemoError("SUPPORTED_PREVIEW", "UNRELATED_TARGET_DRIFT", json.dumps(update_diff, sort_keys=True))
    _copy_preview(create_temp, stage / "create-preview", FULL_PREVIEW_FILES, "create preview")
    _copy_preview(update_temp, stage / "update-preview", FULL_PREVIEW_FILES, "update preview")
    return {"change_id": proposal.change_id, "create_exit_code": create.exit_code, "update_exit_code": update.exit_code, "target_change_count": len(changes_list)}


def _hours_preview(workspace: Path) -> tuple[Path, ChangeStore, ImportStore]:
    imports, changes = ImportStore(workspace / "hours-imports"), ChangeStore(workspace / "hours-changes")
    run = create_import_run(HOURS_MODEL, store=imports, now=datetime(2026, 7, 18, 19, 0, tzinfo=timezone.utc), entropy="05050505")
    batch = create_import_proposal_batch(run.import_id, store=imports, change_store=changes, now=datetime(2026, 7, 18, 19, 1, tzinfo=timezone.utc))
    proposal = next(
        changes.load_proposal(change_id) for change_id in batch.blocked_child_ids
        if changes.load_proposal(change_id).resolution.get("source_measure") == "Hours"
    )
    output = workspace / "hours-preview"
    result = preview_sync(proposal.change_id, target_mode="create", output_dir=output, change_store=changes, import_store=imports)
    if result.exit_code != 3:
        raise DemoError("REVIEW_MEMORY", "HOURS_PREVIEW_EXIT", str(result.exit_code))
    _assert_file_set(output, BLOCKED_PREVIEW_FILES, "Hours preview")
    return output, changes, imports


def _decision(preview: Path) -> dict[str, Any]:
    queue_path, manifest_path = preview / "validation-queue.json", preview / "manifest.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    finding = queue["findings"][0]
    suggestion = suggest_review(preview, finding_id=finding["finding_id"]).evaluations[0]
    if suggestion.get("status") != "REVIEW_RULE_SUGGESTED" or suggestion.get("registered_rule_id") != "review_unit_conversion_seconds_to_hours_v1" or suggestion.get("human_confirmation") != "REQUIRED":
        raise DemoError("REVIEW_MEMORY", "EXACT_RULE_NOT_SUGGESTED", json.dumps(suggestion, sort_keys=True))
    return {
        "actor": FIXED_ACTOR,
        "application_state": "NOT_REQUESTED",
        "approval_state": "NOT_REQUESTED",
        "bound_input_hashes": finding["bound_input_hashes"],
        "decision_id": FIXED_DECISION_ID,
        "deployment_authorized": False,
        "evidence_references": [
            {"evidence_id": "evidence_queue", "finding_id": finding["finding_id"], "kind": "PREVIEW_ARTIFACT", "path": _relative(queue_path), "sha256": _sha(queue_path)},
            {"evidence_id": "evidence_rule", "finding_id": finding["finding_id"], "kind": "REVIEW_RULE", "path": _relative(RULE), "sha256": _sha(RULE)},
        ],
        "finding_id": finding["finding_id"],
        "human_confirmation": "RECORDED",
        "preview_id": queue["preview_id"],
        "preview_manifest_sha256": _sha(manifest_path),
        "propose_review_rule": False,
        "rationale": "The exact registered unit rule remains applicable.",
        "recorded_at": FIXED_RECORDED_AT,
        "schema_version": 1,
        "selected_answer": suggestion["permitted_structured_answer"],
        "suggested_rule": {"rule_id": suggestion["registered_rule_id"], "sha256": suggestion["registered_rule_sha256"]},
    }


def _review_evidence(stage: Path, workspace: Path) -> dict[str, Any]:
    preview, _, _ = _hours_preview(workspace)
    decision_file = workspace / "hours-decision.yml"
    decision = _decision(preview)
    _write_yaml(decision_file, decision)
    recorded = record_review(preview, decision_file=decision_file, output_dir=workspace / "recorded-review")
    if recorded.exit_code != 0 or recorded.status != "REVIEW_RECORDED":
        raise DemoError("REVIEW_MEMORY", "RECORD_REVIEW_FAILED", f"{recorded.exit_code}/{recorded.status}")
    _copy_preview(workspace / "recorded-review", stage / "recorded-review", REVIEW_FILES, "recorded review")
    return {"suggestion_status": "REVIEW_RULE_SUGGESTED", "human_confirmation": "REQUIRED", "recorded_status": recorded.status, "decision_id": FIXED_DECISION_ID}


def _stale_evidence(workspace: Path) -> dict[str, Any]:
    copied_model = workspace / "stale.SemanticModel"
    shutil.copytree(HOURS_MODEL, copied_model)
    imports, changes = ImportStore(workspace / "stale-imports"), ChangeStore(workspace / "stale-changes")
    run = create_import_run(copied_model, store=imports, now=datetime(2026, 7, 18, 19, 2, tzinfo=timezone.utc), entropy="06060606")
    batch = create_import_proposal_batch(run.import_id, store=imports, change_store=changes, now=datetime(2026, 7, 18, 19, 3, tzinfo=timezone.utc))
    proposal = next(changes.load_proposal(change_id) for change_id in batch.blocked_child_ids if changes.load_proposal(change_id).resolution.get("source_measure") == "Hours")
    preview = workspace / "stale-preview"
    result = preview_sync(proposal.change_id, target_mode="create", output_dir=preview, change_store=changes, import_store=imports)
    if result.exit_code != 3:
        raise DemoError("STALE", "STALE_SETUP_PREVIEW_EXIT", str(result.exit_code))
    decision_file = workspace / "stale-decision.yml"
    _write_yaml(decision_file, _decision(preview))
    table = copied_model / "definition" / "tables" / "Measures.tmdl"
    if not table.is_file():
        table = next((copied_model / "definition" / "tables").glob("*.tmdl"))
    original = table.read_bytes()
    table.write_bytes(original + b"\n// deterministic stale mutation\n")
    try:
        record_review(preview, decision_file=decision_file, output_dir=workspace / "stale-review")
    except ReviewStateError as exc:
        if exc.code != "PREVIEW_INPUT_STALE":
            raise DemoError("STALE", "UNEXPECTED_STALE_REASON", exc.code) from exc
        return {"status": "REJECTED", "exit_code": 4, "reason_code": exc.code, "mutated_path": _relative(table)}
    finally:
        table.write_bytes(original)
    raise DemoError("STALE", "STALE_DECISION_ACCEPTED", "record-review accepted a mutated input")


def _blocked_evidence(stage: Path, workspace: Path) -> dict[str, Any]:
    imports, changes = ImportStore(workspace / "blocked-imports"), ChangeStore(workspace / "blocked-changes")
    run = create_import_run(UNSUPPORTED_MODEL, store=imports, now=datetime(2026, 7, 18, 19, 4, tzinfo=timezone.utc), entropy="07070707")
    batch = create_import_proposal_batch(run.import_id, store=imports, change_store=changes, now=datetime(2026, 7, 18, 19, 5, tzinfo=timezone.utc))
    proposal = next(changes.load_proposal(change_id) for change_id in batch.blocked_child_ids if changes.load_proposal(change_id).resolution.get("source_measure") == "Inactive Relationship")
    output = workspace / "blocked-preview"
    result = preview_sync(proposal.change_id, target_mode="create", output_dir=output, change_store=changes, import_store=imports)
    if result.exit_code != 3:
        raise DemoError("BLOCKED", "BLOCKED_PREVIEW_EXIT", str(result.exit_code))
    _assert_file_set(output, BLOCKED_PREVIEW_FILES, "blocked preview")
    blocked = json.loads((output / "blocked-preview.json").read_text(encoding="utf-8"))
    queue = json.loads((output / "validation-queue.json").read_text(encoding="utf-8"))
    if blocked.get("executable_artifacts_emitted") or any(key in blocked for key in ("expression", "patch", "candidate")):
        raise DemoError("BLOCKED", "EXECUTABLE_ARTIFACT_EMITTED", json.dumps(blocked, sort_keys=True))
    if not any("DAX_INACTIVE_RELATIONSHIP_DEPENDENCY" in finding["reason_codes"] for finding in queue["findings"]):
        raise DemoError("BLOCKED", "RELATIONSHIP_REASON_MISSING", json.dumps(queue, sort_keys=True))
    _copy_preview(output, stage / "blocked-preview", BLOCKED_PREVIEW_FILES, "blocked preview")
    return {"exit_code": result.exit_code, "status": "MANUAL_REVIEW_REQUIRED", "reason_code": "DAX_INACTIVE_RELATIONSHIP_DEPENDENCY"}


def _write_bundle(stage: Path, evidence: Mapping[str, Any], before: Mapping[str, str], after: Mapping[str, str]) -> None:
    _write_json(stage / "conversion-gap-summary.json", evidence["conversion_gap"])
    _write_json(stage / "stale-decision.json", evidence["stale"])
    _write_json(stage / "immutability-report.json", {"before": before, "after": after, "source_modified": False})
    summary = [
        "# Guided sync demonstration", "", "All results below are derived from deterministic component artifacts.", "",
        "- Conversion gap: UNSAFE_TO_ACCEPT_BLINDLY", "- Supported previews: SAFE_CANDIDATE", "- Source mutation: NO",
        "- Stale decision: REJECTED", "- Review memory: SUGGESTED_CONFIRMATION_REQUIRED",
        "- Unsupported change: MANUAL_REVIEW_REQUIRED", "- Deployment: NOT_PERFORMED", "",
    ]
    (stage / "executive-summary.md").write_text("\n".join(summary), encoding="utf-8", newline="\n")
    files = {name: hashlib.sha256(payload).hexdigest() for name, payload in _tree(stage).items()}
    manifest = {
        "schema_version": 1,
        "prerequisite_receipts": [{"path": _relative(path), "sha256": _sha(path)} for path in RECEIPTS],
        "component_results": evidence,
        "protected_inputs": {"before": before, "after": after},
        "source_modified": False,
        "approval_performed": False,
        "application_performed": False,
        "network_contacted": False,
        "deployment_performed": False,
        "agent_tool_contract": "PASSED",
        "verdict": "INCREMENTAL_SYNC_POC_ACCEPTED",
        "files": files,
    }
    _write_json(stage / "manifest.json", manifest)
    _assert_file_set(stage, TOP_LEVEL, "guided demo bundle")


def generate(output: Path, *, workspace: Path) -> None:
    before = _snapshot_protected()
    workspace.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise DemoError("OUTPUT", "OUTPUT_MUST_BE_FRESH", str(output))
    stage = output.parent / (output.name + ".stage")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        evidence: dict[str, Any] = {}
        evidence["conversion_gap"] = _task01_evidence(workspace)
        evidence["supported"] = _supported_previews(stage, workspace)
        evidence["review_memory"] = _review_evidence(stage, workspace)
        evidence["stale"] = _stale_evidence(workspace)
        evidence["blocked"] = _blocked_evidence(stage, workspace)
        contract = run_conformance()
        if contract.get("status") != "PASSED" or contract.get("executed_prohibited_tool_calls") != 0:
            raise DemoError("TOOL_CONTRACT", "CONFORMANCE_FAILED", json.dumps(contract, sort_keys=True))
        evidence["tool_contract"] = contract
        after = _assert_protected(before)
        _write_bundle(stage, evidence, before, after)
        _assert_protected(before)
        stage.replace(output)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def _scratch_manifest(path: Path) -> Path:
    return path / ".runner-scratch.json"


def _prepare_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)
    _write_json(_scratch_manifest(path), {"schema_version": 1, "owner": "guided-sync-demo", "files": [".runner-scratch.json"]})


def _safe_clean() -> None:
    if not SCRATCH.exists():
        return
    if SCRATCH.is_symlink() or SCRATCH.resolve() != SCRATCH.absolute().resolve():
        raise DemoError("CLEAN", "SCRATCH_PATH_UNSAFE", str(SCRATCH))
    for child in sorted(SCRATCH.iterdir()):
        if child.is_symlink() or not child.is_dir() or not _scratch_manifest(child).is_file():
            raise DemoError("CLEAN", "SCRATCH_TREE_UNSAFE", str(child))
        manifest = json.loads(_scratch_manifest(child).read_text(encoding="utf-8"))
        if manifest != {"schema_version": 1, "owner": "guided-sync-demo", "files": [".runner-scratch.json"]} or {item.name for item in child.iterdir()} != {".runner-scratch.json"}:
            raise DemoError("CLEAN", "SCRATCH_TREE_TAMPERED", str(child))
        child.rmdir()
    SCRATCH.rmdir()


def _success_transcript() -> str:
    return "\n".join((
        "Conversion gap: UNSAFE_TO_ACCEPT_BLINDLY", "Supported create preview: SAFE_CANDIDATE", "Supported update preview: SAFE_CANDIDATE",
        "Source mutation: NO", "Stale decision: REJECTED", "Unrelated target drift: 0", "Review memory: SUGGESTED_CONFIRMATION_REQUIRED",
        "Unsupported change: MANUAL_REVIEW_REQUIRED", "Deterministic modes: CREATE_AND_UPDATE_PASSED", "Agent tool contract: PASSED",
        "Deployment: NOT_PERFORMED", "INCREMENTAL_SYNC_POC_ACCEPTED",
    ))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the checked offline guided-sync demonstration.")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--bootstrap-golden", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.clean:
            _safe_clean()
        if args.check or args.bootstrap_golden:
            SCRATCH.mkdir(parents=True, exist_ok=True)
            one, two = SCRATCH / "one", SCRATCH / "two"
            workspace = SCRATCH / "work"
            try:
                first, second = one / "bundle", two / "bundle"
                generate(first, workspace=workspace)
                shutil.rmtree(workspace)
                generate(second, workspace=workspace)
                if _tree(first) != _tree(second):
                    raise DemoError("DETERMINISM", "BUNDLE_BYTES_DIFFER", "independent runs differ")
                if args.bootstrap_golden:
                    if GOLDEN.exists():
                        raise DemoError("GOLDEN", "GOLDEN_ALREADY_EXISTS", str(GOLDEN))
                    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(first, GOLDEN)
                else:
                    actual = _tree(first)
                    expected = _tree(GOLDEN) if GOLDEN.is_dir() else {}
                    if actual != expected:
                        detail = _tree_difference(actual, expected)
                        raise DemoError("GOLDEN", "GOLDEN_MISMATCH", detail or str(GOLDEN))
            finally:
                for transient in (one, two, workspace):
                    if transient.exists():
                        shutil.rmtree(transient)
                if SCRATCH.exists() and not any(SCRATCH.iterdir()):
                    SCRATCH.rmdir()
        else:
            if not args.output_dir:
                parser.error("--output-dir is required without --check")
            output = Path(args.output_dir).resolve()
            if not output.is_relative_to(ROOT.resolve()):
                raise DemoError("OUTPUT", "OUTPUT_OUTSIDE_REPOSITORY", str(output))
            SCRATCH.mkdir(parents=True, exist_ok=True)
            workspace = SCRATCH / "run"
            try:
                generate(output, workspace=workspace)
            finally:
                if workspace.exists():
                    shutil.rmtree(workspace)
                if SCRATCH.exists() and not any(SCRATCH.iterdir()):
                    SCRATCH.rmdir()
    except DemoError as exc:
        print("INCREMENTAL_SYNC_POC_NOT_ACCEPTED", file=sys.stderr)
        print(f"{exc.stage}:{exc.reason}: {exc.detail}", file=sys.stderr)
        return 1
    except Exception as exc:  # Defensive stable diagnostic for unexpected failures.
        print("INCREMENTAL_SYNC_POC_NOT_ACCEPTED", file=sys.stderr)
        print(f"UNEXPECTED:{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(_success_transcript())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
