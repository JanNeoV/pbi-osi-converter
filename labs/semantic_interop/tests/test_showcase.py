"""Evidence custody and honest failure handling for the nine-route demo."""
import hashlib
import json

from semantic_lab.fixtures import LAB
from semantic_lab.showcase import assess


def test_missing_tools_do_not_become_successes(tmp_path):
    result = assess(tmp_path)
    assert len(result["paths"]) == 9
    assert all(p["conversion"] == "NOT_EXECUTED" for p in result["paths"])
    assert all(p["semantic_preservation"] == "UNKNOWN" for p in result["paths"])
    assert all(p["numerical_equivalence"] == "BLOCKED" for p in result["paths"])


def test_checked_showcase_outputs_are_hash_bound():
    folder = LAB / "results/task04-showcase"
    report = json.loads((folder / "report.json").read_text())
    for name, expected in report["artifact_sha256"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected, name
    paths = report["comparison"]["paths"]
    assert {(p["source"], p["target"]) for p in paths} == {
        (a, b) for a in ("MetricFlow", "Power BI", "Databricks")
        for b in ("MetricFlow", "Power BI", "Databricks")}
    for path in paths:
        for leg in path["legs"]:
            assert (folder / leg).is_file(), leg


def test_numerical_claims_require_engine_results():
    folder = LAB / "results/task04-showcase"
    paths = json.loads((folder / "paths.json").read_text())["paths"]
    for path in paths:
        if path["target"] == "MetricFlow":
            source = {"MetricFlow": "metricflow", "Power BI": "powerbi", "Databricks": "databricks"}[path["source"]]
            execution = json.loads((folder / f"to-metricflow/{source}/execution.json").read_text())
            assert path["numerical_equivalence"] == execution["numerical_equivalence"]
            assert all(c["source_platform"] != "Authored SQL mutation" for c in execution.get("cases", []))
            if path["numerical_equivalence"] == "PASS":
                assert execution["requested"] == execution["executed"] == execution["oracle_matches"] > 0
        else:
            assert path["numerical_equivalence"] == "BLOCKED"
    switch = json.loads((folder / "powerbi/report.json").read_text())["duckdb_oracle"]["planned_selector_tests"]
    assert len(switch) == 12
    assert all(c["actual_DAX_value"] is None and c["DAX_execution"] == "NOT_EXECUTED" for c in switch)
