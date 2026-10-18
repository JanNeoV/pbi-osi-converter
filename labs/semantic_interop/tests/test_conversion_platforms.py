"""Real official conversion probes; structural output is never a runtime pass."""
import hashlib
import json

import jsonschema
import pytest

from semantic_lab import conversion_platforms
from semantic_lab.fixtures import LAB


@pytest.fixture(scope="module")
def probe():
    return conversion_platforms.run_platforms()


def test_stage_b_stops_before_conversion_when_stage_a_fails(monkeypatch, tmp_path):
    from ossie_dbt import MSIToOssieConverter
    monkeypatch.setattr(conversion_platforms, "run_demo", lambda: (tmp_path, {"stage_a": {"accepted": False}}))
    def forbidden(*args, **kwargs):
        raise AssertionError("Conversion must not run before Stage A acceptance")
    monkeypatch.setattr(MSIToOssieConverter, "convert", forbidden)
    with pytest.raises(RuntimeError, match="Stage A failed"):
        conversion_platforms.run_platforms()
    assert not (tmp_path / "platforms").exists()


def test_official_conversion_output_retains_actual_blockers(probe):
    run, report = probe
    integrations = report["platform_integrations"]
    assert report["stage_a"]["accepted"]
    assert integrations["ossie_conversion"]["execution_status"] == "EXECUTED"
    assert integrations["ossie_conversion"]["pydantic_structure_valid"] is True
    roundtrip = integrations["roundtrip_conversion"]
    assert roundtrip["configuration_reconstructed"] is False
    assert roundtrip["source_inventory"]["time_spines"] == 1
    assert roundtrip["target_inventory"]["time_spines"] == 0
    assert roundtrip["requested_comparisons"] == 31
    assert roundtrip["numerically_executed"] == 0
    rows = [r for r in report["cases"] if r["conversion_path"] == "MetricFlow -> Apache Ossie -> MetricFlow -> DuckDB"]
    assert len(rows) == 31
    assert all(r["execution_status"] == "NOT_EXECUTED" and r["target_result"] is None
               and r["comparison_result"] is None for r in rows)
    assert all("time spine" in str(r["diagnostics"]) for r in rows)
    assert not list((run / "platforms/roundtrip").glob("*.sql"))


def test_microsoft_placeholders_are_observations_not_dax_results(probe):
    _run, report = probe
    ms = report["platform_integrations"]["microsoft_conversion"]
    assert ms["execution_status"] == "EXECUTED"
    assert len(ms["emitted_measures"]) == len(ms["placeholder_measures"]) == 9
    assert all(expression == "BLANK()" for expression in ms["emitted_measures"].values())
    assert ms["DAX_execution"] == ms["TOM_validation"] == "NOT_EXECUTED"
    assert report["platform_integrations"]["target_runtime"] == {
        "Power BI/DAX": "NOT_EXECUTED", "Databricks": "NOT_EXECUTED", "TOM": "NOT_EXECUTED"}
    assert report["platform_integrations"]["databricks_conversion"]["execution_status"] == "NOT_EXECUTED"


def test_every_comparison_and_artifact_remains_bound_to_real_evidence(probe):
    run, report = probe
    validator = jsonschema.Draft202012Validator(json.loads((LAB / "docs/conversion-result.schema.json").read_text()))
    for record in report["cases"] + report["controlled_failures"]:
        validator.validate(record)
        for relative in record["evidence_reference"]:
            assert (run / relative).is_file(), relative
    for relative, expected in report["artifact_sha256"].items():
        assert hashlib.sha256((run / relative).read_bytes()).hexdigest() == expected
