import json

from semantic_lab.fixtures import LAB
from test_roundtrip_diagnostics import diagnostic_runs as _diagnostic_runs

# Re-export the shared pytest fixture for this module's injected test arguments.
diagnostic_runs = _diagnostic_runs


def test_reductions_reexecute_source_and_retain_failure_mechanism(diagnostic_runs):
    path, report = diagnostic_runs[0]
    regressions = report["semantic_diagnostics"]["regressions"]
    assert {r["id"] for r in regressions} == {"entity_alias", "compound_aggregate", "metric_filter"}
    for r in regressions:
        assert r["native_matches_oracle"] and not r["returned_matches_oracle"]
        assert r["reduction"]["kept"]
        assert r["reduction"]["initial_failure"] == r["reduction"]["reduced_failure"]
        assert (path.parent / r["artifact_directory"] / "one_row/source.json").exists()
    assert regressions[0]["entity_names"] == []
    assert "nested" in regressions[1]["returned"]["error"].lower()
    assert "item_id__status" in regressions[2]["returned"]["error"]


def test_microsoft_dax_metadata_and_calculated_host_are_preserved_without_runtime_claim(diagnostic_runs):
    report = diagnostic_runs[0][1]
    microsoft = report["semantic_diagnostics"]["microsoft"]
    assert sum(m["dax"] == "BLANK()" for m in microsoft["matrix"]) == 4
    assert len(microsoft["relationship_source"]) == 2 and microsoft["relationship_target"] == []
    probe = microsoft["dax_probe"]
    assert probe["imported_metric_names"] == ["DoubleRevenue", "Revenue"]
    assert probe["returned_measures"]["Revenue"]["expression"] == "SUM(Fact[amount])"
    assert probe["returned_measures"]["Revenue"]["formatString"] == "0.00"
    assert probe["returned_measures"]["Revenue"]["displayFolder"] == "Finance"
    assert probe["returned_measures"]["DoubleRevenue"]["expression"] == "[Revenue] * 2"
    assert probe["returned_measures"]["DoubleRevenue"]["isHidden"]
    assert all(m["table"] == "Measures" for m in probe["returned_measures"].values())
    assert probe["relationships"][0]["fromTable"] == "Fact"
    assert probe["relationships"][0]["toTable"] == "Dim"
    assert probe["runtime"] == "NOT_EXECUTED"


def test_original_captured_evidence_is_unchanged():
    import hashlib
    baseline = json.loads((LAB / "results/task02/baseline.json").read_text())
    from semantic_lab.fixtures import ROOT
    protected = {p:h for p,h in baseline["file_sha256"].items() if p.startswith("labs/semantic_interop/results/evidence/")
                 or p in {"labs/semantic_interop/uv.lock", "labs/semantic_interop/pyproject.toml", "PUBLIC_PROVENANCE.json"}}
    assert protected
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p,h in protected.items())
