import hashlib
import json
import os
import subprocess
import sys

import jsonschema
import pytest

from semantic_lab.diagnostics import CLASSIFICATIONS, capture, reconstructed, plain, changes
from semantic_lab.fixtures import LAB, ROOT


@pytest.fixture(scope="module")
def diagnostic_runs(tmp_path_factory):
    guard = tmp_path_factory.mktemp("diagnostic_offline_guard")
    (guard / "sitecustomize.py").write_text("import sys\ndef deny(event, args):\n    if event in ('socket.connect','socket.getaddrinfo'): raise RuntimeError('offline')\nsys.addaudithook(deny)\n")
    reports = []
    for _ in range(2):
        result = subprocess.run([sys.executable, "-m", "semantic_lab", "diagnose-worker"], cwd=LAB,
                                env=dict(os.environ, PYTHONPATH=str(guard), PYTHONHASHSEED="0"),
                                capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        path = ROOT / json.loads(result.stdout)["report"]
        reports.append((path, json.loads(path.read_text())))
    return reports


def test_offline_diagnostics_repeat_and_preserve_v1_contract(diagnostic_runs):
    assert diagnostic_runs[0][1] == diagnostic_runs[1][1]
    schema = json.loads((LAB / "docs/report.schema.json").read_text())
    for path, report in diagnostic_runs:
        jsonschema.Draft202012Validator(schema).validate(report)
        for name, expected in report["artifact_sha256"].items():
            assert hashlib.sha256((path.parent / name).read_bytes()).hexdigest() == expected
        assert report["acceptance"]["accepted"]
        assert report["path_counts"]["native"] == {"PASS": 14}
        assert sum(c["failure_detection"] == "DETECTED" for c in report["cases"] if c["controlled_failure"]) == 10


def test_prerequisite_loss_and_next_blocker_are_separate(diagnostic_runs):
    report = diagnostic_runs[0][1]
    probe = report["semantic_diagnostics"]["time_spine"]
    assert probe["pydantic_returned_manifest_valid"]
    assert probe["probes"]["source_modern"]["lookup_constructed"]
    assert probe["probes"]["source_legacy"]["lookup_constructed"]
    assert "time spine" in probe["probes"]["empty_manifest"]["message"]
    assert "time spine" in probe["probes"]["returned_unmodified"]["message"]
    for lane in ("level3_spine", "level3_legacy_spine", "level3_spine_defaults"):
        records = [c for c in report["cases"] if c["path"] == lane]
        assert len(records) == 14
        assert all(c["execution_status"] == "NOT_EXECUTED" and "No primary entity" in c["diagnostics"][0]["error"] for c in records)


def test_interventions_never_rewrite_converted_definitions(diagnostic_runs):
    path, report = diagnostic_runs[0]
    from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
    source = PydanticSemanticManifest.parse_raw((path.parent / "source.manifest.normalized.json").read_text())
    target = PydanticSemanticManifest.parse_raw((path.parent / "roundtrip.manifest.json").read_text())
    before = plain(target)
    amended = reconstructed(source, target, defaults=True)
    assert plain(target) == before
    assert plain(amended)["metrics"] == before["metrics"]
    for a,b in zip(amended.semantic_models, target.semantic_models):
        assert a.entities == b.entities and a.dimensions == b.dimensions
    for lane in report["semantic_diagnostics"]["experiments"]:
        ledger = json.loads((path.parent / lane / "experiment.json").read_text())
        assert ledger["level"] == 3 and not ledger["lossless_roundtrip_claim"]
        parent = json.loads((path.parent / ledger["parent_manifest"]).read_text())
        actual = json.loads((path.parent / lane / "manifest.json").read_text())
        assert ledger["interventions"] == changes(parent, actual)
        assert ledger["manifest_sha256"] == hashlib.sha256((path.parent / lane / "manifest.json").read_bytes()).hexdigest()


def test_classifications_are_hash_bound_and_separate_from_status(diagnostic_runs):
    path, report = diagnostic_runs[0]
    findings = report["semantic_diagnostics"]["findings"]
    assert findings and len({f["id"] for f in findings}) == len(findings)
    assert {f["classification"] for f in findings} <= CLASSIFICATIONS
    assert any(f["classification"] == "UNKNOWN" for f in findings)
    for f in findings:
        assert "status" not in f
        for side in ("source", "target"):
            assert f[side]["sha256"] == hashlib.sha256((path.parent / f[side]["artifact"]).read_bytes()).hexdigest()


def test_capture_refuses_overwrite_and_hash_corruption(diagnostic_runs, tmp_path):
    run = diagnostic_runs[0][0].parent
    capture(run, tmp_path / "capture")
    with pytest.raises(FileExistsError):
        capture(run, tmp_path / "capture")
    report = json.loads((tmp_path / "capture/report.json").read_text())
    artifact = next(iter(report["artifact_sha256"]))
    (tmp_path / "capture" / artifact).write_text("tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        capture(tmp_path / "capture", tmp_path / "rejected")
