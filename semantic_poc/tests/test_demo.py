from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import yaml

import semantic_poc
import semantic_poc.demo as demo_module
from semantic_poc.agent import cli
from semantic_poc.agent.powerbi_import import resolve_powerbi_model_dir
from semantic_poc.demo import (
    DemoError,
    FIXTURE_MODEL,
    FIXTURE_PBIP,
    FIXTURE_SNOWFLAKE,
    PROJECT_ROOT,
    bundle_hashes,
    clean_demo_output,
    create_demo_bundle,
    finalize_demo_bundle,
)
from semantic_poc.src.models import DBT_SEMANTIC_YAML
from semantic_poc.src.snowflake_semantic_view import read_semantic_view_yaml, run_preflight


TEST_ROOT = PROJECT_ROOT / ".tmp" / "pytest-m8-demo"
LEGACY = PROJECT_ROOT / "semantic" / "triathlon_metric_contract.yml"


def _remove(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def demo_bundle() -> Path:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    output = TEST_ROOT / "bundle"
    _remove(output)
    create_demo_bundle(fixture="semantic-trap", output_dir=output)
    yield output
    _remove(output)


@pytest.fixture(scope="module")
def accepted_demo_bundle() -> Path:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    output = TEST_ROOT / "accepted-bundle"
    _remove(output)
    create_demo_bundle(fixture="semantic-trap", output_dir=output, check=True)
    yield output
    clean_demo_output(output)


def test_pbip_and_semantic_model_resolve_to_the_same_definition() -> None:
    assert resolve_powerbi_model_dir(FIXTURE_PBIP, PROJECT_ROOT) == resolve_powerbi_model_dir(
        FIXTURE_MODEL, PROJECT_ROOT
    )


def test_cli_fixture_default_and_project_surface() -> None:
    parser = cli.build_parser()
    fixture = parser.parse_args(["demo", "--fixture", "--output-dir", "demo-output"])
    assert fixture.fixture == "semantic-trap" and fixture.project is None
    project = parser.parse_args(
        ["demo", "--project", "model.SemanticModel", "--output-dir", "demo-output"]
    )
    assert project.project == "model.SemanticModel" and project.fixture is None


def test_bundle_has_expected_totals_boundaries_and_no_path_or_secret_leakage(
    demo_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "m8-secret-must-not-appear")
    manifest = json.loads((demo_bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"] == {
        "blocking_findings": 16,
        "equivalence_tests_failed": 2,
        "equivalence_tests_passed": 4,
        "exact_conversions": 3,
        "informational_findings": 3,
        "manual_review": 4,
        "measures_analyzed": 7,
        "power_bi_objects_analyzed": 25,
        "supported_with_explicit_mapping": 0,
        "unsupported_constructs": 1,
        "warnings": 3,
    }
    assert manifest["assurance"] == {
        "human_approval": "PENDING",
        "semantic_equivalence": "FAILED",
        "structural_compatibility": "MANUAL_REVIEW_REQUIRED",
        "syntactic_validity": "PASSED",
    }
    assert manifest["overall_status"] == "BLOCKED_PENDING_REVIEW"
    assert manifest["deployment_status"] == "NOT_REQUESTED"
    assert manifest["tool_version"] == version("triathlon-semantic-contract-poc") == "0.1.0"
    text = "\n".join(
        item.read_text(encoding="utf-8", errors="ignore")
        for item in demo_bundle.rglob("*")
        if item.is_file()
    )
    assert "m8-secret-must-not-appear" not in text
    assert str(PROJECT_ROOT) not in text
    assert "JanBusse" not in text and "C:\\Users\\" not in text and "C:/Users/" not in text


def test_manifest_hashes_cover_every_non_manifest_artifact(demo_bundle: Path) -> None:
    manifest = json.loads((demo_bundle / "manifest.json").read_text(encoding="utf-8"))
    actual = bundle_hashes(demo_bundle)
    assert set(manifest["output_hashes"]) == set(actual) - {"manifest.json"}
    assert all(actual[name] == value for name, value in manifest["output_hashes"].items())


def test_unchecked_bundle_is_explicitly_not_checked(demo_bundle: Path) -> None:
    manifest = json.loads((demo_bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["check_status"] == "NOT_CHECKED"


def test_source_hash_normalizes_only_safe_text_representation() -> None:
    root = TEST_ROOT / "source-hash-normalization"
    _remove(root)
    root.mkdir(parents=True)
    lf = root / "lf.pbip"
    crlf = root / "crlf.pbip"
    bom = root / "bom.pbip"
    changed = root / "changed.pbip"
    whitespace = root / "whitespace.pbip"
    binary_lf = root / "lf.bin"
    binary_crlf = root / "crlf.bin"
    payload = b'{\n  "version": "1.0"\n}\n'
    lf.write_bytes(payload)
    crlf.write_bytes(payload.replace(b"\n", b"\r\n"))
    bom.write_bytes(b"\xef\xbb\xbf" + payload)
    changed.write_bytes(payload.replace(b'"1.0"', b'"1.1"'))
    whitespace.write_bytes(payload.replace(b"  ", b"    "))
    binary_lf.write_bytes(payload)
    binary_crlf.write_bytes(payload.replace(b"\n", b"\r\n"))
    try:
        expected = demo_module._source_file_hash(lf)
        assert demo_module._source_file_hash(crlf) == expected
        assert demo_module._source_file_hash(bom) == expected
        assert demo_module._source_file_hash(changed) != expected
        assert demo_module._source_file_hash(whitespace) != expected
        assert demo_module._source_file_hash(binary_lf) != demo_module._source_file_hash(
            binary_crlf
        )
    finally:
        _remove(root)


def test_registered_check_is_byte_deterministic_and_matches_expectations() -> None:
    output = TEST_ROOT / "check"
    _remove(output)
    try:
        result = create_demo_bundle(
            fixture="semantic-trap", output_dir=output, check=True
        )
        assert result["verdict"] == "POC_DEMO_ACCEPTED"
        assert result["semantic_status"] == "BLOCKED_PENDING_REVIEW"
    finally:
        _remove(output)


def test_source_tree_hash_uses_relative_ordering() -> None:
    first = TEST_ROOT / "tree-first"
    second = TEST_ROOT / "tree-second"
    _remove(first)
    _remove(second)
    (first / "tables").mkdir(parents=True)
    (second / "tables").mkdir(parents=True)
    (first / "tables" / "B.tmdl").write_bytes(b"table B\r\n")
    (first / "tables" / "A.tmdl").write_bytes(b"table A\n")
    (second / "tables" / "A.tmdl").write_bytes(b"\xef\xbb\xbftable A\r\n")
    (second / "tables" / "B.tmdl").write_bytes(b"table B\n")
    try:
        assert demo_module._source_tree_hash(first) == demo_module._source_tree_hash(second)
    finally:
        _remove(first)
        _remove(second)


def test_fixture_inventory_provenance_is_crlf_invariant() -> None:
    root = TEST_ROOT / "crlf-fixture-copy"
    _remove(root)
    root.mkdir(parents=True)
    copied_pbip = root / FIXTURE_PBIP.name
    copied_model = root / FIXTURE_MODEL.name
    shutil.copy2(FIXTURE_PBIP, copied_pbip)
    shutil.copytree(FIXTURE_MODEL, copied_model)
    pbip_payload = copied_pbip.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    copied_pbip.write_bytes(pbip_payload.replace(b"\n", b"\r\n"))
    for path in copied_model.rglob("*"):
        if path.is_file() and path.suffix.casefold() in {".pbism", ".tmdl"}:
            payload = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            path.write_bytes(payload.replace(b"\n", b"\r\n"))
    try:
        expected = demo_module._fixture_result(FIXTURE_PBIP, FIXTURE_SNOWFLAKE)
        actual = demo_module._fixture_result(copied_pbip, FIXTURE_SNOWFLAKE)
        assert actual["source_snapshot_sha256"] == expected["source_snapshot_sha256"]
        assert (
            actual["inventory"]["model"]["source_tree_hash"]
            == expected["inventory"]["model"]["source_tree_hash"]
        )
    finally:
        _remove(root)


def test_project_mode_without_snowflake_yaml_is_explicitly_unavailable() -> None:
    output = TEST_ROOT / "project"
    _remove(output)
    try:
        create_demo_bundle(project=FIXTURE_MODEL, output_dir=output)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        comparison = json.loads(
            (output / "conversion-comparison.json").read_text(encoding="utf-8")
        )
        candidate = yaml.safe_load(
            (output / "generated" / "canonical-contract.candidate.yml").read_text(encoding="utf-8")
        )
        assert manifest["inputs"]["snowflake_yaml"] is None
        assert comparison["snowflake_evidence_status"] == "NOT_AVAILABLE"
        assert candidate["status"] == "MANUAL_REVIEW_REQUIRED"
    finally:
        _remove(output)


def test_explicit_snowflake_input_and_manifest_paths_are_normalized() -> None:
    output = TEST_ROOT / "explicit-snowflake"
    _remove(output)
    try:
        create_demo_bundle(
            project=str(FIXTURE_PBIP).replace("/", "\\"),
            snowflake_yaml=str(FIXTURE_SNOWFLAKE).replace("/", "\\"),
            output_dir=str(output).replace("/", "\\"),
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["inputs"]["snowflake_yaml"] == FIXTURE_SNOWFLAKE.relative_to(
            PROJECT_ROOT
        ).as_posix()
        for value in manifest["inputs"].values():
            if isinstance(value, str):
                assert "\\" not in value and not Path(value).is_absolute()
    finally:
        _remove(output)


def test_outside_and_overlap_paths_are_rejected() -> None:
    with pytest.raises(DemoError, match="repository-contained"):
        create_demo_bundle(
            fixture="semantic-trap",
            output_dir=PROJECT_ROOT.parent / "m8-outside-repository-output",
        )

    overlap = FIXTURE_MODEL / "definition" / "must-not-be-created"
    with pytest.raises(DemoError, match="overlaps protected source"):
        create_demo_bundle(fixture="semantic-trap", output_dir=overlap)
    assert not overlap.exists()


def test_symlink_input_is_rejected_when_host_supports_symlinks() -> None:
    link = TEST_ROOT / "fixture-link.pbip"
    link.unlink(missing_ok=True)
    try:
        try:
            link.symlink_to(FIXTURE_PBIP)
        except OSError:
            pytest.skip("Symbolic-link creation is unavailable on this Windows host")
        with pytest.raises(DemoError, match="repository-contained"):
            create_demo_bundle(project=link, output_dir=TEST_ROOT / "symlink-output")
    finally:
        link.unlink(missing_ok=True)
        _remove(TEST_ROOT / "symlink-output")


def test_output_refusal_and_managed_cleanup_safety() -> None:
    existing = TEST_ROOT / "existing"
    _remove(existing)
    existing.mkdir(parents=True)
    try:
        with pytest.raises(DemoError, match="already exists"):
            create_demo_bundle(fixture="semantic-trap", output_dir=existing)
        with pytest.raises(DemoError, match="no managed M8 manifest"):
            clean_demo_output(existing)
    finally:
        _remove(existing)

    managed = TEST_ROOT / "managed"
    _remove(managed)
    create_demo_bundle(fixture="semantic-trap", output_dir=managed)
    assert clean_demo_output(managed) is True
    assert not managed.exists()


def test_benchmark_mismatch_refuses_the_checked_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = TEST_ROOT / "mismatch-output"
    bad_expected = TEST_ROOT / "mismatched-expected-summary.json"
    _remove(output)
    expected = json.loads(
        (PROJECT_ROOT / "semantic_poc" / "demo" / "expected_summary.json").read_text(
            encoding="utf-8"
        )
    )
    expected["artifact_hashes"]["semantic-lint-report.json"] = "0" * 64
    bad_expected.write_text(json.dumps(expected), encoding="utf-8", newline="\n")
    monkeypatch.setattr(demo_module, "EXPECTED_SUMMARY", bad_expected)
    retained: list[Path] = []
    try:
        with pytest.raises(DemoError) as raised:
            create_demo_bundle(
                fixture="semantic-trap",
                output_dir=output,
                check=True,
            )
        error = raised.value
        assert error.code == "DEMO_EXPECTATION_HASH_MISMATCH"
        assert error.stage == "check_expected_bundle"
        assert error.artifact == "semantic-lint-report.json"
        assert error.expected == "0" * 64
        assert error.actual != error.expected
        assert error.remediation
        assert error.diagnostics and Path(error.diagnostics).is_file()
        assert error.retained_bundle and Path(error.retained_bundle).is_dir()
        retained.append(Path(error.retained_bundle))
        with pytest.raises(DemoError) as repeated:
            create_demo_bundle(
                fixture="semantic-trap",
                output_dir=output,
                check=True,
            )
        assert repeated.value.retained_bundle
        retained.append(Path(repeated.value.retained_bundle))
        assert retained[0] != retained[1]
        assert all(path.is_dir() for path in retained)
        manifest = json.loads(
            (retained[0] / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["check_status"] == "FAILED"
        assert "failure-diagnostics.json" in manifest["output_hashes"]
        assert not output.exists()
        with pytest.raises(DemoError) as finalize_error:
            finalize_demo_bundle(
                demo_run=retained[0],
                decisions=PROJECT_ROOT / "semantic_poc" / "demo" / "review-decisions.accepted.yml",
                output_dir=TEST_ROOT / "failed-must-not-finalize",
            )
        assert finalize_error.value.code == "DEMO_FINALIZE_INPUT_NOT_ACCEPTED"
    finally:
        bad_expected.unlink(missing_ok=True)
        _remove(output)
        _remove(TEST_ROOT / "failed-must-not-finalize")
        for path in retained:
            clean_demo_output(path)


def test_default_demo_preserves_powerbi_canonical_and_legacy_sources() -> None:
    protected = {
        DBT_SEMANTIC_YAML: _sha(DBT_SEMANTIC_YAML),
        LEGACY: _sha(LEGACY),
        FIXTURE_MODEL / "definition" / "tables" / "Measures.tmdl": _sha(
            FIXTURE_MODEL / "definition" / "tables" / "Measures.tmdl"
        ),
    }
    benchmark_outputs = PROJECT_ROOT / "semantic_poc" / "benchmark" / "output"
    protected.update(
        {
            path: _sha(path)
            for path in benchmark_outputs.rglob("*")
            if path.is_file()
        }
    )
    output = TEST_ROOT / "immutable"
    _remove(output)
    try:
        create_demo_bundle(fixture="semantic-trap", output_dir=output)
        assert {path: _sha(path) for path in protected} == protected
    finally:
        _remove(output)


def test_snowflake_candidate_is_locally_syntactic_and_verification_only(
    demo_bundle: Path,
) -> None:
    candidate = demo_bundle / "generated" / "snowflake-semantic-view.candidate.yml"
    preflight = run_preflight(
        read_semantic_view_yaml(candidate),
        {
            "database": "BENCHMARK",
            "mart_schema": "PUBLIC",
            "semantic_schema": "SEMANTIC",
            "semantic_view_name": "semantic_traps_candidate",
        },
    )
    assert preflight["status"] == "passed"
    sql = (demo_bundle / "generated" / "snowflake-verification.sql").read_text(
        encoding="utf-8"
    )
    assert "$$, TRUE);" in sql and "never executed" in sql

    stable_text = "\n".join(
        item.read_text(encoding="utf-8", errors="ignore")
        for item in demo_bundle.rglob("*")
        if item.is_file()
    )
    assert re.search(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", stable_text) is None
    assert "deploy_snowflake_semantic_view" not in stable_text


def test_reviewed_finalization_requires_complete_hash_bound_decisions(
    accepted_demo_bundle: Path,
) -> None:
    output = TEST_ROOT / "finalized"
    _remove(output)
    try:
        result = finalize_demo_bundle(
            demo_run=accepted_demo_bundle,
            decisions=PROJECT_ROOT / "semantic_poc" / "demo" / "review-decisions.accepted.yml",
            output_dir=output,
        )
        assert result["status"] == "READY_FOR_GOVERNED_FINALIZATION"
        assert result["equivalence_passed"] == result["equivalence_total"] == 5
        assert result["deployment_status"] == "NOT_REQUESTED"
        assert result["source_power_bi_modified"] is False
        assert result["canonical_modified"] is False
    finally:
        _remove(output)

    incomplete = TEST_ROOT / "incomplete-decisions.yml"
    value = yaml.safe_load(
        (PROJECT_ROOT / "semantic_poc" / "demo" / "review-decisions.accepted.yml").read_text(
            encoding="utf-8"
        )
    )
    value["decisions"] = value["decisions"][:-1]
    incomplete.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    try:
        with pytest.raises(DemoError, match="lack decisions"):
            finalize_demo_bundle(
                demo_run=accepted_demo_bundle,
                decisions=incomplete,
                output_dir=TEST_ROOT / "must-not-exist",
            )
    finally:
        incomplete.unlink(missing_ok=True)
        _remove(TEST_ROOT / "must-not-exist")


@pytest.mark.parametrize("case", ["stale", "duplicate", "contradictory"])
def test_finalization_rejects_stale_duplicate_and_contradictory_decisions(
    accepted_demo_bundle: Path,
    case: str,
) -> None:
    source = PROJECT_ROOT / "semantic_poc" / "demo" / "review-decisions.accepted.yml"
    value = yaml.safe_load(source.read_text(encoding="utf-8"))
    if case == "stale":
        value["source_snapshot_sha256"] = "0" * 64
        expected = "not bound"
    elif case == "duplicate":
        value["decisions"].append(dict(value["decisions"][0]))
        expected = "Duplicate"
    else:
        exact = next(item for item in value["decisions"] if item["action"] == "ACCEPT")
        exact["action"] = "REJECT"
        exact["canonical_metric"] = None
        expected = "must be explicitly accepted"
    path = TEST_ROOT / f"{case}-decisions.yml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    try:
        with pytest.raises(DemoError, match=expected):
            finalize_demo_bundle(
                demo_run=accepted_demo_bundle,
                decisions=path,
                output_dir=TEST_ROOT / f"{case}-must-not-exist",
            )
    finally:
        path.unlink(missing_ok=True)
        _remove(TEST_ROOT / f"{case}-must-not-exist")


def test_json_cli_check_has_no_mixed_human_output(capsys: pytest.CaptureFixture[str]) -> None:
    output = TEST_ROOT / "cli-json"
    _remove(output)
    try:
        assert cli.main(
            ["demo", "--fixture", "--output-dir", str(output), "--check", "--json"]
        ) == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["verdict"] == "POC_DEMO_ACCEPTED"
        assert payload["deployment_status"] == "NOT_REQUESTED"
    finally:
        _remove(output)


def test_json_cli_failure_contains_structured_diagnostics(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = TEST_ROOT / "cli-json-failure"
    bad_expected = TEST_ROOT / "cli-json-failure-expected.json"
    _remove(output)
    expected = json.loads(
        (PROJECT_ROOT / "semantic_poc" / "demo" / "expected_summary.json").read_text(
            encoding="utf-8"
        )
    )
    expected["artifact_hashes"]["support-matrix.json"] = "f" * 64
    bad_expected.write_text(json.dumps(expected), encoding="utf-8", newline="\n")
    monkeypatch.setattr(demo_module, "EXPECTED_SUMMARY", bad_expected)
    retained: Path | None = None
    try:
        assert cli.main(
            ["demo", "--fixture", "--output-dir", str(output), "--check", "--json"]
        ) == cli.EXIT_UNEXPECTED
        payload = json.loads(capsys.readouterr().out)
        assert payload["verdict"] == "POC_DEMO_NOT_ACCEPTED"
        assert payload["error"]["code"] == "DEMO_EXPECTATION_HASH_MISMATCH"
        assert payload["error"]["stage"] == "check_expected_bundle"
        assert payload["error"]["artifact"] == "support-matrix.json"
        assert payload["error"]["diagnostics"]
        assert payload["error"]["retained_bundle"]
        retained = PROJECT_ROOT / payload["error"]["retained_bundle"]
        assert retained.is_dir()
    finally:
        bad_expected.unlink(missing_ok=True)
        _remove(output)
        if retained is not None:
            clean_demo_output(retained)


def test_article_evidence_pack_matches_committed_demo_expectations() -> None:
    required = {
        "architecture.mmd",
        "migration-review-flow.mmd",
        "maintenance-flow.mmd",
        "benchmark-summary.json",
        "benchmark-summary.md",
        "key-findings.md",
        "limitations.md",
        "reproduction-commands.md",
    }
    root = PROJECT_ROOT / "docs" / "article-assets"
    assert required <= {item.name for item in root.iterdir() if item.is_file()}
    article = json.loads((root / "benchmark-summary.json").read_text(encoding="utf-8"))
    expected = json.loads(
        (PROJECT_ROOT / "semantic_poc" / "demo" / "expected_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert article["power_bi_objects"] == expected["counts"]["power_bi_objects_analyzed"]
    assert article["measures"] == expected["counts"]["measures_analyzed"]
    assert article["exact_conversions"] == expected["counts"]["exact_conversions"]
    assert article["blocking_findings"] == expected["counts"]["blocking_findings"]
    assert article["warnings"] == expected["counts"]["warnings"]
    assert article["source_equivalence"] == {"passed": 4, "failed": 2}
    findings = json.loads(
        (PROJECT_ROOT / "semantic_poc" / "demo" / "expected_findings.json").read_text(
            encoding="utf-8"
        )
    )
    finding_ids = {item["finding_id"] for item in findings["findings"]}
    cited = set(re.findall(r"fnd_[0-9a-f]{16}", (root / "key-findings.md").read_text(encoding="utf-8")))
    assert cited and cited <= finding_ids


def test_python_walkthrough_wrapper_is_windows_compatible() -> None:
    output = TEST_ROOT / "wrapper-output"
    _remove(output)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "semantic_poc" / "run_demo.py"),
                "--clean",
                "--check",
                "--output-dir",
                str(output),
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "POC_DEMO_ACCEPTED"
    finally:
        if output.exists():
            clean_demo_output(output)


def test_direct_console_entry_point_is_checkout_bound_and_checked() -> None:
    executable = shutil.which("semantic-agent")
    if executable is None:
        pytest.skip("The editable console entry point is not installed")
    output = TEST_ROOT / "console-entry-output"
    _remove(output)
    try:
        completed = subprocess.run(
            [
                executable,
                "demo",
                "--fixture",
                "semantic-trap",
                "--output-dir",
                str(output),
                "--check",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert "POC_DEMO_ACCEPTED" in completed.stdout
        assert Path(semantic_poc.__file__).resolve().is_relative_to(PROJECT_ROOT)
    finally:
        if output.exists():
            clean_demo_output(output)


@pytest.mark.skipif(
    sys.platform != "win32" or sys.version_info[:2] != (3, 12),
    reason="Designated Milestone 8 acceptance runtime is Windows Python 3.12",
)
def test_designated_windows_python_312_editable_runtime() -> None:
    assert sys.version_info[:2] == (3, 12)
    assert Path(semantic_poc.__file__).resolve().is_relative_to(PROJECT_ROOT)


def test_wrapper_cleanup_refusal_prints_actionable_diagnostics() -> None:
    output = TEST_ROOT / "wrapper-refusal-output"
    _remove(output)
    output.mkdir(parents=True)
    sentinel = output / "user-evidence.txt"
    sentinel.write_text("must survive\n", encoding="utf-8", newline="\n")
    diagnostic_root: Path | None = None
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "semantic_poc" / "run_demo.py"),
                "--clean",
                "--check",
                "--output-dir",
                str(output),
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 0
        assert completed.stdout.strip() == "POC_DEMO_NOT_ACCEPTED"
        assert "ERROR_CODE: DEMO_CLEANUP_REFUSED" in completed.stderr
        assert "STAGE: clean_output" in completed.stderr
        assert "EXPECTED:" in completed.stderr
        assert "ACTUAL:" in completed.stderr
        assert "REMEDIATION:" in completed.stderr
        match = re.search(r"^DIAGNOSTICS: (.+)$", completed.stderr, re.MULTILINE)
        assert match is not None
        diagnostics = PROJECT_ROOT / Path(match.group(1).strip())
        assert diagnostics.is_file()
        diagnostic_root = diagnostics.parent
        assert sentinel.read_text(encoding="utf-8") == "must survive\n"
    finally:
        _remove(output)
        if diagnostic_root is not None:
            _remove(diagnostic_root)
