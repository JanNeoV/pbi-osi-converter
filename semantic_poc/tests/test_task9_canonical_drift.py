from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from semantic_poc.agent.canonical_apply import render_candidate
from semantic_poc.agent.proposal_engine import propose_change
from semantic_poc.agent.schemas import MetricChangeRequest
from semantic_poc.canonical_drift import check_canonical_drift
from semantic_poc.src.models import DBT_SEMANTIC_YAML, PROJECT_ROOT


REQUEST = (
    PROJECT_ROOT
    / "semantic_poc"
    / "examples"
    / "requests"
    / "valid_sbr_finishers_add_filter.json"
)
TASK_ROOT = PROJECT_ROOT / ".tmp" / "pytest-task9-drift"


@pytest.fixture()
def canonical_pair() -> tuple[Path, Path]:
    if TASK_ROOT.exists():
        shutil.rmtree(TASK_ROOT)
    TASK_ROOT.mkdir(parents=True)
    baseline = TASK_ROOT / "baseline.yml"
    current = TASK_ROOT / "current.yml"
    baseline.write_bytes(DBT_SEMANTIC_YAML.read_bytes())
    request = MetricChangeRequest.from_dict(json.loads(REQUEST.read_text(encoding="utf-8")))
    proposal = propose_change(request)
    current.write_bytes(render_candidate(baseline.read_bytes(), proposal.canonical_patch))
    yield baseline, current
    if TASK_ROOT.exists():
        shutil.rmtree(TASK_ROOT)


def test_canonical_filter_candidate_has_exact_aligned_drift(canonical_pair: tuple[Path, Path]) -> None:
    result = check_canonical_drift(*canonical_pair)

    assert result["changed_canonical_metrics"] == ["valid_sbr_finishers"]
    assert result["canonical_changes"] == 1
    assert result["expected_power_bi_changes"] == 1
    assert result["expected_snowflake_changes"] == 1
    assert result["unexpected_target_only_drift"] == 0
    assert result["unrelated_object_changes"] == 0
    assert result["cross_target_semantic_drift"] == 0
    assert result["synchronization_status"] == "ALIGNED"
    assert result["baseline_hashes"]["canonical"] != result["current_hashes"]["canonical"]
    assert result["baseline_hashes"]["power_bi"] != result["current_hashes"]["power_bi"]
    assert result["baseline_hashes"]["snowflake"] != result["current_hashes"]["snowflake"]
    assert result["expected_power_bi_metrics"] == ["valid_sbr_finishers"]
    assert result["expected_snowflake_metrics"] == ["valid_sbr_finishers"]


@pytest.mark.parametrize("mode", ["stale", "missing", "extra"])
def test_stale_missing_or_extra_target_definition_refuses_alignment(
    canonical_pair: tuple[Path, Path], mode: str
) -> None:
    aligned = check_canonical_drift(*canonical_pair)
    observed = dict(aligned["expected_targets"]["power_bi"])
    if mode == "stale":
        observed = dict(aligned["baseline_targets"]["power_bi"])
    elif mode == "missing":
        observed.pop("valid_sbr_finishers")
    else:
        observed["unexpected_metric"] = {
            "table": "Rogue",
            "measure": "Rogue",
            "dax": "1",
        }

    result = check_canonical_drift(
        *canonical_pair,
        observed_power_bi=observed,
        observed_snowflake=aligned["expected_targets"]["snowflake"],
    )

    assert result["synchronization_status"] == "MANUAL_REVIEW_REQUIRED"
    assert result["unexpected_target_only_drift"] >= 1
    assert result["cross_target_semantic_drift"] >= 1
    if mode == "extra":
        assert result["unrelated_object_changes"] == 1


def test_windows_compatible_console_drift_and_committed_proposal_commands(
    canonical_pair: tuple[Path, Path],
) -> None:
    executable = shutil.which("semantic-agent")
    assert executable is not None, "editable semantic-agent console entry point is required"
    drift = subprocess.run(
        [
            executable,
            "check-canonical-drift",
            "--baseline",
            str(canonical_pair[0]),
            "--current",
            str(canonical_pair[1]),
            "--json",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert drift.returncode == 0, drift.stderr
    assert json.loads(drift.stdout)["synchronization_status"] == "ALIGNED"

    change_store = TASK_ROOT / "subprocess-changes"
    environment = os.environ.copy()
    environment["SEMANTIC_AGENT_CHANGE_DIR"] = str(change_store)
    proposal = subprocess.run(
        [executable, "propose", "--request", str(REQUEST), "--json"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proposal.returncode == 0, proposal.stderr
    payload = json.loads(proposal.stdout)
    assert payload["change_id"] == "chg_20260718T190000Z_90909090"
    assert payload["status"] == "PROPOSED"
    assert payload["approval_state"] == "PENDING"
    assert payload["deployment_state"] == "NOT_REQUESTED"
    assert (change_store / f"{payload['change_id']}.json").is_file()


def test_console_drift_exit_codes(canonical_pair: tuple[Path, Path]) -> None:
    executable = shutil.which("semantic-agent")
    assert executable is not None
    invalid = subprocess.run(
        [executable, "check-canonical-drift", "--baseline", "missing.yml", "--current", "missing.yml"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 2
    assert "CANONICAL_DRIFT_INPUT_INVALID" in invalid.stderr

    unsupported = yaml.safe_load(canonical_pair[1].read_text(encoding="utf-8"))
    semantic_model = unsupported["semantic_models"][0]
    measure = next(
        item
        for item in semantic_model["measures"]
        if item["name"] == "valid_sbr_finishers"
    )
    measure["agg"] = "median"
    manual_path = TASK_ROOT / "manual-review.yml"
    manual_path.write_text(
        yaml.safe_dump(unsupported, sort_keys=False), encoding="utf-8", newline="\n"
    )
    manual = subprocess.run(
        [
            executable,
            "check-canonical-drift",
            "--baseline",
            str(canonical_pair[0]),
            "--current",
            str(manual_path),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert manual.returncode == 3
    assert "Synchronization status:        MANUAL_REVIEW_REQUIRED" in manual.stdout
