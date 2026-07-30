from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import pytest
import yaml

from semantic_poc.agent.change_store import ChangeStore
from semantic_poc.agent.import_store import ImportStore
from semantic_poc.agent.import_workflow import (
    create_import_proposal_batch,
    create_import_run,
)
from semantic_poc.agent.preview_sync import preview_sync
from semantic_poc.review_memory import (
    DEFAULT_REVIEW_MEMORY_ROOT,
    ReviewMemoryError,
    load_review_registry,
    sha256_value,
    suggest_review_for_finding,
)
from semantic_poc.src.models import PROJECT_ROOT


FIXTURES = (
    PROJECT_ROOT / "semantic_poc" / "tests" / "fixtures" / "review_memory_v2"
)
ORIGINAL = (
    PROJECT_ROOT
    / "semantic_poc"
    / "tests"
    / "fixtures"
    / "conversion_benchmark"
    / "b_semantic_traps.SemanticModel"
)
CROSS_OBJECT = FIXTURES / "cross_object.SemanticModel"


@pytest.fixture
def workspace() -> Path:
    path = Path(
        tempfile.mkdtemp(prefix="pytest-review-memory-v2-", dir=PROJECT_ROOT / ".tmp")
    )
    try:
        yield path
    finally:
        resolved = path.resolve()
        assert resolved.is_relative_to((PROJECT_ROOT / ".tmp").resolve())
        shutil.rmtree(resolved)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _finding(workspace: Path, model: Path, entropy: str) -> tuple[dict, Path]:
    root = workspace / entropy
    imports = ImportStore(root / "imports")
    changes = ChangeStore(root / "changes")
    run = create_import_run(
        model,
        store=imports,
        now=datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
        entropy=entropy,
    )
    batch = create_import_proposal_batch(
        run.import_id,
        store=imports,
        change_store=changes,
        now=datetime(2026, 7, 24, 12, 1, tzinfo=timezone.utc),
    )
    proposal = next(
        changes.load_proposal(change_id)
        for change_id in batch.blocked_child_ids
        if changes.load_proposal(change_id).resolution.get("source_measure")
        == "Hours"
    )
    preview = root / "preview"
    preview_sync(
        proposal.change_id,
        target_mode="create",
        output_dir=preview,
        change_store=changes,
        import_store=imports,
    )
    queue = json.loads((preview / "validation-queue.json").read_text(encoding="utf-8"))
    return queue["findings"][0], preview


def _rule(
    finding: dict,
    preview: Path,
    *,
    scope: str,
) -> dict:
    typed = scope == "EXACT_TYPED_PATTERN"
    fixtures = (
        {
            "near_misses": [
                {
                    "fixture_id": path.stem,
                    "path": _relative(path),
                    "sha256": _hash(path),
                }
                for path in sorted(FIXTURES.glob("near_miss_*.yml"))
            ],
            "positive": [
                {
                    "fixture_id": "exact_typed_pattern_positive",
                    "path": _relative(FIXTURES / "exact_typed_pattern_positive.yml"),
                    "sha256": _hash(FIXTURES / "exact_typed_pattern_positive.yml"),
                }
            ],
        }
        if typed
        else None
    )
    queue = preview / "validation-queue.json"
    return {
        "applicability": {
            "canonical_object_id": finding["finding_identity_payload"][
                "canonical_object_id"
            ],
            "parameter_role_constraints": (
                finding["typed_pattern_signature_payload"][
                    "typed_parameter_roles"
                ]
                if typed
                else {}
            ),
            "semantic_signature_payload": finding["semantic_signature_payload"],
            "semantic_signature_sha256": finding["semantic_signature_sha256"],
            "source_identifier": finding["source_identifier"],
            "typed_pattern_signature_payload": finding[
                "typed_pattern_signature_payload"
            ],
            "typed_pattern_signature_sha256": finding[
                "typed_pattern_signature_sha256"
            ],
        },
        "applicability_scope": scope,
        "confirmation_required": True,
        "finding_reason_codes": finding["reason_codes"],
        "lifecycle": "CURRENT",
        "provenance": {
            "actor": "task04_fixture_reviewer",
            "canonical_source_file": finding["canonical_source_file"],
            "concrete_role_bindings": finding["concrete_role_bindings"],
            "decision_id": "review_decision_v2_fixture",
            "decision_path": _relative(FIXTURES / "exact_object_positive.yml"),
            "decision_sha256": _hash(FIXTURES / "exact_object_positive.yml"),
            "evidence_references": [
                {
                    "evidence_id": "evidence_queue",
                    "finding_id": finding["finding_id"],
                    "kind": "PREVIEW_ARTIFACT",
                    "path": _relative(queue),
                    "sha256": _hash(queue),
                }
            ],
            "fixture_evidence": fixtures,
            "rationale": "The fixture establishes an exact deterministic boundary.",
            "recorded_at": "2026-07-24T12:05:00Z",
            "source_snapshot_sha256": finding["bound_input_hashes"][
                "powerbi_source_tree_sha256"
            ],
        },
        "review_class": finding["review_class"],
        "rule_id": (
            "review_v2_exact_typed_pattern_v1"
            if typed
            else "review_v2_exact_object_v1"
        ),
        "schema_version": 2,
        "status": "ACCEPTED",
        "structured_answer": {
            "answer_id": "CONFIRM_TYPED_SEMANTICS",
            "parameters": {
                "semantic_signature_sha256": finding[
                    "semantic_signature_sha256"
                ]
            },
        },
        "superseded_by": None,
        "version": 1,
    }


def _registry(workspace: Path, rule: dict) -> Path:
    root = workspace / ("registry-" + rule["applicability_scope"].casefold())
    accepted = root / "accepted"
    accepted.mkdir(parents=True)
    shutil.copy2(DEFAULT_REVIEW_MEMORY_ROOT / "schema.json", root / "schema.json")
    shutil.copy2(
        DEFAULT_REVIEW_MEMORY_ROOT / "schema-v2.json", root / "schema-v2.json"
    )
    shutil.copy2(
        DEFAULT_REVIEW_MEMORY_ROOT / "registry.schema.json",
        root / "registry.schema.json",
    )
    rule_path = accepted / "rule.yml"
    rule_path.write_text(
        yaml.safe_dump(rule, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    registry = {
        "rules": [
            {
                "lifecycle": "CURRENT",
                "path": "accepted/rule.yml",
                "rule_id": rule["rule_id"],
                "schema_version": 2,
                "sha256": _hash(rule_path),
                "status": "ACCEPTED",
            }
        ],
        "schema_version": 1,
    }
    (root / "registry.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def test_fixture_manifest_binds_every_reviewed_fixture() -> None:
    manifest = json.loads((FIXTURES / "fixture-manifest.json").read_text())
    actual = {
        item.relative_to(FIXTURES).as_posix(): _hash(item)
        for item in FIXTURES.rglob("*")
        if item.is_file() and item.name != "fixture-manifest.json"
    }
    assert actual == {item["path"]: item["sha256"] for item in manifest["files"]}


def test_runtime_registry_contains_only_exact_unchanged_v1_bytes() -> None:
    rules = load_review_registry()
    assert len(rules) == 1
    assert rules[0].schema_version == 1
    assert (
        rules[0].registered_sha256
        == "af996d6a1d96e5f8a75b0b34baf567f0b47788523d2722057d877eef9e451e4a"
    )


def test_modified_or_duplicate_registry_entries_fail_closed(
    workspace: Path,
) -> None:
    finding, preview = _finding(workspace, ORIGINAL, "04040409")
    root = _registry(workspace, _rule(finding, preview, scope="EXACT_OBJECT"))
    registry = json.loads((root / "registry.json").read_text())
    registry["rules"][0]["sha256"] = "0" * 64
    (root / "registry.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ReviewMemoryError, match="stale"):
        load_review_registry(root)

    registry["rules"][0]["sha256"] = _hash(root / "accepted" / "rule.yml")
    duplicate = dict(registry["rules"][0])
    duplicate["rule_id"] = "review_v2_exact_object_v2"
    duplicate["path"] = "accepted/other.yml"
    shutil.copy2(root / "accepted" / "rule.yml", root / "accepted" / "other.yml")
    registry["rules"].append(duplicate)
    (root / "registry.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ReviewMemoryError, match="unique"):
        load_review_registry(root)


def test_stale_v2_provenance_fails_closed(workspace: Path) -> None:
    finding, preview = _finding(workspace, ORIGINAL, "04040410")
    rule = _rule(finding, preview, scope="EXACT_OBJECT")
    rule["provenance"]["decision_sha256"] = "0" * 64
    root = _registry(workspace, rule)
    with pytest.raises(ReviewMemoryError, match="provenance is stale"):
        load_review_registry(root)


def test_schema_v2_exact_object_and_cross_object_typed_pattern_suggest_only(
    workspace: Path,
) -> None:
    original, original_preview = _finding(workspace, ORIGINAL, "04040411")
    cross, _ = _finding(workspace, CROSS_OBJECT, "04040412")
    assert original["source_identifier"] == "Measures[Hours]"
    assert cross["source_identifier"] == "Metrics[Hours]"
    assert (
        original["typed_pattern_signature_sha256"]
        == cross["typed_pattern_signature_sha256"]
    )
    assert (
        original["semantic_signature_sha256"]
        != cross["semantic_signature_sha256"]
    )

    exact_rules = load_review_registry(
        _registry(workspace, _rule(original, original_preview, scope="EXACT_OBJECT"))
    )
    exact = suggest_review_for_finding(original, exact_rules)
    identity_change = suggest_review_for_finding(cross, exact_rules)
    assert exact["match_status"] == "EXACT"
    assert exact["human_confirmation"] == "REQUIRED"
    assert identity_change["reason_code"] == "NO_EXACT_REVIEW_RULE"

    pattern_rules = load_review_registry(
        _registry(
            workspace,
            _rule(original, original_preview, scope="EXACT_TYPED_PATTERN"),
        )
    )
    cross_result = suggest_review_for_finding(cross, pattern_rules)
    assert cross_result["match_status"] == "EXACT"
    assert cross_result["approval_state"] == "NOT_REQUESTED"
    assert cross_result["application_state"] == "NOT_REQUESTED"


def test_v1_near_match_and_typed_role_constraint_violation_fail_closed(
    workspace: Path,
) -> None:
    original, original_preview = _finding(workspace, ORIGINAL, "04040413")
    v1_rules = load_review_registry()
    near = copy.deepcopy(original)
    near["semantic_signature_payload"]["filter_context"]["behavior"] = "REPLACE"
    near["semantic_signature_sha256"] = sha256_value(
        near["semantic_signature_payload"]
    )
    assert (
        suggest_review_for_finding(near, v1_rules)["reason_code"]
        == "NO_EXACT_REVIEW_RULE"
    )

    typed_rules = load_review_registry(
        _registry(
            workspace,
            _rule(original, original_preview, scope="EXACT_TYPED_PATTERN"),
        )
    )
    cross, _ = _finding(workspace, CROSS_OBJECT, "04040414")
    cross["concrete_role_bindings"]["$SOURCE_FIELD"]["table"] = "wrong_context"
    assert (
        suggest_review_for_finding(cross, typed_rules)["reason_code"]
        == "NO_EXACT_REVIEW_RULE"
    )


@pytest.mark.parametrize(
    ("component", "mutator"),
    [
        (
            "expression",
            lambda value: value["typed_pattern_signature_payload"][
                "typed_expression"
            ].__setitem__("operator", "MULTIPLY"),
        ),
        (
            "aggregation",
            lambda value: value["typed_pattern_signature_payload"][
                "typed_expression"
            ].__setitem__("aggregation", "AVG"),
        ),
        (
            "parameter_role",
            lambda value: value["typed_pattern_signature_payload"][
                "typed_parameter_roles"
            ]["$SOURCE_FIELD"].__setitem__("semantic_role", "DIMENSION_FIELD"),
        ),
        (
            "units",
            lambda value: value["typed_pattern_signature_payload"].__setitem__(
                "target_units", "minutes"
            ),
        ),
        (
            "population",
            lambda value: value["typed_pattern_signature_payload"][
                "population"
            ].__setitem__("kind", "ALL_ROWS"),
        ),
        (
            "grain",
            lambda value: value["typed_pattern_signature_payload"]["grain"].__setitem__(
                "table", "$EVENT_TABLE"
            ),
        ),
        (
            "filter_context",
            lambda value: value["typed_pattern_signature_payload"][
                "filter_context"
            ].__setitem__("behavior", "REPLACE"),
        ),
        (
            "relationship_context",
            lambda value: value["typed_pattern_signature_payload"][
                "relationship_context"
            ].__setitem__("mode", "INCLUDE_INACTIVE"),
        ),
    ],
)
def test_each_one_field_semantic_near_miss_requires_manual_review(
    workspace: Path,
    component: str,
    mutator: object,
) -> None:
    original, preview = _finding(workspace, ORIGINAL, "04040421")
    cross, _ = _finding(workspace, CROSS_OBJECT, "04040422")
    rules = load_review_registry(
        _registry(workspace, _rule(original, preview, scope="EXACT_TYPED_PATTERN"))
    )
    changed = copy.deepcopy(cross)
    mutator(changed)
    changed["typed_pattern_signature_sha256"] = sha256_value(
        changed["typed_pattern_signature_payload"]
    )
    result = suggest_review_for_finding(changed, rules)
    assert result["status"] == "MANUAL_REVIEW_REQUIRED", component
    assert result["reason_code"] == "NO_EXACT_REVIEW_RULE", component
