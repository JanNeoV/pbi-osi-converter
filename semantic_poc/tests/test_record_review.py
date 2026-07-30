from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import pytest
import yaml

from semantic_poc.agent import cli
from semantic_poc.agent.change_store import ChangeStore
from semantic_poc.agent.import_store import ImportStore
from semantic_poc.agent.import_workflow import (
    create_import_proposal_batch,
    create_import_run,
)
from semantic_poc.agent.preview_sync import PORTABLE_POWERBI_DEFINITION, preview_sync
from semantic_poc.agent.proposal_engine import propose_change
from semantic_poc.agent.proposal_models import ProposalStatus
from semantic_poc.agent.review_recording import (
    ReviewInputError,
    ReviewStateError,
    record_review,
    suggest_review,
)
from semantic_poc.agent.schemas import MetricChangeRequest
from semantic_poc.review_memory import (
    DEFAULT_REVIEW_MEMORY_ROOT,
    load_review_registry,
)
from semantic_poc.src.models import PROJECT_ROOT


MODEL = (
    PROJECT_ROOT
    / "semantic_poc"
    / "tests"
    / "fixtures"
    / "conversion_benchmark"
    / "b_semantic_traps.SemanticModel"
)
RULE = (
    DEFAULT_REVIEW_MEMORY_ROOT
    / "accepted"
    / "unit_conversion_seconds_to_hours.yml"
)
REQUEST = (
    PROJECT_ROOT
    / "semantic_poc"
    / "examples"
    / "requests"
    / "valid_sbr_finishers_add_filter.json"
)
CANONICAL = PROJECT_ROOT / "models" / "semantic" / "triathlon_semantic.yml"


@pytest.fixture
def workspace() -> Path:
    path = Path(
        tempfile.mkdtemp(prefix="pytest-record-review-", dir=PROJECT_ROOT / ".tmp")
    )
    try:
        yield path
    finally:
        resolved = path.resolve()
        assert resolved.is_relative_to((PROJECT_ROOT / ".tmp").resolve())
        shutil.rmtree(resolved)


@pytest.fixture
def hours_preview(workspace: Path) -> Path:
    imports = ImportStore(workspace / "imports")
    changes = ChangeStore(workspace / "changes")
    run = create_import_run(
        MODEL,
        store=imports,
        now=datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc),
        entropy="04040404",
    )
    batch = create_import_proposal_batch(
        run.import_id,
        store=imports,
        change_store=changes,
        now=datetime(2026, 7, 24, 10, 1, tzinfo=timezone.utc),
    )
    proposal = next(
        changes.load_proposal(change_id)
        for change_id in batch.blocked_child_ids
        if changes.load_proposal(change_id).resolution.get("source_measure")
        == "Hours"
    )
    output = workspace / "preview"
    result = preview_sync(
        proposal.change_id,
        target_mode="create",
        output_dir=output,
        change_store=changes,
        import_store=imports,
    )
    assert result.exit_code == 3
    return output


@pytest.fixture
def supported_preview(workspace: Path) -> Path:
    changes = ChangeStore(workspace / "supported-changes")
    imports = ImportStore(workspace / "supported-imports")
    request = MetricChangeRequest.from_dict(
        json.loads(REQUEST.read_text(encoding="utf-8"))
    )
    proposal = propose_change(
        request, powerbi_definition_dir=PORTABLE_POWERBI_DEFINITION
    )
    assert proposal.status is ProposalStatus.PROPOSED
    changes.save_proposal(proposal)
    output = workspace / "supported-preview"
    result = preview_sync(
        proposal.change_id,
        target_mode="create",
        output_dir=output,
        change_store=changes,
        import_store=imports,
    )
    assert result.exit_code == 0
    return output


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _decision(preview: Path, *, deferred: bool = False) -> dict[str, object]:
    manifest = preview / "manifest.json"
    queue_path = preview / "validation-queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    finding = queue["findings"][0]
    evidence = [
        {
            "evidence_id": "evidence_queue",
            "finding_id": finding["finding_id"],
            "kind": "PREVIEW_ARTIFACT",
            "path": _relative(queue_path),
            "sha256": _hash(queue_path),
        }
    ]
    if deferred:
        answer = {
            "answer_id": "DEFER_MANUAL_REVIEW",
            "parameters": {"reason_code": "REVIEW_DEFERRED"},
        }
        suggested = {}
    else:
        suggestion = suggest_review(
            preview, finding_id=finding["finding_id"]
        ).evaluations[0]
        answer = suggestion["permitted_structured_answer"]
        evidence.append(
            {
                "evidence_id": "evidence_rule",
                "finding_id": finding["finding_id"],
                "kind": "REVIEW_RULE",
                "path": _relative(RULE),
                "sha256": _hash(RULE),
            }
        )
        suggested = {
            "suggested_rule": {
                "rule_id": suggestion["registered_rule_id"],
                "sha256": suggestion["registered_rule_sha256"],
            }
        }
    return {
        "actor": "task04_reviewer",
        "application_state": "NOT_REQUESTED",
        "approval_state": "NOT_REQUESTED",
        "bound_input_hashes": finding["bound_input_hashes"],
        "decision_id": (
            "review_decision_hours_deferred"
            if deferred
            else "review_decision_hours_confirmed"
        ),
        "deployment_authorized": False,
        "evidence_references": evidence,
        "finding_id": finding["finding_id"],
        "human_confirmation": "RECORDED",
        "preview_id": queue["preview_id"],
        "preview_manifest_sha256": _hash(manifest),
        "propose_review_rule": False,
        "rationale": (
            "Review remains deferred pending compiler support."
            if deferred
            else "The exact registered unit rule remains applicable."
        ),
        "recorded_at": "2026-07-24T10:05:00Z",
        "schema_version": 1,
        "selected_answer": answer,
        **suggested,
    }


def _write_decision(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def _metadata_rule_decision(preview: Path) -> dict[str, object]:
    manifest = preview / "manifest.json"
    queue_path = preview / "validation-queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    finding = next(
        item
        for item in queue["findings"]
        if item["reason_codes"] == ["SNOWFLAKE_FORMAT_METADATA_NOT_REPRESENTABLE"]
    )
    confirm = next(
        branch
        for branch in finding["allowed_answer_schema"]["oneOf"]
        if branch["properties"]["answer_id"].get("const") == "CONFIRM_METADATA"
    )
    value_sha256 = confirm["properties"]["parameters"]["properties"][
        "value_sha256"
    ]["const"]
    return {
        "actor": "task04_reviewer",
        "application_state": "NOT_REQUESTED",
        "approval_state": "NOT_REQUESTED",
        "bound_input_hashes": finding["bound_input_hashes"],
        "decision_id": "review_decision_metadata_rule",
        "deployment_authorized": False,
        "evidence_references": [
            {
                "evidence_id": "evidence_canonical",
                "finding_id": finding["finding_id"],
                "kind": "MODEL_METADATA",
                "path": _relative(CANONICAL),
                "sha256": _hash(CANONICAL),
            },
            {
                "evidence_id": "evidence_queue",
                "finding_id": finding["finding_id"],
                "kind": "PREVIEW_ARTIFACT",
                "path": _relative(queue_path),
                "sha256": _hash(queue_path),
            },
        ],
        "finding_id": finding["finding_id"],
        "human_confirmation": "RECORDED",
        "preview_id": queue["preview_id"],
        "preview_manifest_sha256": _hash(manifest),
        "propose_review_rule": True,
        "rationale": "The canonical metadata and exact preview finding were reviewed.",
        "recorded_at": "2026-07-24T10:05:00Z",
        "requested_rule_scope": "EXACT_OBJECT",
        "schema_version": 1,
        "selected_answer": {
            "answer_id": "CONFIRM_METADATA",
            "parameters": {"value_sha256": value_sha256},
        },
    }


def test_parser_exposes_exact_review_surfaces() -> None:
    parser = cli.build_parser()
    suggested = parser.parse_args(
        [
            "suggest-review",
            "--preview-dir",
            ".tmp/preview",
            "--finding-id",
            "fnd_" + "0" * 24,
            "--json",
        ]
    )
    assert suggested.command == "suggest-review"
    assert suggested.finding_id == "fnd_" + "0" * 24
    recorded = parser.parse_args(
        [
            "record-review",
            "--preview-dir",
            ".tmp/preview",
            "--decision-file",
            ".tmp/decision.yml",
            "--output-dir",
            ".tmp/review",
        ]
    )
    assert recorded.command == "record-review"


def test_v1_rule_suggests_exact_hours_read_only_and_unknown_id_is_input_error(
    hours_preview: Path,
) -> None:
    before = {
        item.name: item.read_bytes()
        for item in hours_preview.iterdir()
        if item.is_file()
    }
    result = suggest_review(hours_preview)
    assert result.exit_code == 0
    assert len(result.evaluations) == 1
    suggestion = result.evaluations[0]
    assert suggestion["match_status"] == "EXACT"
    assert (
        suggestion["registered_rule_id"]
        == "review_unit_conversion_seconds_to_hours_v1"
    )
    assert suggestion["human_confirmation"] == "REQUIRED"
    assert before == {
        item.name: item.read_bytes()
        for item in hours_preview.iterdir()
        if item.is_file()
    }
    with pytest.raises(ReviewInputError, match="Unknown preview finding"):
        suggest_review(hours_preview, finding_id="fnd_" + "0" * 24)


def test_cli_suggestion_exit_codes_are_exact(
    hours_preview: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "suggest-review",
                "--preview-dir",
                _relative(hours_preview),
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "REVIEW_RULE_SUGGESTED"
    assert (
        cli.main(
            [
                "suggest-review",
                "--preview-dir",
                _relative(hours_preview),
                "--finding-id",
                "fnd_" + "0" * 24,
                "--json",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["error"]["code"] == (
        "REVIEW_FINDING_NOT_FOUND"
    )


def test_cli_suggestion_returns_manual_and_stale_exit_codes(
    workspace: Path,
    hours_preview: Path,
    supported_preview: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "suggest-review",
                "--preview-dir",
                _relative(supported_preview),
                "--json",
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out)["status"] == (
        "MANUAL_REVIEW_REQUIRED"
    )

    stale = workspace / "stale-preview"
    shutil.copytree(hours_preview, stale)
    queue = stale / "validation-queue.json"
    queue.write_bytes(queue.read_bytes() + b"\n")
    assert (
        cli.main(
            [
                "suggest-review",
                "--preview-dir",
                _relative(stale),
                "--json",
            ]
        )
        == 4
    )
    assert json.loads(capsys.readouterr().out)["error"]["code"] == (
        "PREVIEW_ARTIFACT_STALE"
    )


def test_record_registered_rule_confirmation_is_deterministic_and_activation_free(
    workspace: Path,
    hours_preview: Path,
) -> None:
    decision_file = workspace / "decision.yml"
    _write_decision(decision_file, _decision(hours_preview))
    first = record_review(
        hours_preview,
        decision_file=decision_file,
        output_dir=workspace / "review-a",
    )
    second = record_review(
        hours_preview,
        decision_file=decision_file,
        output_dir=workspace / "review-b",
    )
    assert first.exit_code == second.exit_code == 0
    assert first.status == "REVIEW_RECORDED"
    expected = {"manifest.json", "review-decision.yml", "review-session.md"}
    assert {item.name for item in (workspace / "review-a").iterdir()} == expected
    assert (workspace / "review-a" / "review-decision.yml").read_bytes() == (
        workspace / "review-b" / "review-decision.yml"
    ).read_bytes()
    manifest = json.loads(
        (workspace / "review-a" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["source_modified"] is False
    assert manifest["approval_performed"] is False
    assert manifest["application_performed"] is False
    assert manifest["deployment_performed"] is False
    normalized = yaml.safe_load(
        (workspace / "review-a" / "review-decision.yml").read_text(
            encoding="utf-8"
        )
    )
    assert normalized["resolved_structured_operation"]["scale_divisor"] == "3600"
    assert normalized["approval_state"] == "NOT_REQUESTED"


def test_valid_deferred_review_records_and_returns_manual_review(
    workspace: Path,
    hours_preview: Path,
) -> None:
    decision_file = workspace / "deferred.yml"
    _write_decision(decision_file, _decision(hours_preview, deferred=True))
    result = record_review(
        hours_preview,
        decision_file=decision_file,
        output_dir=workspace / "deferred-review",
    )
    assert result.exit_code == 3
    assert result.status == "MANUAL_REVIEW_REQUIRED"
    cli_output = workspace / "deferred-cli"
    assert (
        cli.main(
            [
                "record-review",
                "--preview-dir",
                _relative(hours_preview),
                "--decision-file",
                _relative(decision_file),
                "--output-dir",
                _relative(cli_output),
                "--json",
            ]
        )
        == 3
    )


def test_extra_field_stale_hash_and_existing_output_fail_closed(
    workspace: Path,
    hours_preview: Path,
) -> None:
    value = _decision(hours_preview)
    value["executable_sql"] = "select 1"
    decision_file = workspace / "invalid.yml"
    _write_decision(decision_file, value)
    with pytest.raises(ReviewInputError, match="fails schema"):
        record_review(
            hours_preview,
            decision_file=decision_file,
            output_dir=workspace / "unused",
        )

    stale = _decision(hours_preview)
    stale["preview_manifest_sha256"] = "0" * 64
    _write_decision(decision_file, stale)
    with pytest.raises(ReviewStateError, match="stale"):
        record_review(
            hours_preview,
            decision_file=decision_file,
            output_dir=workspace / "unused",
        )

    valid = _decision(hours_preview)
    _write_decision(decision_file, valid)
    existing = workspace / "existing"
    existing.mkdir()
    with pytest.raises(ReviewStateError, match="fresh"):
        record_review(
            hours_preview,
            decision_file=decision_file,
            output_dir=existing,
        )


def test_exact_object_rule_proposal_is_deterministic_and_remains_unregistered(
    workspace: Path,
    supported_preview: Path,
) -> None:
    registry_before = (
        DEFAULT_REVIEW_MEMORY_ROOT / "registry.json"
    ).read_bytes()
    decision_file = workspace / "metadata-rule.yml"
    _write_decision(decision_file, _metadata_rule_decision(supported_preview))
    first = workspace / "metadata-review-a"
    second = workspace / "metadata-review-b"
    first_result = record_review(
        supported_preview,
        decision_file=decision_file,
        output_dir=first,
    )
    second_result = record_review(
        supported_preview,
        decision_file=decision_file,
        output_dir=second,
    )
    assert first_result.exit_code == second_result.exit_code == 0
    assert first_result.proposed_rule is True
    assert {item.name for item in first.iterdir()} == {
        "manifest.json",
        "review-decision.yml",
        "review-rule.proposed.yml",
        "review-session.md",
    }
    for name in (
        "manifest.json",
        "review-decision.yml",
        "review-rule.proposed.yml",
        "review-session.md",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    proposed = yaml.safe_load(
        (first / "review-rule.proposed.yml").read_text(encoding="utf-8")
    )
    assert proposed["status"] == "PROPOSED"
    assert proposed["confirmation_required"] is True
    assert proposed["applicability_scope"] == "EXACT_OBJECT"
    assert (
        DEFAULT_REVIEW_MEMORY_ROOT / "registry.json"
    ).read_bytes() == registry_before
    assert all(
        rule.rule_id != proposed["rule_id"]
        for rule in load_review_registry()
    )


@pytest.mark.parametrize(
    "answer",
    [
        {
            "answer_id": "DEFER_MANUAL_REVIEW",
            "parameters": {"reason_code": "REVIEW_DEFERRED"},
        },
        {
            "answer_id": "REJECT_CHANGE",
            "parameters": {"reason_code": "SEMANTIC_CHANGE_REJECTED"},
        },
    ],
)
def test_deferred_and_rejected_decisions_cannot_propose_rules(
    workspace: Path,
    supported_preview: Path,
    answer: dict[str, object],
) -> None:
    value = _metadata_rule_decision(supported_preview)
    value["selected_answer"] = answer
    value["decision_id"] = "review_decision_ineligible_rule"
    decision_file = workspace / "ineligible-rule.yml"
    _write_decision(decision_file, value)
    output = workspace / "ineligible-output"
    with pytest.raises(ReviewInputError, match="not eligible"):
        record_review(
            supported_preview,
            decision_file=decision_file,
            output_dir=output,
        )
    assert not output.exists()
