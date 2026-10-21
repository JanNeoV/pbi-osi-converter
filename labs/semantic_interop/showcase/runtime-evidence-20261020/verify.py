"""Verify published captures against each other and freshly executed DuckDB SQL.

Run with the frozen lab environment from any directory. This verifies retained
evidence; it does not launch Power BI or claim a fresh DAX execution.
"""
from pathlib import Path
import hashlib
import json

import duckdb

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def verify():
    hashes = read("sha256.json")
    for name, expected in hashes.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    oracle = read("oracle.json")
    conn = duckdb.connect()
    fixture = REPO / "labs/semantic_interop/fixtures/conversion_demo"
    schemas = {
        "sessions": {"session_id": "INTEGER", "session_date": "DATE", "sales_channel": "VARCHAR", "is_eligible": "BOOLEAN", "exclusion_reason": "VARCHAR"},
        "orders": {"order_id": "INTEGER", "session_id": "INTEGER", "order_date": "DATE", "amount_eur": "DECIMAL(18,2)", "order_status": "VARCHAR"},
    }
    for table in ("sessions", "orders"):
        csv = fixture / "data" / f"{table}.csv"
        conn.execute(f"CREATE TABLE raw_{table} AS SELECT * FROM read_csv(?, columns=?, header=true, nullstr='')", [str(csv), schemas[table]])
    expected = {}
    for name, query in oracle["headline_queries"].items():
        rows = conn.execute((ROOT / query["sql"]).read_text()).fetchall()
        captured = query["typed_result"]["rows"]
        if name.endswith("__channel"):
            rows = sorted(rows, key=lambda row: row[0])
            captured = sorted(captured, key=lambda row: row[0]["value"])
        assert len(rows) == len(captured), name
        for actual, saved in zip(rows, captured):
            assert float(actual[-1]) == float(saved[-1]["value"]), name
            context = "Overall" if name.endswith("__overall") else actual[0]
            if len(actual) > 1:
                assert actual[0] == saved[0]["value"], name
            metric = "[Conversion Rate]" if name.startswith("conversion_rate") else "[Paid Revenue]"
            expected.setdefault(context, {})[metric] = float(actual[-1])
    for variant in ("native-core", "roundtrip-core"):
        d = read(variant + ".json")
        queries = {q["name"]: q for q in d["queries"]}
        assert all(q["status"] == "EXECUTED" for q in queries.values())
        assert queries["counts"]["rows"] == [{"[Sessions]": 12, "[Orders]": 6}]
        assert queries["headline"]["rows"] == [expected["Overall"]]
        for row in queries["channels"]["rows"]:
            assert {k: v for k, v in row.items() if k != "Sales Channel[sales_channel]"} == expected[row["Sales Channel[sales_channel]"]]
        assert len(queries["channels"]["rows"]) == 2
        saved_comparison = read("comparison.json")["runtime"][variant]
        assert saved_comparison["equal"] and saved_comparison["all_queries_executed"]
        assert saved_comparison["live_dax_equal"] and saved_comparison["live_format_strings_equal"]
    comparison = read("comparison.json")
    assert all(comparison["model_bytes_unchanged"].values())
    assert all(all(checks.values()) for checks in comparison["static"].values())
    experimental = read("selector-experimental.json")
    assert set(experimental["executions"]) == {"native-selector", "roundtrip-selector", "roundtrip-changed-default"}
    assert experimental["attempts"][0]["refresh"] == "FAIL"
    for variant, d in experimental["executions"].items():
        queries = {q["name"]: q for q in d["queries"]}
        assert len(queries) == 25
        assert d["dynamic_format_metadata_matches_submitted"]
        assert d["live_business_dax_matches_submitted"]
        assert all(q["status"] == "EXECUTED" for q in queries.values())
        assert queries["counts"]["rows"] == [{"[Sessions]": 12, "[Orders]": 6}]
        for context, values in expected.items():
            for selection in ("none", "rate", "revenue", "multiple"):
                revenue = selection == "revenue" or ("changed-default" in variant and selection in ("none", "multiple"))
                selected = values["[Paid Revenue]" if revenue else "[Conversion Rate]"]
                assert queries[f"{context}-{selection}"]["rows"] == [{**values, "[Selected KPI]": selected}], (variant, context, selection)
                fmt = '#,0.00 "EUR"' if revenue else "0.0%"
                assert queries[f"format-{context}-{selection}"]["rows"] == [{"[Format expression]": fmt}]
    print("PASS: package hashes, fresh DuckDB oracle, 12 core values, 36 repaired selector contexts and 36 separate format-expression evaluations; native changed-default refresh FAIL retained")


if __name__ == "__main__":
    verify()
