"""The public demo must retain evidence and never invent executed target values."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from semantic_lab.fixtures import ROOT
from semantic_lab import showcase_three as demo


@pytest.fixture(scope="module")
def focused(tmp_path_factory):
    work = tmp_path_factory.mktemp("three-offline")
    guard = work / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import sys\ndef deny(event, args):\n"
        "    if event in ('socket.connect', 'socket.getaddrinfo'): raise RuntimeError('Network forbidden during demo')\n"
        "sys.addaudithook(deny)\n", encoding="utf-8")
    output = work / "evidence"
    command = [sys.executable, "labs/semantic_interop/scripts/run_showcase.py", "--scope", "three",
               "--check", "--output", str(output)]
    done = subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(guard),
                          DBT_SEND_ANONYMOUS_USAGE_STATS="false", DO_NOT_TRACK="1"),
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert done.returncode == 0, done.stdout + done.stderr
    return output, json.loads((output / "report.json").read_text(encoding="utf-8"))


def test_offline_three_paths_retain_source_and_target_boundaries(focused):
    output, report = focused
    assert report["accepted"] and report["fixture_unchanged"]
    assert [(p["source"], p["target"]) for p in report["paths"]] == list(demo.ROUTES)
    assert report["native_gate"]["native_matches"] == 31
    assert report["native_gate"]["detected_failures"] == 5
    assert [p["semantic_preservation"] for p in report["paths"]] == ["MISMATCH", "PASS", "MISMATCH"]
    assert [p["target_execution"] for p in report["paths"]] == ["BLOCKED", "NOT_EXECUTED", "NOT_EXECUTED"]
    assert all(p["numerical_equivalence"] == "BLOCKED" for p in report["paths"])
    assert not (output / "databricks").exists()
    assert not (output / "powerbi/switch").exists()


def test_evidence_hashes_and_markdown_links_resolve(focused):
    output, report = focused
    for relative, expected in report["artifact_sha256"].items():
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == expected
    for path in report["paths"]:
        assert all((output / p).is_file() for p in path["evidence"])
    for relative in re.findall(r"\]\(([^)]+)\)", (output / "report.md").read_text(encoding="utf-8")):
        assert (output / relative).is_file(), relative


def test_existing_destination_is_never_overwritten(focused):
    output, report = focused
    with pytest.raises(FileExistsError):
        demo.run_three(output)
    assert json.loads((output / "report.json").read_text()) == report


def test_missing_execution_cannot_be_reported_as_numerical_pass(tmp_path):
    directory = tmp_path / "to-metricflow/metricflow"
    directory.mkdir(parents=True)
    (directory / "execution.json").write_text(json.dumps({
        "numerical_equivalence": "PASS", "requested": 11, "executed": 11, "oracle_matches": 11}))
    paths = demo.assess_three(tmp_path)
    assert all(p["conversion"] == "NOT_EXECUTED" for p in paths)
    assert all(p["semantic_preservation"] == "UNKNOWN" for p in paths)
    assert all(p["numerical_equivalence"] == "BLOCKED" for p in paths)
    assert not demo.acceptance(tmp_path, paths, {"accepted": True}, [])["accepted"]


def test_converter_failure_does_not_become_static_success(tmp_path):
    directory = tmp_path / "to-powerbi/metricflow"
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps({"conversion": "FAIL", "error": "missing adapter"}))
    path = demo.assess_three(tmp_path)[2]
    assert path["conversion"] == "FAIL"
    assert path["semantic_preservation"] == "UNKNOWN"
    assert path["numerical_equivalence"] == "BLOCKED"


def test_source_failure_still_attempts_independent_powerbi(tmp_path, monkeypatch):
    calls = []
    def fail():
        raise RuntimeError("source unavailable")
    monkeypatch.setattr(demo, "run_demo", fail)
    monkeypatch.setattr(demo, "versions", fail)
    monkeypatch.setattr(demo, "native_demo", lambda *a, **k: calls.append(k))
    report = demo.run_three(tmp_path / "evidence")
    assert calls == [{"include_switch": False}]
    assert not report["accepted"]
    assert {e["step"] for e in report["errors"]} == {"versions", "native"}
    assert (tmp_path / "evidence/report.md").is_file()


def test_executed_mismatch_is_not_an_accepted_blocker(tmp_path):
    directory = tmp_path / "to-metricflow/metricflow"
    directory.mkdir(parents=True)
    (directory / "execution.json").write_text(json.dumps({"requested": 1, "cases": [
        {"execution_status": "EXECUTED", "target_result": {"rows": [[1]]}, "comparison_result": {"equal": False}}]}))
    paths = demo.assess_three(tmp_path)
    assert paths[0]["numerical_equivalence"] == "MISMATCH"
    assert not demo.acceptance(tmp_path, paths, {"accepted": True}, [])["checks"]["no_numerical_mismatch"]


def test_match_flag_without_actual_values_is_not_numerical_evidence(tmp_path):
    directory = tmp_path / "to-metricflow/metricflow"
    directory.mkdir(parents=True)
    (directory / "execution.json").write_text(json.dumps({"requested": 1, "cases": [
        {"execution_status": "EXECUTED", "target_result": None, "comparison_result": {"equal": True}}]}))
    assert demo.assess_three(tmp_path)[0]["numerical_equivalence"] == "BLOCKED"
