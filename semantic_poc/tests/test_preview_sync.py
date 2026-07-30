from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import jsonschema
import pytest
import yaml

from semantic_poc.agent import cli
from semantic_poc.agent import preview_sync as preview_module
from semantic_poc.agent.change_store import ChangeStore
from semantic_poc.agent.import_store import ImportStore
from semantic_poc.agent.import_workflow import (
    create_import_proposal_batch,
    create_import_run,
)
from semantic_poc.agent.preview_sync import (
    BLOCKED_FILES,
    FULL_FILES,
    PORTABLE_POWERBI_DEFINITION,
    PreviewInputError,
    PreviewStateError,
    preview_sync,
)
from semantic_poc.agent.proposal_engine import propose_change
from semantic_poc.agent.proposal_models import ApprovalState, ProposalStatus
from semantic_poc.agent.schemas import MetricChangeRequest
from semantic_poc.agent.workflow import WorkflowError, apply_change, approve_change
from semantic_poc.src.models import DBT_SEMANTIC_YAML, PROJECT_ROOT


REQUEST = (
    PROJECT_ROOT
    / "semantic_poc"
    / "examples"
    / "requests"
    / "valid_sbr_finishers_add_filter.json"
)
NO_OP_REQUEST = (
    PROJECT_ROOT
    / "semantic_poc"
    / "tests"
    / "fixtures"
    / "proposals"
    / "no_op_exclusions.json"
)
UPDATE_BASELINE = (
    PROJECT_ROOT / "semantic_poc" / "output" / "snowflake_semantic_view.yml"
)
SUPPORTED_IMPORT = (
    PROJECT_ROOT
    / "semantic_poc"
    / "tests"
    / "fixtures"
    / "powerbi_import"
    / "supported.SemanticModel"
)
SCALAR_MAPPING = SUPPORTED_IMPORT.parent / "scalar_mapping.yml"
UNSUPPORTED_IMPORT = (
    PROJECT_ROOT
    / "semantic_poc"
    / "tests"
    / "fixtures"
    / "conversion_benchmark"
    / "c_unsupported.SemanticModel"
)
QUEUE_SCHEMA = (
    PROJECT_ROOT
    / "semantic_poc"
    / "agent"
    / "preview_sync_validation_queue.schema.json"
)
FULL_SET = set(FULL_FILES)
BLOCKED_SET = set(BLOCKED_FILES)


@pytest.fixture
def workspace() -> Path:
    path = Path(
        tempfile.mkdtemp(
            prefix="pytest-preview-sync-",
            dir=PROJECT_ROOT / ".tmp",
        )
    )
    try:
        yield path
    finally:
        resolved = path.resolve()
        assert resolved.is_relative_to((PROJECT_ROOT / ".tmp").resolve())
        shutil.rmtree(resolved)


def _request(path: Path) -> MetricChangeRequest:
    return MetricChangeRequest.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _supported(
    workspace: Path,
) -> tuple[ChangeStore, ImportStore, str]:
    changes = ChangeStore(workspace / "changes")
    imports = ImportStore(workspace / "imports")
    proposal = propose_change(
        _request(REQUEST),
        powerbi_definition_dir=PORTABLE_POWERBI_DEFINITION,
    )
    assert proposal.status is ProposalStatus.PROPOSED
    assert proposal.change_id == "chg_20260718T190000Z_90909090"
    assert len(proposal.canonical_patch) == 1
    changes.save_proposal(proposal)
    return changes, imports, proposal.change_id


def _files(path: Path) -> set[str]:
    return {item.name for item in path.iterdir()}


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"), key=lambda value: value.as_posix())
        if item.is_file()
    }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_parser_exposes_exact_preview_surface_and_argument_rules() -> None:
    parser = cli.build_parser()
    create = parser.parse_args(
        [
            "preview-sync",
            "chg_20260718T190000Z_90909090",
            "--target-mode",
            "create",
            "--output-dir",
            ".tmp/preview",
            "--check",
            "--json",
        ]
    )
    assert create.command == "preview-sync"
    assert create.target_mode == "create"
    assert create.check and create.json
    update = parser.parse_args(
        [
            "preview-sync",
            "chg_20260718T190000Z_90909090",
            "--target-mode",
            "update",
            "--existing-snowflake-yaml",
            "semantic_poc/output/snowflake_semantic_view.yml",
            "--output-dir",
            ".tmp/preview",
        ]
    )
    assert update.target_mode == "update"
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "preview-sync",
                "chg_20260718T190000Z_90909090",
                "--target-mode",
                "update",
                "--output-dir",
                ".tmp/preview",
            ]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "preview-sync",
                "chg_20260718T190000Z_90909090",
                "--target-mode",
                "create",
                "--existing-snowflake-yaml",
                "semantic_poc/output/snowflake_semantic_view.yml",
                "--output-dir",
                ".tmp/preview",
            ]
        )


def test_supported_create_update_are_complete_deterministic_and_checkable(
    workspace: Path,
) -> None:
    changes, imports, change_id = _supported(workspace)
    canonical_before = DBT_SEMANTIC_YAML.read_bytes()
    baseline_before = UPDATE_BASELINE.read_bytes()
    fixture_before = _tree_bytes(PORTABLE_POWERBI_DEFINITION.parent)

    create_a = workspace / "create-a"
    create_b = workspace / "create-b"
    for output in (create_a, create_b):
        result = preview_sync(
            change_id,
            target_mode="create",
            output_dir=output,
            change_store=changes,
            import_store=imports,
        )
        assert result.exit_code == 0
        assert result.artifact_count == 9
        assert _files(output) == FULL_SET
    assert _tree_bytes(create_a) == _tree_bytes(create_b)
    checked = preview_sync(
        change_id,
        target_mode="create",
        output_dir=create_a,
        check=True,
        change_store=changes,
        import_store=imports,
    )
    assert checked.check and checked.exit_code == 0

    update_a = workspace / "update-a"
    update_b = workspace / "update-b"
    for output in (update_a, update_b):
        preview_sync(
            change_id,
            target_mode="update",
            existing_snowflake_yaml=UPDATE_BASELINE,
            output_dir=output,
            change_store=changes,
            import_store=imports,
        )
    assert _tree_bytes(update_a) == _tree_bytes(update_b)
    create_diff = json.loads(
        (create_a / "target-diff.json").read_text(encoding="utf-8")
    )
    update_diff = json.loads(
        (update_a / "target-diff.json").read_text(encoding="utf-8")
    )
    assert create_diff["summary"]["additions"] > 0
    assert create_diff["summary"]["changes"] == 0
    assert update_diff["summary"] == {
        "additions": 0,
        "changes": 1,
        "removals": 0,
    }
    assert update_diff["changes"][0]["object_id"] == (
        "metric:results.valid_sbr_finishers"
    )
    assert DBT_SEMANTIC_YAML.read_bytes() == canonical_before
    assert UPDATE_BASELINE.read_bytes() == baseline_before
    assert _tree_bytes(PORTABLE_POWERBI_DEFINITION.parent) == fixture_before


def test_manifest_hashes_queue_signatures_and_powerbi_lineage(
    workspace: Path,
) -> None:
    changes, imports, change_id = _supported(workspace)
    output = workspace / "preview"
    preview_sync(
        change_id,
        target_mode="update",
        existing_snowflake_yaml=UPDATE_BASELINE,
        output_dir=output,
        change_store=changes,
        import_store=imports,
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["artifact_hashes"]) == FULL_SET - {"manifest.json"}
    for name, digest in manifest["artifact_hashes"].items():
        assert digest == _sha((output / name).read_bytes())
    assert manifest["application_performed"] is False
    assert manifest["approval_performed"] is False
    assert manifest["deployment_performed"] is False
    assert manifest["network_contacted"] is False
    assert manifest["source_edit_performed"] is False
    assert len(manifest["compiler_files"]) == 6
    assert [item["path"] for item in manifest["compiler_files"]] == sorted(
        item["path"] for item in manifest["compiler_files"]
    )
    assert manifest["source_powerbi_tree_sha256"] == (
        "54f8f8ab2f080aba5b42b924bb6467e363e36cc1da3bbf6f8e2deaf68614971b"
    )
    assert all(
        item["before_sha256"] == item["after_sha256"]
        for item in manifest["protected_inputs"]
    )

    plan = json.loads(
        (output / "powerbi-copy-plan.json").read_text(encoding="utf-8")
    )
    assert len(plan["definition_operations"]) == 1
    operation = plan["definition_operations"][0]
    assert operation["source_object_id"] == "MEASURES_[Valid SBR Finishers]"
    assert operation["target_mapping"] == {
        "measure": "Valid SBR Finishers",
        "table": "tri_measures",
    }
    assert operation["canonical_source"] == (
        "models/semantic/triathlon_semantic.yml"
    )

    queue = json.loads(
        (output / "validation-queue.json").read_text(encoding="utf-8")
    )
    schema = json.loads(QUEUE_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(queue)
    assert set(queue["review_classes"]) == {
        "FORMULA_REVIEW_REQUIRED",
        "MODEL_RELATIONSHIP_REVIEW_REQUIRED",
        "DATA_VALIDATION_REQUIRED",
        "METADATA_REVIEW_REQUIRED",
    }
    for finding in queue["findings"]:
        assert finding["finding_id"].startswith("fnd_")
        assert set(finding["semantic_signature_payload"]) == {
            "typed_expression",
            "typed_parameter_roles",
            "source_units",
            "target_units",
            "population",
            "grain",
            "filter_context",
            "relationship_context",
        }
        roles = finding["typed_pattern_signature_payload"][
            "typed_parameter_roles"
        ]
        assert set(roles) == set(finding["concrete_role_bindings"])
        jsonschema.Draft202012Validator.check_schema(
            finding["allowed_answer_schema"]
        )
    assert any(
        "BLANK_NULL_REPRESENTATION_JSON_NULL" in item["reason_codes"]
        and item["dependency_ids"] == ["valid_sbr_finishers"]
        for item in queue["findings"]
    )
    assert any(
        item["reason_codes"]
        == ["POWERBI_DISPLAY_FOLDER_NOT_CANONICALLY_SPECIFIED"]
        and "human_question" not in item
        for item in queue["findings"]
    )


def test_no_op_emits_unchanged_full_candidates_and_empty_diff(
    workspace: Path,
) -> None:
    changes = ChangeStore(workspace / "changes")
    imports = ImportStore(workspace / "imports")
    proposal = propose_change(
        _request(NO_OP_REQUEST),
        powerbi_definition_dir=PORTABLE_POWERBI_DEFINITION,
    )
    assert proposal.status is ProposalStatus.NO_OP
    changes.save_proposal(proposal)
    output = workspace / "noop"
    result = preview_sync(
        proposal.change_id,
        target_mode="update",
        existing_snowflake_yaml=UPDATE_BASELINE,
        output_dir=output,
        change_store=changes,
        import_store=imports,
    )
    assert result.exit_code == 0
    assert _files(output) == FULL_SET
    assert (output / "canonical-candidate.yml").read_bytes() == (
        DBT_SEMANTIC_YAML.read_bytes()
    )
    diff = json.loads((output / "target-diff.json").read_text(encoding="utf-8"))
    assert diff["summary"] == {"additions": 0, "changes": 0, "removals": 0}
    assert diff["additions"] == diff["changes"] == diff["removals"] == []


def test_supported_import_child_uses_its_immutable_source(
    workspace: Path,
) -> None:
    imports = ImportStore(workspace / "imports")
    changes = ChangeStore(workspace / "changes")
    run = create_import_run(
        SUPPORTED_IMPORT,
        store=imports,
        mapping_file=SCALAR_MAPPING,
        now=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        entropy="33333333",
    )
    batch = create_import_proposal_batch(
        run.import_id,
        store=imports,
        change_store=changes,
        now=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
    )
    child = next(
        item for item in batch.proposals if item["status"] == "PROPOSED"
    )
    output = workspace / "import-child"
    result = preview_sync(
        child["change_id"],
        target_mode="create",
        output_dir=output,
        change_store=changes,
        import_store=imports,
    )
    assert result.exit_code == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["proposal_source"] == "POWERBI_IMPORT"
    assert manifest["import_run_sha256"] is not None
    assert manifest["import_batch_sha256"] is not None
    assert manifest["source_powerbi_path"] == run.source_model_path


def test_import_blockers_persist_at_root_and_never_emit_executables(
    workspace: Path,
) -> None:
    imports = ImportStore(workspace / "imports")
    changes = ChangeStore(workspace / "changes")
    run = create_import_run(
        UNSUPPORTED_IMPORT,
        store=imports,
        now=datetime(2026, 7, 18, 19, 0, tzinfo=timezone.utc),
        entropy="00000003",
    )
    batch = create_import_proposal_batch(
        run.import_id,
        store=imports,
        change_store=changes,
        now=datetime(2026, 7, 18, 19, 0, tzinfo=timezone.utc),
    )
    assert batch.blocked_child_ids == tuple(sorted(batch.blocked_child_ids))
    assert len(batch.blocked_child_ids) == (
        len(batch.manual_review_items) + len(batch.unsupported_items)
    )
    assert not set(batch.blocked_child_ids) & set(batch.proposal_change_ids)

    selected = {}
    for change_id in batch.blocked_child_ids:
        proposal = changes.load_proposal(change_id)
        assert proposal.status is ProposalStatus.MANUAL_REVIEW_REQUIRED
        assert proposal.canonical_patch == ()
        assert proposal.proposed_ir is None
        assert proposal.proposed_dax is None
        assert proposal.proposed_snowflake is None
        if proposal.resolution.get("source_measure") in {
            "Iterator Total",
            "Inactive Relationship",
        }:
            selected[proposal.resolution["source_measure"]] = proposal
    assert set(selected) == {"Iterator Total", "Inactive Relationship"}

    for name, proposal in selected.items():
        output = workspace / name.replace(" ", "-")
        result = preview_sync(
            proposal.change_id,
            target_mode="create",
            output_dir=output,
            change_store=changes,
            import_store=imports,
        )
        assert result.exit_code == 3
        assert _files(output) == BLOCKED_SET
        blocked = json.loads(
            (output / "blocked-preview.json").read_text(encoding="utf-8")
        )
        assert blocked["operations"] == []
        assert blocked["executable_artifacts_emitted"] is False
        assert not any(
            word in blocked for word in ("expression", "patch", "candidate")
        )
        queue = json.loads(
            (output / "validation-queue.json").read_text(encoding="utf-8")
        )
        classes = {item["review_class"] for item in queue["findings"]}
        if name == "Iterator Total":
            assert "FORMULA_REVIEW_REQUIRED" in classes
            assert any(
                "DAX_ITERATOR_UNSUPPORTED" in item["reason_codes"]
                for item in queue["findings"]
            )
        else:
            assert "MODEL_RELATIONSHIP_REVIEW_REQUIRED" in classes
            assert any(
                "DAX_INACTIVE_RELATIONSHIP_DEPENDENCY"
                in item["reason_codes"]
                for item in queue["findings"]
            )
        with pytest.raises(WorkflowError):
            approve_change(changes, proposal.change_id, actor="test-user")
        with pytest.raises(WorkflowError):
            apply_change(changes, proposal.change_id)


def test_import_blocker_collision_preflight_publishes_nothing_new(
    workspace: Path,
) -> None:
    first_imports = ImportStore(workspace / "first-imports")
    first_changes = ChangeStore(workspace / "first-changes")
    first_run = create_import_run(
        UNSUPPORTED_IMPORT,
        store=first_imports,
        now=datetime(2026, 7, 18, 19, 0, tzinfo=timezone.utc),
        entropy="00000003",
    )
    first_batch = create_import_proposal_batch(
        first_run.import_id,
        store=first_imports,
        change_store=first_changes,
    )
    collision_id = first_batch.blocked_child_ids[0]

    second_imports = ImportStore(workspace / "second-imports")
    second_changes = ChangeStore(workspace / "second-changes")
    second_run = create_import_run(
        UNSUPPORTED_IMPORT,
        store=second_imports,
        now=datetime(2026, 7, 18, 19, 0, tzinfo=timezone.utc),
        entropy="00000003",
    )
    second_changes.save_proposal(first_changes.load_proposal(collision_id))
    before = _tree_bytes(second_changes.root)
    with pytest.raises(ValueError, match="already exists"):
        create_import_proposal_batch(
            second_run.import_id,
            store=second_imports,
            change_store=second_changes,
        )
    assert second_imports.try_load_proposal_batch(second_run.import_id) is None
    assert _tree_bytes(second_changes.root) == before


def test_terminal_lifecycle_output_conflict_and_target_identity_are_exit_four(
    workspace: Path,
) -> None:
    changes, imports, change_id = _supported(workspace)
    proposal = changes.load_proposal(change_id)
    terminal_store = ChangeStore(workspace / "terminal-changes")
    terminal_store.save_proposal(
        replace(
            proposal,
            status=ProposalStatus.APPROVED,
            approval_state=ApprovalState.APPROVED,
        )
    )
    with pytest.raises(PreviewStateError) as lifecycle:
        preview_sync(
            change_id,
            target_mode="create",
            output_dir=workspace / "terminal",
            change_store=terminal_store,
            import_store=imports,
        )
    assert lifecycle.value.exit_code == 4
    assert lifecycle.value.code == "PREVIEW_LIFECYCLE_CONFLICT"

    existing_output = workspace / "existing-output"
    existing_output.mkdir()
    with pytest.raises(PreviewStateError) as conflict:
        preview_sync(
            change_id,
            target_mode="create",
            output_dir=existing_output,
            change_store=changes,
            import_store=imports,
        )
    assert conflict.value.exit_code == 4

    target = yaml.safe_load(UPDATE_BASELINE.read_text(encoding="utf-8"))
    target["name"] = "CONFLICTING_VIEW"
    conflicting = workspace / "conflicting.yml"
    conflicting.write_text(
        yaml.safe_dump(target, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(PreviewStateError) as identity:
        preview_sync(
            change_id,
            target_mode="update",
            existing_snowflake_yaml=conflicting,
            output_dir=workspace / "identity",
            change_store=changes,
            import_store=imports,
        )
    assert identity.value.exit_code == 4
    assert identity.value.code == "SNOWFLAKE_TARGET_IDENTITY_CONFLICT"


def test_invalid_ids_argument_shapes_and_path_overlap_are_exit_two(
    workspace: Path,
) -> None:
    changes, imports, change_id = _supported(workspace)
    with pytest.raises(PreviewInputError) as unknown:
        preview_sync(
            "chg_20260718T190000Z_00000000",
            target_mode="create",
            output_dir=workspace / "unknown",
            change_store=changes,
            import_store=imports,
        )
    assert unknown.value.exit_code == 2
    with pytest.raises(PreviewInputError):
        preview_sync(
            change_id,
            target_mode="update",
            output_dir=workspace / "missing-target",
            change_store=changes,
            import_store=imports,
        )
    with pytest.raises(PreviewInputError):
        preview_sync(
            change_id,
            target_mode="create",
            existing_snowflake_yaml=UPDATE_BASELINE,
            output_dir=workspace / "forbidden-target",
            change_store=changes,
            import_store=imports,
        )
    with pytest.raises(PreviewInputError):
        preview_sync(
            change_id,
            target_mode="create",
            output_dir=PORTABLE_POWERBI_DEFINITION / "preview-output",
            change_store=changes,
            import_store=imports,
        )


def test_atomic_publish_failure_leaves_no_output(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes, imports, change_id = _supported(workspace)
    output = workspace / "atomic-output"

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated atomic promotion failure")

    monkeypatch.setattr(preview_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        preview_sync(
            change_id,
            target_mode="create",
            output_dir=output,
            change_store=changes,
            import_store=imports,
        )
    assert not output.exists()
    assert not list(workspace.glob(".preview-stage-*"))


def test_preview_result_evidence_is_strict_hash_bound_and_tolerant(
    workspace: Path,
) -> None:
    changes, imports, change_id = _supported(workspace)
    baseline = workspace / "without-evidence"
    preview_sync(
        change_id,
        target_mode="create",
        output_dir=baseline,
        change_store=changes,
        import_store=imports,
    )
    manifest = json.loads(
        (baseline / "manifest.json").read_text(encoding="utf-8")
    )
    subjects = {
        "proposal_sha256": manifest["proposal_sha256"],
        "canonical_baseline_sha256": manifest["canonical_baseline_sha256"],
        "canonical_candidate_sha256": manifest["canonical_candidate_sha256"],
        "powerbi_source_tree_sha256": manifest[
            "source_powerbi_tree_sha256"
        ],
        "powerbi_copy_plan_sha256": manifest["powerbi_copy_plan_sha256"],
        "snowflake_candidate_sha256": manifest["snowflake_candidate_sha256"],
        "snowflake_environment_sha256": manifest[
            "snowflake_environment_sha256"
        ],
        "snowflake_query_pack_sha256": manifest[
            "snowflake_query_pack_sha256"
        ],
    }
    left_rows = [{"coordinates": {"DIM_EVENT.EVENT_ID": "e1"}, "value": 1.0}]
    right_rows = [
        {"coordinates": {"DIM_EVENT.EVENT_ID": "e1"}, "value": 1.0000000005}
    ]
    evidence = {
        "comparison": {
            "blank_null_representation": "JSON_NULL",
            "decimal_absolute_tolerance": 1e-9,
            "decimal_relative_tolerance": 1e-9,
            "integer": "EXACT",
        },
        "profile": "PREVIEW_SYNC_V1",
        "results": [
            {
                "power_bi": {
                    "complete": True,
                    "result_sha256": _sha(_canonical_bytes(left_rows)),
                    "rows": left_rows,
                    "status": "AVAILABLE",
                },
                "slice": "EVENT",
                "snowflake": {
                    "complete": True,
                    "result_sha256": _sha(_canonical_bytes(right_rows)),
                    "rows": right_rows,
                    "status": "AVAILABLE",
                },
                "source_measure": "Valid SBR Finishers",
                "value_type": "DECIMAL",
            }
        ],
        "schema_version": 1,
        "subject_hashes": subjects,
    }
    evidence_path = workspace / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result = preview_sync(
        change_id,
        target_mode="create",
        result_evidence=evidence_path,
        output_dir=workspace / "with-evidence",
        change_store=changes,
        import_store=imports,
    )
    assert result.result_evidence_status == "PASSED"
    assert result.exit_code == 0

    evidence["subject_hashes"]["proposal_sha256"] = "0" * 64
    stale_path = workspace / "stale-evidence.json"
    stale_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(PreviewStateError) as stale:
        preview_sync(
            change_id,
            target_mode="create",
            result_evidence=stale_path,
            output_dir=workspace / "stale",
            change_store=changes,
            import_store=imports,
        )
    assert stale.value.exit_code == 4


def test_required_missing_runtime_evidence_is_blocking_but_keeps_full_preview(
    workspace: Path,
) -> None:
    changes, imports, change_id = _supported(workspace)
    proposal = changes.load_proposal(change_id)
    required_store = ChangeStore(workspace / "required-changes")
    required_store.save_proposal(
        replace(
            proposal,
            resolution={
                **dict(proposal.resolution),
                "runtime_evidence_required": True,
            },
        )
    )
    output = workspace / "required"
    result = preview_sync(
        change_id,
        target_mode="create",
        output_dir=output,
        change_store=required_store,
        import_store=imports,
    )
    assert result.exit_code == 3
    assert _files(output) == FULL_SET
    queue = json.loads(
        (output / "validation-queue.json").read_text(encoding="utf-8")
    )
    assert queue["blocking_count"] > 0
    assert any(
        "REQUIRED_RUNTIME_EVIDENCE_MISSING" in item["reason_codes"]
        for item in queue["findings"]
    )


def test_cli_adapter_returns_zero_and_json_only_changes_stdout(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    changes, imports, change_id = _supported(workspace)
    monkeypatch.delenv("SEMANTIC_AGENT_CHANGE_DIR", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_CHANGE_DIR", changes.root)
    monkeypatch.setattr(cli, "DEFAULT_IMPORT_DIR", imports.root)
    output = workspace / "cli-output"
    exit_code = cli.main(
        [
            "preview-sync",
            change_id,
            "--target-mode",
            "create",
            "--output-dir",
            str(output),
            "--json",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["change_id"] == change_id
    assert payload["artifact_count"] == 9
