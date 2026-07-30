from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from semantic_poc import run_guided_sync_demo as demo
from semantic_poc.agent import proposal_engine
from semantic_poc.src import models


REPO_ROOT = Path(__file__).resolve().parents[2]


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def test_unchecked_bundle_is_evidence_derived_and_hash_bound(tmp_path: Path) -> None:
    output = REPO_ROOT / ".tmp" / "pytest-guided-sync-demo" / tmp_path.name / "bundle"
    try:
        assert demo.main(["--output-dir", _relative(output)]) == 0
        assert {item.name for item in output.iterdir()} == demo.TOP_LEVEL
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["verdict"] == "INCREMENTAL_SYNC_POC_ACCEPTED"
        assert manifest["source_modified"] is False
        assert manifest["component_results"]["supported"]["target_change_count"] == 1
        assert manifest["component_results"]["review_memory"]["recorded_status"] == "REVIEW_RECORDED"
        assert manifest["component_results"]["stale"]["reason_code"] == "PREVIEW_INPUT_STALE"
        assert manifest["component_results"]["blocked"]["reason_code"] == "DAX_INACTIVE_RELATIONSHIP_DEPENDENCY"
        for relative, digest in manifest["files"].items():
            assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == digest
        blocked = json.loads((output / "blocked-preview" / "blocked-preview.json").read_text(encoding="utf-8"))
        assert blocked["executable_artifacts_emitted"] is False
        assert (output / "create-preview" / "snowflake-semantic-view.candidate.yml").read_bytes() == (
            output / "update-preview" / "snowflake-semantic-view.candidate.yml"
        ).read_bytes()
    finally:
        shutil.rmtree(output.parent, ignore_errors=True)


def test_checked_runner_matches_committed_golden(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main(["--clean", "--check"]) == 0
    assert capsys.readouterr().out.strip().endswith("INCREMENTAL_SYNC_POC_ACCEPTED")


def test_tree_difference_reports_portable_paths_and_hashes() -> None:
    detail = demo._tree_difference(
        {"changed.txt": b"actual", "extra.txt": b"extra"},
        {"changed.txt": b"expected", "missing.txt": b"missing"},
    )
    assert detail == (
        "changed.txt:"
        f"{hashlib.sha256(b'actual').hexdigest()}!="
        f"{hashlib.sha256(b'expected').hexdigest()}"
        '[line=1,actual="actual",expected="expected"], '
        "extra.txt:unexpected, missing.txt:missing"
    )


def test_source_tree_hash_uses_portable_case_sensitive_order(tmp_path: Path) -> None:
    (tmp_path / "Z.txt").write_bytes(b"upper")
    (tmp_path / "a.txt").write_bytes(b"lower")
    expected = hashlib.sha256()
    for relative, payload in (("Z.txt", b"upper"), ("a.txt", b"lower")):
        expected.update(relative.encode("utf-8"))
        expected.update(b"\0")
        expected.update(payload)
    assert proposal_engine._tree_hash(tmp_path) == expected.hexdigest()


def test_cleanup_refuses_tampered_scratch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scratch = tmp_path / "guided-sync-demo"
    child = scratch / "unknown"
    child.mkdir(parents=True)
    (child / "unexpected.txt").write_text("tampered", encoding="utf-8")
    monkeypatch.setattr(demo, "SCRATCH", scratch)
    with pytest.raises(demo.DemoError) as raised:
        demo._safe_clean()
    assert raised.value.reason == "SCRATCH_TREE_UNSAFE"
    assert (child / "unexpected.txt").is_file()


def test_defaults_use_tracked_legacy_fixture() -> None:
    assert models.PBI_DEFINITION_DIR.is_relative_to(REPO_ROOT / "semantic_poc" / "fixtures")
    assert "legacy_triathlon_pbi_model" in str(models.PBI_DEFINITION_DIR.relative_to(REPO_ROOT))
    assert "pbi_trial.SemanticModel" not in str(models.PBI_DEFINITION_DIR.relative_to(REPO_ROOT))
