"""Execute one minimized case without dbt build or the full benchmark; no network."""
import argparse
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import duckdb
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest

from semantic_lab.diagnostics import environment, digest
from semantic_lab.fixtures import FIXTURES, LAB, ROOT
from semantic_lab.integrations import write_json
from semantic_lab.regressions import evaluate_small


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=["entity_alias", "compound_aggregate", "metric_filter"])
    parser.add_argument("--expect", choices=["failure", "match"], default="failure")
    parser.add_argument("--source", type=Path, help="Override the captured one-row source MSI manifest")
    args = parser.parse_args()
    source_path = args.source or LAB / "results/task02/verified-001/regressions" / args.case / "one_row/source.json"
    source = PydanticSemanticManifest.parse_raw(source_path.read_text())
    fixture = json.loads((FIXTURES / "regressions/cases.json").read_text())
    spec = next(s for s in fixture["cases"] if s["id"] == args.case)
    scratch = Path(tempfile.mkdtemp(prefix="semantic-counterexample-", dir=ROOT / ".tmp"))
    # explain() uses the official renderer; execution is independently performed by DuckDB.
    client = SimpleNamespace(sql_engine_type=SqlEngine.DUCKDB, sql_plan_renderer=DuckDbSqlPlanRenderer())
    with duckdb.connect(str(scratch / "benchmark.duckdb")) as conn:
        conn.execute("CREATE TABLE regression_items (item_id INTEGER, order_date DATE, status VARCHAR, amount DECIMAL(18,2))")
        row = fixture["rows"][0]
        conn.execute("INSERT INTO regression_items VALUES (?, ?, ?, ?)", [row["item_id"], row["order_date"], row["status"], row["amount"]])
        conn.execute("CREATE TABLE time_spine AS SELECT DATE '2026-01-01' AS date_day")
        result = evaluate_small(source, spec, SimpleNamespace(sql_client=client), conn, scratch / "evidence")
    result["environment"] = environment()
    result["source_sha256"] = digest(source_path)
    result["artifact_sha256"] = {p.relative_to(scratch).as_posix(): digest(p) for p in sorted((scratch / "evidence").rglob("*")) if p.is_file()}
    write_json(scratch / "result.json", result)
    print(json.dumps({"result": (scratch / "result.json").relative_to(ROOT).as_posix(),
                      "native_matches_oracle": result["native_matches_oracle"],
                      "returned_executed": result["returned_executed"], "returned_matches_oracle": result["returned_matches_oracle"]}, indent=2))
    return 0 if result["native_matches_oracle"] and result["returned_matches_oracle"] == (args.expect == "match") else 1


if __name__ == "__main__":
    raise SystemExit(main())
