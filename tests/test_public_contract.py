from __future__ import annotations

import json
from pathlib import Path
import struct
import subprocess
import sys
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


def test_public_projection_excludes_private_authoring_and_live_evidence() -> None:
    paths = {item.relative_to(ROOT).as_posix() for item in ROOT.rglob("*") if item.is_file()}
    assert not any(path.startswith("publication-private/") for path in paths)
    assert not any(path.startswith(".agents/") for path in paths)
    assert not any("live-evidence" in path for path in paths)
    assert not any(path.endswith("07-live-validation.json") for path in paths)


def test_public_pbit_is_sanitized_and_has_no_security_bindings() -> None:
    with ZipFile(ROOT / "pbit" / "pbi_trial.pbit") as archive:
        assert "SecurityBindings" not in archive.namelist()
        model = archive.read("DataModelSchema").decode("utf-16-le")
    assert "snowflakecomputing.com" not in model
    assert "SANITIZED_ACCOUNT" in model
    assert "SANITIZED_WAREHOUSE" in model
    assert "SANITIZED_DATABASE" in model
    assert "SANITIZED_SCHEMA" in model


def test_public_provenance_is_complete_and_release_state_is_explicit() -> None:
    value = json.loads((ROOT / "PUBLIC_PROVENANCE.json").read_text(encoding="utf-8"))
    assert value["private_source_commit"]
    assert value["local_review_only"] is value["private_source_dirty"]
    assert value["protected_inputs"]["models/semantic/triathlon_semantic.yml"]["kind"] == "FILE"
    kinds = {item["kind"] for item in value["transformations"]}
    assert {"PBIT_SANITIZATION", "TEXT_REDACTION", "GUIDED_GOLDEN_REGENERATION"} <= kinds


def test_public_package_exposes_semantic_agent_console_script() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" in pyproject
    assert 'semantic-agent = "semantic_poc.agent.cli:main"' in pyproject


def test_relationship_graph_contains_exactly_the_captured_endpoints() -> None:
    svg = (ROOT / "docs" / "images" / "relationship-comparison.svg").read_text(
        encoding="utf-8"
    )
    expected = {
        "DIM_EVENT.COUNTRY_ID-&gt;DIM_COUNTRY.COUNTRY_ID",
        "DIM_DIVSION.AGE_GROUP_ID-&gt;DIM_AGE_GROUP.AGE_GROUP_ID",
        "FCT_RESULT.EVENT_ID-&gt;DIM_EVENT.EVENT_ID",
        "FCT_RESULT.DIVISION_ID-&gt;DIM_DIVSION.DIVISION_ID",
        "FCT_RESULT.DISTANCE_ID-&gt;DIM_DISTANCE.DISTANCE_ID",
        "FCT_RESULT.GENDER_ID-&gt;DIM_GENDER.GENDER_ID",
        "FCT_SPLIT.RESULT_ID-&gt;FCT_RESULT.RESULT_ID",
    }
    actual = {
        value.split('"', 1)[0]
        for value in svg.split('data-edge="')[1:]
    }
    assert actual == expected
    assert svg.count('data-edge="') == 7


def test_agent_governance_graph_keeps_execution_and_approval_separate() -> None:
    svg = (ROOT / "docs" / "images" / "agent-review-flow.svg").read_text(
        encoding="utf-8"
    )
    for required in (
        "The agent never writes executable DAX or SQL",
        "Deterministic checks",
        "Proven",
        "Tested compiler",
        "Candidate only",
        "Pause for human choice",
        "confirm again next time",
        "Outside this workflow",
        "live validate",
        "deploy",
    ):
        assert required in svg
    assert "Task 07" not in svg
    assert 'width="1200"' in svg
    assert 'height="675"' in svg
    assert 'role="img"' in svg
    assert "<title" in svg
    assert "<desc" in svg

    png = (ROOT / "docs" / "images" / "agent-review-flow.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (1200, 675)


def test_public_demo_check_is_offline_and_accepted() -> None:
    result = subprocess.run(
        [sys.executable, "demo/run_public_demo.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "PUBLIC_COMPANION_DEMO_ACCEPTED" in result.stdout
    assert "Deployment: NO" in result.stdout
