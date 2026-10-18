"""Isolated teaching benchmark: raw SQL oracles versus genuine MetricFlow SQL."""
from datetime import datetime
from decimal import Decimal
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import duckdb
import yaml

from .compare import compare, table
from .fixtures import Case, FIXTURES, ROOT
from .integrations import compile_queries, write_json

FIXTURE = FIXTURES / "conversion_demo"
METRICS = ("eligible_sessions", "converted_sessions", "conversion_rate", "paid_revenue",
           "paid_order_count", "average_paid_order_value")
GRAINS = ("overall", "channel", "month", "month_channel")
FAULTS = ("wrong_denominator", "incorrect_conversion", "average_of_rates", "removed_channel_filter", "inner_join")
QUESTIONS = {
    "eligible_sessions": "How many eligible sessions did we receive?",
    "converted_sessions": "How many eligible sessions had a paid order by the cutoff?",
    "conversion_rate": "What share of eligible sessions converted?",
    "paid_revenue": "How much paid revenue belongs to these session cohorts?",
    "paid_order_count": "How many paid orders belong to eligible sessions?",
    "average_paid_order_value": "What is the average paid order value?",
}


def load_data(conn, edge=False):
    schemas = {
        "sessions": {"session_id": "INTEGER", "session_date": "DATE", "sales_channel": "VARCHAR",
                     "is_eligible": "BOOLEAN", "exclusion_reason": "VARCHAR"},
        "orders": {"order_id": "INTEGER", "session_id": "INTEGER", "order_date": "DATE",
                   "amount_eur": "DECIMAL(18,2)", "order_status": "VARCHAR"},
    }
    for name, schema in schemas.items():
        conn.execute(f"CREATE TABLE raw_{name} AS SELECT * FROM read_csv(?, columns=?, header=true, nullstr='')",
                     [str(FIXTURE / "data" / f"{name}.csv"), schema])
    if edge:
        conn.execute("INSERT INTO raw_orders SELECT * FROM read_csv(?, columns=?, header=true)",
                     [str(FIXTURE / "data/additional_order.csv"), schemas["orders"]])


def oracle(conn, metric, grain="overall", cohort_filter=None):
    if metric not in METRICS or grain not in GRAINS:
        raise ValueError("Unknown maintained metric or grain")
    dimensions = []
    if "month" in grain:
        dimensions.append("CAST(DATE_TRUNC('month', s.session_date) AS DATE) AS session_month")
    if "channel" in grain:
        dimensions.append("s.sales_channel")
    sql = (FIXTURE / "oracle" / f"{metric}.sql").read_text(encoding="utf-8").format(
        dimensions=", ".join(dimensions) + "," if dimensions else "",
        group_by="GROUP BY " + ", ".join(str(i + 1) for i in range(len(dimensions))) if dimensions else "",
        cohort_filter=f"AND ({cohort_filter})" if cohort_filter else "")
    cursor = conn.execute(sql)
    return [c[0] for c in cursor.description], cursor.fetchall(), sql


def cases():
    result = []
    for metric in METRICS:
        for grain in GRAINS:
            groups, aliases = [], []
            if "month" in grain:
                groups.append("metric_time__month")
                aliases.append(("metric_time__month", "session_month"))
            if "channel" in grain:
                groups.append("session__sales_channel")
                aliases.append(("session__sales_channel", "sales_channel"))
            result.append(Case(f"{metric}__{grain}", metric, tuple(groups), aliases=tuple(aliases)))
    for metric in METRICS:
        result.append(Case(f"{metric}__empty", metric, where=(
            "{{ TimeDimension('metric_time', 'DAY') }} < '2020-01-01'",)))
    result.append(Case("conversion_rate__nonconverting", "conversion_rate", where=(
        "{{ Dimension('session__session_date') }} = '2026-01-20'",)))
    return tuple(result)


def result_record(case_id, metric, reference, observed, *, path="native", evidence=(), provenance="MetricFlow-generated SQL"):
    executed = observed is not None and "rows" in observed
    comparison = compare(reference[0], reference[1], observed["columns"], observed["rows"],
                         [c for c in reference[0] if c != metric]) if executed else None
    return {"case_id": case_id, "business_question": QUESTIONS[metric],
            "source_platform": "MetricFlow" if path == "native" else "Authored SQL mutation",
            "target_platform": "DuckDB", "conversion_path": path,
            "execution_status": "EXECUTED" if executed else "NOT_EXECUTED",
            "semantic_status": "BOUNDED_MATCH" if comparison and comparison["equal"] else
                               "INJECTED_FAILURE" if path == "injected" else "UNKNOWN",
            "oracle_result": table(reference[0], reference[1]),
            "target_result": table(observed["columns"], observed["rows"]) if executed else None,
            "comparison_result": comparison, "evidence_reference": list(evidence), "provenance": provenance,
            "diagnostics": [] if executed else [observed.get("error", "No execution") if observed else "No execution"]}


def normalize(case, observed):
    if "rows" not in observed:
        return observed
    columns = [dict(case.aliases).get(c, c) for c in observed["columns"]]
    rows = []
    for row in observed["rows"]:
        values = list(row)
        for i, col in enumerate(columns):
            if col == "session_month" and isinstance(values[i], datetime):
                if values[i].time().isoformat() != "00:00:00" or values[i].tzinfo is not None:
                    raise ValueError("Unexpected non-midnight month value")
                values[i] = values[i].date()
        rows.append(tuple(values))
    return {**observed, "columns": columns, "rows": rows}


def prepare(run, edge=False):
    project = run / "dbt"
    shutil.copytree(FIXTURE / "dbt", project)
    profile = yaml.safe_load((project / "profile.template.yml").read_text())
    profile["conversion_demo"]["outputs"]["local"]["path"] = str(project / "benchmark.duckdb")
    (project / "profiles.yml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    with duckdb.connect(str(project / "benchmark.duckdb")) as conn:
        load_data(conn, edge=edge)
    env = dict(os.environ, DBT_SEND_ANONYMOUS_USAGE_STATS="false", DO_NOT_TRACK="1",
               DBT_PROFILES_DIR=str(project), DBT_PROJECT_DIR=str(project), DBT_USE_COLORS="false")
    done = subprocess.run([sys.executable, "-m", "dbt.cli.main", "build", "--no-partial-parse"],
                          cwd=project, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (run / "dbt-build.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    return project, done.returncode


def run_demo(edge=False):
    from dbt_metricflow.cli.cli_configuration import CLIConfiguration
    from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
    scratch = ROOT / ".tmp" / "conversion-demo"
    scratch.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=scratch))
    project, build_code = prepare(run, edge=edge)
    references = {}
    with duckdb.connect() as conn:
        load_data(conn, edge=edge)
        for case in cases():
            grain = case.id.split("__", 1)[1]
            predicate = ("s.session_date < DATE '2020-01-01'" if grain == "empty" else
                         "s.session_date = DATE '2026-01-20'" if grain == "nonconverting" else None)
            references[case.id] = oracle(conn, case.metric, grain if grain in GRAINS else "overall", predicate)
        baseline = {m: oracle(conn, m)[1][0][0] for m in METRICS}
        expected = [10, 4, 0.4, Decimal("420.00") if edge else Decimal("390.00"), 5 if edge else 4, 84.0 if edge else 97.5]
        reviewed_values_match = all(baseline[m] == v for m, v in zip(METRICS, expected))
        if not edge:
            reviewed_values_match = reviewed_values_match and all([
                dict(oracle(conn, "conversion_rate", "channel")[1]) == {"Direct": 0.5, "Indirect": 0.25},
                dict(oracle(conn, "paid_revenue", "channel")[1]) == {"Direct": Decimal("300"), "Indirect": Decimal("90")},
                sorted(row[-1] for row in oracle(conn, "eligible_sessions", "month")[1]) == [5, 5],
                [row[-1] for row in sorted(oracle(conn, "paid_revenue", "month")[1])] == [Decimal("270"), Decimal("120")],
            ])
        faults = []
        if not edge:
            for name in FAULTS:
                sql = (FIXTURE / "failures" / f"{name}.sql").read_text(encoding="utf-8")
                (run / "failures").mkdir(exist_ok=True)
                (run / "failures" / f"{name}.sql").write_text(sql, encoding="utf-8")
                cur = conn.execute(sql)
                observed = {"columns": [c[0] for c in cur.description], "rows": cur.fetchall()}
                ref = references["conversion_rate__channel" if name == "removed_channel_filter" else "conversion_rate__overall"]
                faults.append(result_record(name, "conversion_rate", ref, observed, path="injected",
                              evidence=[f"failures/{name}.sql"], provenance="Injected teaching SQL; not converter output"))
    for key, ref in references.items():
        (run / "oracle").mkdir(exist_ok=True)
        (run / "oracle" / f"{key}.sql").write_text(ref[2], encoding="utf-8")
    native, error = {}, None
    if build_code == 0:
        config = CLIConfiguration()
        try:
            config.setup(dbt_profiles_path=project, dbt_project_path=project, configure_file_logging=False)
            manifest = parse_manifest_from_dbt_generated_manifest((project / "target/semantic_manifest.json").read_text())
            (run / "source.manifest.json").write_text(manifest.json(by_alias=True, exclude_none=True, indent=2), encoding="utf-8")
            with duckdb.connect(str(project / "benchmark.duckdb")) as conn:
                native = compile_queries(manifest, config.sql_client, run / "native", conn, cases())
        except Exception as exc:
            error = str(exc)
        finally:
            if getattr(config, "dbt_artifacts", None):
                config.dbt_artifacts.adapter.cleanup_connections()
    else:
        error = "dbt build failed; inspect dbt-build.log"
    records = []
    for case in cases():
        observed = normalize(case, native.get(case.id, {"error": error or "Not executed"}))
        records.append(result_record(case.id, case.metric, references[case.id], observed,
                       evidence=[f"oracle/{case.id}.sql", f"native/{case.id}.sql"] if "rows" in observed else [f"oracle/{case.id}.sql"]))
    matches = sum(bool(r["comparison_result"] and r["comparison_result"]["equal"]) for r in records)
    report = {"schema_version": 1, "fixture": "conversion_demo", "edge_case": edge,
              "observation_cutoff": "2026-03-01 exclusive; order DATE snapshot, whole-order statuses",
              "attribution": "Each order belongs to its originating session; canonical month uses session_date",
              "tool_versions": {n: importlib.metadata.version(n) for n in ("duckdb", "metricflow", "dbt-core", "dbt-duckdb")},
              "cases": records, "controlled_failures": faults,
              "platform_execution": {"MetricFlow SQL -> DuckDB": "EXECUTED" if any(r["execution_status"] == "EXECUTED" for r in records) else "NOT_EXECUTED",
                                     "Apache Ossie conversion": "NOT_EXECUTED", "Databricks runtime": "NOT_EXECUTED", "Power BI/DAX runtime": "NOT_EXECUTED"},
              "stage_a": {"dbt_build_exit_code": build_code, "oracle_reviewed_values_match": reviewed_values_match,
                          "native_cases": len(records), "native_matches": matches,
                          "detected_failures": sum(r["comparison_result"]["equal"] is False for r in faults),
                          "accepted": build_code == 0 and reviewed_values_match and matches == len(records) and
                                      (edge or all(not r["comparison_result"]["equal"] for r in faults))}}
    report["input_sha256"] = {p.relative_to(FIXTURE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(FIXTURE.rglob("*")) if p.is_file()}
    report["artifact_sha256"] = {p.relative_to(run).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for d in (run / "native", run / "oracle", run / "failures") for p in sorted(d.glob("*.sql"))}
    if (run / "source.manifest.json").is_file():
        report["artifact_sha256"]["source.manifest.json"] = hashlib.sha256((run / "source.manifest.json").read_bytes()).hexdigest()
    report["implementation_sha256"] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in (Path(__file__), ROOT / "labs/semantic_interop/scripts/run_conversion_demo.py")}
    write_json(run / "report.json", report)
    return run, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--edge", action="store_true", help="Add a paid order to an already converted session")
    parser.add_argument("--capture", type=Path, help="Capture hash-bound artifacts in a NEW directory; refuses overwrites")
    args = parser.parse_args()
    run, report = run_demo(edge=args.edge)
    if args.capture:
        from .diagnostics import capture
        if not report["stage_a"]["accepted"]:
            raise RuntimeError("Stage A is not accepted; inspect the retained run before capturing")
        capture(run, args.capture)
    contract = json.loads((FIXTURE / "contract.json").read_text(encoding="utf-8"))
    print("SESSION COHORT CONVERSION - order cutoff 2026-03-01 exclusive; amounts EUR")
    for r in report["cases"]:
        if r["case_id"].endswith("__overall"):
            metric = r["case_id"].split("__")[0]
            result = r["oracle_result"]["rows"][0][0]["value"]
            print(f"{r['business_question']}\n  {metric}: oracle={result}; MetricFlow={r['semantic_status']}\n  Definition: {contract['metrics'][metric]['definition']}")
    def readable(encoded, metric):
        rows = []
        for row in encoded["rows"]:
            values = [c["value"] for c in row]
            value = values[-1]
            rendered = "NULL" if value is None else f"{float(value):.2%}" if metric == "conversion_rate" else str(value)
            rows.append(" / ".join(str(v) for v in values[:-1]) + (": " if len(values) > 1 else "") + rendered)
        return "; ".join(rows)
    for grain in ("channel", "month"):
        print(f"SQL oracle by {grain} (canonical session cohort):")
        for r in report["cases"]:
            if r["case_id"].endswith("__" + grain):
                print(f"  {r['case_id'].split('__')[0]}: {readable(r['oracle_result'], r['case_id'].split('__')[0])}")
    print("Features: eligibility/status filters; session relationship; session-month cohort; ratio aggregation")
    for r in report["controlled_failures"]:
        print(f"INJECTED {r['case_id']}: correct={readable(r['oracle_result'], 'conversion_rate')}; faulty={readable(r['target_result'], 'conversion_rate')}")
    print(json.dumps({"stage_a": report["stage_a"], "platform_execution": report["platform_execution"],
                      "report": (run / "report.json").relative_to(ROOT).as_posix()}, indent=2))
    return 1 if args.check and not report["stage_a"]["accepted"] else 0
