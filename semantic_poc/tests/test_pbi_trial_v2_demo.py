from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from semantic_poc.agent.pbi_trial_v2_demo import (
    CONFIRMED,
    POTENTIAL,
    PRESENTER_RELATIVE_PATH,
    render_presenter,
    validate_demo_assertions,
)
from semantic_poc.agent.pbi_trial_v2_fixture import (
    EXPECTED_COUNTS,
    REVIEWED_RAW_STRUCTURE_SHA256,
    validate_committed_fixture,
)
from semantic_poc.agent.powerbi_snowflake_audit import audit_powerbi_snowflake
from semantic_poc.src.models import PROJECT_ROOT


FIXTURE_ROOT = (
    PROJECT_ROOT / "semantic_poc" / "benchmark" / "pbi_trial_v2" / "fixtures"
)
FIXTURE_MODEL = FIXTURE_ROOT / "pbi_trial.SemanticModel"
MANIFEST = FIXTURE_ROOT / "fixture-manifest.json"
SNOWFLAKE = PROJECT_ROOT / "pbit" / "snowflake_semantic_view" / "pbi_trial.yaml"
SPEC = PROJECT_ROOT / "semantic_poc" / "benchmark" / "pbi_trial_v2" / "measure-cases.yml"


def golden_audit():
    return audit_powerbi_snowflake(
        model_dir=FIXTURE_MODEL,
        snowflake_yaml=SNOWFLAKE,
        benchmark_spec=SPEC,
        repository_root=PROJECT_ROOT,
    )


def test_fixture_is_sanitized_portable_and_fingerprint_bound() -> None:
    manifest = validate_committed_fixture(
        fixture_model=FIXTURE_MODEL,
        manifest_path=MANIFEST,
        repository_root=PROJECT_ROOT,
    )
    assert manifest["inventory_counts"] == EXPECTED_COUNTS
    assert manifest["prior_reviewed_raw_structure_sha256"] == REVIEWED_RAW_STRUCTURE_SHA256
    assert manifest["fixture_raw_structure_sha256"] != REVIEWED_RAW_STRUCTURE_SHA256
    assert (
        manifest["source_connection_normalized_semantic_structure_sha256"]
        == manifest["connection_normalized_semantic_structure_sha256"]
        == manifest["fixture_raw_structure_sha256"]
    )
    paths = set(manifest["included_paths"])
    assert ".pbi/cache.abf" not in paths
    assert "diagramLayout.json" not in paths
    assert paths == {
        item.relative_to(FIXTURE_MODEL).as_posix()
        for item in FIXTURE_MODEL.rglob("*")
        if item.is_file()
    }
    fixture_text = "\n".join(
        item.read_text(encoding="utf-8")
        for item in FIXTURE_MODEL.rglob("*.tmdl")
    )
    assert "SANITIZED_ACCOUNT" in fixture_text
    assert "SANITIZED_WAREHOUSE" in fixture_text
    assert "SANITIZED_DATABASE" in fixture_text
    assert "SANITIZED_SCHEMA" in fixture_text


def test_fixture_check_is_portable_and_does_not_read_ignored_source() -> None:
    manifest = validate_committed_fixture(
        fixture_model=FIXTURE_MODEL,
        manifest_path=MANIFEST,
        repository_root=PROJECT_ROOT,
    )
    assert manifest["fixture_model_tree_sha256"]


def test_demo_assertions_presenter_and_ignored_path_independence() -> None:
    audit = golden_audit()
    validate_demo_assertions(audit)
    confirmed = {
        item.source["name"]
        for item in audit.measures
        if item.fidelity_status == "CONFIRMED_INCORRECT"
    }
    potential = {
        item.source["name"]
        for item in audit.measures
        if item.fidelity_status == "POTENTIALLY_INCORRECT"
    }
    assert confirmed == CONFIRMED
    assert potential == POTENTIAL
    presenter = render_presenter(audit)
    assert (PROJECT_ROOT / PRESENTER_RELATIVE_PATH).read_text(encoding="utf-8") == presenter
    for line in presenter.splitlines():
        if line.startswith("["):
            assert sum(line.startswith(f"[{tag}]") for tag in ("PROVEN", "OBSERVED", "NOT_PROVEN")) == 1
    for name in ("Result Rows", "Split Coverage Rate", "NC - Bike Time Hours Divisor 60"):
        finding = next(item for item in audit.measures if item.source["name"] == name)
        assert finding.finding_ids[0] in presenter
    executable_files = (
        PROJECT_ROOT / "semantic_poc" / "run_pbi_trial_v2_audit.py",
        PROJECT_ROOT / "semantic_poc" / "tests" / "test_powerbi_snowflake_audit.py",
        PROJECT_ROOT / "semantic_poc" / "tests" / "test_safe_ir_extensions.py",
    )
    assert all(
        ' / "pbi" / "pbi_trial.SemanticModel"' not in path.read_text(encoding="utf-8")
        for path in executable_files
    )


def test_fixed_demo_runs_in_controlled_copy_without_ignored_source(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    shutil.copytree(
            PROJECT_ROOT / "semantic_poc",
            root / "semantic_poc",
            ignore=shutil.ignore_patterns(
                "__pycache__", "agent_sessions", "changes", "output", "tests"
            ),
        )
    shutil.copytree(PROJECT_ROOT / "models", root / "models")
    shutil.copytree(PROJECT_ROOT / "pbit", root / "pbit")
    (root / "docs" / "codex" / "pbi-snowflake-poc" / "demo").mkdir(
        parents=True
    )
    shutil.copy2(
        PROJECT_ROOT / "docs" / "POWERBI_SNOWFLAKE_V2_CONVERSION_FINDINGS.md",
        root / "docs" / "POWERBI_SNOWFLAKE_V2_CONVERSION_FINDINGS.md",
    )
    shutil.copy2(
        PROJECT_ROOT / PRESENTER_RELATIVE_PATH,
        root / PRESENTER_RELATIVE_PATH,
    )
    assert not (root / "pbi" / "pbi_trial.SemanticModel").exists()
    result = subprocess.run(
        [
            sys.executable,
            "semantic_poc/run_pbi_trial_v2_audit.py",
            "--check",
            "--json",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verdict"] == "CONVERSION_GAP_DEMO_ACCEPTED"
