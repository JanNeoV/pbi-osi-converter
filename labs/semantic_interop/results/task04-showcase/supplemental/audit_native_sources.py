"""Independent source sanity review; neither Power BI nor Databricks execution."""
import hashlib
import importlib.util
import json
from pathlib import Path

import duckdb
import yaml

from semantic_lab.compare import compare, table
from semantic_lab.conversion_demo import FIXTURE, load_data, oracle

ROOT = FIXTURE.parents[3]
OUTPUT = ROOT / ".tmp/task04-full/source-audit.json"
DBX = FIXTURE / "targets/databricks/metric_view.yaml"
definition = yaml.safe_load(DBX.read_text(encoding="utf-8"))
measures = {item["name"]: item["expr"] for item in definition["measures"]}
dimensions = {item["name"]: item["expr"] for item in definition["dimensions"]}

report = {
    "purpose": "Independent authored native source contract sanity review",
    "databricks_runtime": "NOT_EXECUTED",
    "power_bi_runtime": "NOT_EXECUTED",
    "converter_execution": "NOT_EXECUTED",
    "databricks_source_sql_in_duckdb": [],
    "boundary": "Databricks YAML source body and measure expressions execute here in DuckDB only. This does not certify Databricks syntax, analysis, runtime results or converter output.",
    "input_sha256": {DBX.relative_to(ROOT).as_posix(): hashlib.sha256(DBX.read_bytes()).hexdigest()},
}
with duckdb.connect() as connection:
    load_data(connection)
    connection.execute("ATTACH ':memory:' AS demo")
    connection.execute("CREATE SCHEMA demo.semantic_interop")
    for name in ("sessions", "orders"):
        connection.execute(f"CREATE TABLE demo.semantic_interop.{name} AS SELECT * FROM raw_{name}")
    for metric in ("conversion_rate", "paid_revenue"):
        for grain in ("overall", "channel", "month", "month_channel", "empty"):
            groups = []
            if "month" in grain:
                groups.append("session_month")
            if "channel" in grain:
                groups.append("sales_channel")
            select = [f'{dimensions[name]} AS {name}' for name in groups]
            select.append(f'{measures[metric]} AS {metric}')
            condition = definition["filter"]
            if grain == "empty":
                condition += " AND session_date < DATE '2020-01-01'"
            sql = "SELECT " + ", ".join(select) + " FROM (" + definition["source"] + ") authored_source WHERE " + condition
            if groups:
                sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(groups)))
            cursor = connection.execute(sql)
            columns, rows = [column[0] for column in cursor.description], cursor.fetchall()
            expected_columns, expected_rows, expected_sql = oracle(connection, metric, "overall" if grain == "empty" else grain, "s.session_date < DATE '2020-01-01'" if grain == "empty" else None)
            comparison = compare(expected_columns, expected_rows, columns, rows, groups)
            report["databricks_source_sql_in_duckdb"].append({"case": f"{metric}__{grain}", "execution_engine": "DuckDB", "authored_sql": sql, "oracle_sql": expected_sql, "observed": table(columns, rows), "reference": table(expected_columns, expected_rows), "comparison": comparison})
    sql = "SELECT " + measures["conversion_rate"] + " AS conversion_rate FROM (" + definition["source"] + ") authored_source WHERE " + definition["filter"] + " AND session_date = DATE '2026-01-20'"
    cursor = connection.execute(sql)
    columns, rows = [column[0] for column in cursor.description], cursor.fetchall()
    expected_columns, expected_rows, expected_sql = oracle(connection, "conversion_rate", cohort_filter="s.session_date = DATE '2026-01-20'")
    report["databricks_source_sql_in_duckdb"].append({"case": "conversion_rate__nonconverting", "execution_engine": "DuckDB", "authored_sql": sql, "oracle_sql": expected_sql, "observed": table(columns, rows), "reference": table(expected_columns, expected_rows), "comparison": compare(expected_columns, expected_rows, columns, rows, [])})

script = ROOT / "labs/semantic_interop/scripts/run_powerbi_showcase.py"
spec = importlib.util.spec_from_file_location("review_authored_powerbi", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source = module.authored_input(False)
tables = {item["name"]: item for item in source["model"]["tables"]}
dax = {item["name"]: module.as_text(item["expression"]) for item in tables["Sessions"]["measures"]}
predicates = ('KEEPFILTERS(Sessions[is_eligible] = TRUE())', 'KEEPFILTERS(Sessions[session_date] >= DATE(2026, 1, 1))', 'KEEPFILTERS(Sessions[session_date] < DATE(2026, 3, 1))')
checks = {
    "both_raw_csvs_used": all(any(f'/{name}.csv' in module.as_text(expression["expression"]) for expression in source["model"]["expressions"]) for name in ("sessions", "orders")),
    "cohort_and_eligibility_intersect_context": all(all(predicate in dax[measure] for predicate in predicates) for measure in ("Eligible Sessions", "Converted Sessions", "Paid Revenue")),
    "distinct_session_denominator": "DISTINCTCOUNT(Sessions[session_id])" in dax["Eligible Sessions"],
    "distinct_session_numerator": "DISTINCTCOUNT(Orders[session_id])" in dax["Converted Sessions"],
    "paid_status_and_exclusive_order_cutoff": all('KEEPFILTERS(Orders[order_status] = "paid")' in dax[measure] and 'KEEPFILTERS(Orders[order_date] < DATE(2026, 3, 1))' in dax[measure] for measure in ("Converted Sessions", "Paid Revenue")),
    "counts_coalesce_zero": all(dax[measure].startswith("COALESCE(") and dax[measure].rstrip().endswith("0\n)") for measure in ("Eligible Sessions", "Converted Sessions")),
    "sum_revenue_not_coalesced": "SUM(Orders[amount_eur])" in dax["Paid Revenue"] and "COALESCE" not in dax["Paid Revenue"],
    "aggregate_ratio_without_alternate": dax["Conversion Rate"] == "DIVIDE([Converted Sessions], [Eligible Sessions])",
    "session_cohort_month": any('Date.StartOfMonth([session_date])' in module.as_text(expression["expression"]) for expression in source["model"]["expressions"]),
    "active_many_to_one_single_direction": all(relation["isActive"] and relation["crossFilteringBehavior"] == "oneDirection" and relation["fromCardinality"] == "many" and relation["toCardinality"] == "one" for relation in source["model"]["relationships"]),
    "orders_filter_from_originating_sessions": any(relation["fromTable"] == "Orders" and relation["toTable"] == "Sessions" and relation["fromColumn"] == relation["toColumn"] == "session_id" for relation in source["model"]["relationships"]),
}
report["power_bi_static_contract_inspection"] = {"execution_engine": None, "runtime": "NOT_EXECUTED", "checks": checks, "dax": dax, "boundary": "Text and metadata inspection support equivalently authored intent, not DAX validity, refresh or numerical results."}
report["databricks_duckdb_matches"] = sum(item["comparison"]["equal"] for item in report["databricks_source_sql_in_duckdb"])
report["power_bi_static_checks_passed"] = sum(checks.values())
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"databricks_source_cases_matching_in_duckdb": report["databricks_duckdb_matches"], "cases": len(report["databricks_source_sql_in_duckdb"]), "power_bi_static_checks_passed": report["power_bi_static_checks_passed"], "static_checks": len(checks), "report": str(OUTPUT)}, indent=2))
assert report["databricks_duckdb_matches"] == 11
assert all(checks.values()), checks
