import json
import os
from pathlib import Path
import subprocess
import sys
from decimal import Decimal

import duckdb
import jsonschema
import pytest

from semantic_lab.fixtures import CASES, FIXTURES, LAB, ROOT, load_data, oracle
from semantic_lab.runner import acceptance


def test_independent_oracle_has_reviewed_business_outputs():
    with duckdb.connect() as conn:
        load_data(conn)
        results = {case.id: oracle(conn, case)[1] for case in CASES}
    assert results["total_revenue"] == [(Decimal("265.30"),)]
    assert results["order_count"] == [(8,)]
    assert results["average_order_value"] == [(33.1625,)]
    assert results["completed_revenue"] == [(Decimal("195.30"),)]
    assert results["purchasing_customers"] == [(5,)]
    assert dict(results["revenue_by_category"]) == {"Books": Decimal("85.10"), "Tools": Decimal("170"), "Zero": Decimal("0"), None: Decimal("10.20")}
    assert results["completed_revenue_share"][0][0] == pytest.approx(195.3 / 265.3, abs=1e-12)
    assert results["zero_share"] == [("Zero", None)]
    assert results["year_boundary"] == [(Decimal("50.30"),)]
    assert results["empty_revenue"] == [(None,)]
    assert results["empty_orders"] == [(0,)]
    assert results["empty_customers"] == [(0,)]
    assert results["empty_aov"] == [(None,)]
    assert {str(day): value for day, value in results["revenue_over_time"]} == {
        "2024-02-28": Decimal("40"), "2024-02-29": Decimal("20"), "2024-03-01": Decimal("150"),
        "2025-12-31": Decimal("0.30"), "2026-01-01": Decimal("50"), "2026-01-02": Decimal("5"),
    }


@pytest.fixture(scope="module")
def executions(tmp_path_factory):
    # Python socket audit guard is inherited by dbt subprocesses. No external account or network is needed.
    guard = tmp_path_factory.mktemp("network_guard")
    (guard / "sitecustomize.py").write_text(
        "import sys\ndef deny(event, args):\n"
        "    if event in ('socket.connect', 'socket.getaddrinfo'): raise RuntimeError('Network forbidden during benchmark')\n"
        "sys.addaudithook(deny)\n", encoding="utf-8")
    reports = []
    for _ in range(2):
        done = subprocess.run([sys.executable, "-m", "semantic_lab", "benchmark", "--check"],
                              cwd=LAB, env=dict(os.environ, PYTHONPATH=str(guard)),
                              capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert done.returncode == 0, done.stdout + done.stderr
        summary = json.loads(done.stdout)
        path = ROOT / summary["report"]
        reports.append((path, json.loads(path.read_text())))
    return reports


def test_reports_and_evidence_are_repeatable(executions):
    assert executions[0][1] == executions[1][1]
    import hashlib
    for report_path, report in executions:
        for relative, expected_hash in report["artifact_sha256"].items():
            assert hashlib.sha256((report_path.parent / relative).read_bytes()).hexdigest() == expected_hash


def test_real_native_execution_and_roundtrip_limitations(executions):
    report = executions[0][1]
    assert report["acceptance"]["accepted"]
    assert report["integration"]["ossie_conversion_executed"]
    assert report["path_counts"]["native"] == {"PASS": 14}
    roundtrip = [r for r in report["cases"] if r["path"] == "roundtrip"]
    assert len(roundtrip) == 14
    assert all(r["execution_status"] == "NOT_EXECUTED" for r in roundtrip)
    assert all(any("time spine" in str(d) for d in r["diagnostics"]) for r in roundtrip)
    assert all(r["status"] == "SEMANTIC_MISMATCH" for r in roundtrip)
    assert all(r["upstream_issue_reporting"] == "NO_ISSUES_REPORTED" for r in roundtrip)


def test_microsoft_placeholders_are_not_runtime_passes(executions):
    records = executions[0][1]["cases"]
    microsoft = [r for r in records if r["path"] == "microsoft"]
    placeholders = [r for r in microsoft if r["semantic_coverage"].get("dax") == "BLANK()"]
    assert {r["metric_id"] for r in placeholders} == {"order_count", "completed_revenue", "average_order_value", "completed_revenue_share"}
    assert all(r["status"] == "UNSUPPORTED" for r in placeholders)
    assert not any(r["status"] == "PASS" for r in records if r["path"] in {"microsoft", "tmdl_static"})


def test_controls_detect_faults_without_marking_faulty_queries_correct(executions):
    report = executions[0][1]
    controls = [r for r in report["cases"] if r["controlled_failure"]]
    assert len(controls) == 10
    assert all(r["failure_detection"] == "DETECTED" and r["status"] != "PASS" for r in controls)
    assert sum(r["status"] == "NUMERICAL_MISMATCH" for r in controls) == 6
    faulty = next(r for r in controls if r["case_id"] == "join_fanout")
    assert faulty["observed"]["rows"][0][0]["value"] == "510.60"
    ratio = next(r for r in controls if r["case_id"] == "average_of_ratios")
    assert float(ratio["observed"]["rows"][0][0]["value"]) == pytest.approx(2 / 3)
    broken = json.loads(json.dumps(report))
    next(r for r in broken["cases"] if r["case_id"] == "join_fanout")["failure_detection"] = "UNDETECTED"
    assert not acceptance(broken)["accepted"]


def test_machine_readable_report_contract(executions):
    schema = json.loads((LAB / "docs/report.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(executions[0][1])


def test_generated_files_do_not_enter_authored_fixtures():
    assert not any(p.name in {"profiles.yml", "manifest.json", "semantic_manifest.json", "benchmark.duckdb"} for p in FIXTURES.rglob("*"))
