from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from semantic_poc import end_to_end_demo
from semantic_poc.agent import workflow
from semantic_poc.end_to_end_demo import (
    MANAGED_BY,
    OUTPUT_FILES,
    SUMMARY_LINES,
    EndToEndDemoError,
    clean_end_to_end_output,
)
from semantic_poc.src.models import DBT_SEMANTIC_YAML, PROJECT_ROOT


TEST_ROOT = PROJECT_ROOT / ".tmp" / "pytest-task9-end-to-end"
WRAPPER = PROJECT_ROOT / "semantic_poc" / "run_end_to_end_demo.py"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_tree_hashes_use_portable_case_sensitive_order(tmp_path: Path) -> None:
    (tmp_path / "Z.txt").write_bytes(b"upper")
    (tmp_path / "a.txt").write_bytes(b"lower")
    expected = hashlib.sha256()
    for relative, payload in (("Z.txt", b"upper"), ("a.txt", b"lower")):
        expected.update(relative.encode("utf-8"))
        expected.update(b"\0")
        expected.update(payload)
    assert end_to_end_demo._tree_hash(tmp_path) == expected.hexdigest()
    assert workflow._proposal_snapshot_tree_hash(tmp_path) == expected.hexdigest()


@pytest.fixture(scope="module")
def end_to_end_bundle() -> tuple[Path, subprocess.CompletedProcess[str], str]:
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT)
    TEST_ROOT.mkdir(parents=True)
    output = TEST_ROOT / "bundle"
    seeded = TEST_ROOT / "seeded-user-records"
    (seeded / "discarded").mkdir(parents=True)
    (seeded / "rolled-back").mkdir(parents=True)
    (seeded / "discarded" / "record.json").write_text("{\"state\":\"DISCARDED\"}\n", encoding="utf-8")
    (seeded / "rolled-back" / "record.json").write_text("{\"state\":\"ROLLED_BACK\"}\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["SEMANTIC_AGENT_CHANGE_DIR"] = str(seeded)
    canonical_before = _hash(DBT_SEMANTIC_YAML)
    completed = subprocess.run(
        [sys.executable, str(WRAPPER), "--clean", "--check", "--output-dir", str(output)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    yield output, completed, canonical_before
    if output.exists():
        clean_end_to_end_output(output)
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT)


def test_full_command_from_repository_root_prints_exact_acceptance_summary(
    end_to_end_bundle: tuple[Path, subprocess.CompletedProcess[str], str]
) -> None:
    _, completed, _ = end_to_end_bundle
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.splitlines() == list(SUMMARY_LINES)


def test_bundle_contract_review_memory_equivalence_and_drift(
    end_to_end_bundle: tuple[Path, subprocess.CompletedProcess[str], str]
) -> None:
    output, _, _ = end_to_end_bundle
    actual_files = {item.relative_to(output).as_posix() for item in output.rglob("*") if item.is_file()}
    assert actual_files == OUTPUT_FILES
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["managed_by"] == MANAGED_BY
    assert manifest["blocking_finding"] == "UNIT_CONVERSION_MISMATCH"
    assert manifest["human_review"] == "ACCEPTED"
    assert manifest["review_memory"] == "RECORDED"
    assert manifest["safe_reuse"] == "REVIEW_RULE_SUGGESTED"
    assert manifest["human_confirmation"] == "REQUIRED"
    assert manifest["corrected_equivalence"] == {"status": "PASSED", "passed": 5, "total": 5}
    assert manifest["changed_canonical_metrics"] == ["valid_sbr_finishers"]
    assert manifest["power_bi_target_changes"] == 1
    assert manifest["snowflake_target_changes"] == 1
    assert manifest["unexpected_target_only_drift"] == 0
    assert manifest["unrelated_object_changes"] == 0
    assert manifest["cross_target_semantic_drift"] == 0
    assert manifest["synchronization_status"] == "ALIGNED"
    assert manifest["source_modified"] is False
    assert manifest["deployment_performed"] is False
    assert manifest["verdict"] == "END_TO_END_POC_ACCEPTED"
    assert "UNIT_CONVERSION_MISMATCH" in (output / "initial-conversion-findings.md").read_text(encoding="utf-8")
    decision = yaml.safe_load((output / "accepted-review-decision.yml").read_text(encoding="utf-8"))
    rule = yaml.safe_load((output / "review-memory-entry.yml").read_text(encoding="utf-8"))
    assert decision["status"] == "ACCEPTED"
    assert rule["rule_id"] == "review_unit_conversion_seconds_to_hours_v1"
    assert rule["approval_provenance"]["decision_sha256"] == _hash(
        output / "accepted-review-decision.yml"
    )


def test_manifest_binds_every_artifact_and_protected_sources_are_unchanged(
    end_to_end_bundle: tuple[Path, subprocess.CompletedProcess[str], str]
) -> None:
    output, _, canonical_before = end_to_end_bundle
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["output_hashes"]) == OUTPUT_FILES - {"manifest.json"}
    for name, expected in manifest["output_hashes"].items():
        assert _hash(output / name) == expected
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    assert hashes["source_before"] == hashes["source_after"]
    assert canonical_before == _hash(DBT_SEMANTIC_YAML)
    assert hashes["source_after"]["models/semantic/triathlon_semantic.yml"] == canonical_before


def test_guarded_cleanup_refuses_modified_bundle() -> None:
    output = TEST_ROOT / "tampered-bundle"
    completed = subprocess.run(
        [sys.executable, str(WRAPPER), "--output-dir", str(output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    (output / "executive-summary.md").write_text("tampered\n", encoding="utf-8", newline="\n")
    try:
        with pytest.raises(EndToEndDemoError, match="cleanup was refused"):
            clean_end_to_end_output(output)
    finally:
        if output.exists():
            shutil.rmtree(output)
