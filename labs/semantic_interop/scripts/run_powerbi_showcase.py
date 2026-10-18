"""Task 04 fixture-only Power BI -> Ossie -> Power BI offline experiment.

This explicitly authors minimal TMSL inputs from the accepted teaching candidate.
It is not a TMDL converter, DAX evaluator, or replacement for official converters.
"""
import argparse
import hashlib
import importlib.metadata
import io
import json
import logging
from pathlib import Path
import re
import tempfile
import textwrap
import warnings

import duckdb
import yaml

from semantic_lab.compare import table
from semantic_lab.conversion_demo import FIXTURE, load_data, oracle
from semantic_lab.fixtures import OSSIE_COMMIT, ROOT
from semantic_lab.integrations import write_json


TARGET = FIXTURE / "targets/powerbi"
DEFINITION = TARGET / "ConversionDemo.SemanticModel/definition"
HEADLINES = ("Conversion Rate", "Paid Revenue")
HELPERS = ("Eligible Sessions", "Converted Sessions")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure(name):
    """Copy only the named accepted fixture measure's DAX and formatting."""
    source = (DEFINITION / "tables/Sessions.tmdl").read_text(encoding="utf-8")
    match = re.search(r"\tmeasure '" + re.escape(name) + r"' =([^\n]*)(.*?)(?=\n\t(?:\S|$)|\Z)", source, re.S)
    if match is None:
        raise ValueError(f"Accepted fixture measure not found: {name}")
    first, body = match.groups()
    dax_body, properties = body.split("\n\t\tformatString:", 1)
    expression = first.strip() or textwrap.dedent(dax_body).strip()
    result = {"name": name, "expression": expression,
              "formatString": properties.splitlines()[0].strip()}
    if name in HELPERS:
        result["isHidden"] = True
    if name == "Selected KPI":
        # Task 04 intentionally has exactly two business choices. The accepted
        # three-choice Task 03 candidate remains byte-for-byte unchanged.
        result["expression"] = expression.replace('    "Eligible Sessions", [Eligible Sessions],\n', '')
        formatting = properties.split("\t\tformatStringDefinition =", 1)[1].split("\n\t\tdisplayFolder:", 1)[0]
        result["formatStringDefinition"] = {"expression": textwrap.dedent(formatting).strip().replace('    "Eligible Sessions", "#,0",\n', '')}
    return result


def column(name, datatype, **extra):
    return {"name": name, "dataType": datatype, "sourceColumn": name,
            "summarizeBy": "none", **extra}


def authored_input(dynamic):
    """Explicit fixture transcription; no inferred relationships or DAX translation."""
    expressions = (DEFINITION / "expressions.tmdl").read_text(encoding="utf-8")
    named_m = []
    for name in ("FixtureRoot", "SessionsData", "OrdersData"):
        match = re.search(r"^expression " + name + r" =([^\n]*)(.*?)(?=\n///|\nexpression |\Z)", expressions, re.S | re.M)
        first, body = match.groups()
        named_m.append({"name": name, "kind": "m", "expression": first.strip() or textwrap.dedent(body).strip()})
    sessions = [column("session_id", "int64", isKey=True),
                column("session_date", "dateTime", formatString="yyyy-MM-dd"),
                column("session_month", "dateTime", formatString="yyyy-MM"),
                column("sales_channel", "string", isHidden=True),
                column("is_eligible", "boolean"), column("exclusion_reason", "string")]
    orders = [column("order_id", "int64", isKey=True), column("session_id", "int64"),
              column("order_date", "dateTime", formatString="yyyy-MM-dd"),
              column("amount_eur", "decimal", summarizeBy="sum", formatString='#,0.00 "EUR"'),
              column("order_status", "string")]
    def business_table(name, columns, m):
        return {"name": name, "columns": columns,
                "partitions": [{"name": name, "mode": "import", "source": {"type": "m", "expression": m}}]}
    tables = [business_table("Sessions", sessions, "SessionsData"),
              business_table("Orders", orders, "OrdersData"),
              business_table("Sales Channel", [column("sales_channel", "string", isKey=True)],
                             'Table.Distinct(Table.SelectColumns(SessionsData, {"sales_channel"}))')]
    tables[0]["measures"] = [measure(n) for n in (*HELPERS, *HEADLINES)]
    if dynamic:
        tables.append(business_table("KPI Selector", [column("KPI", "string")],
                                     '#table(type table [KPI = text], {{"Conversion Rate"}, {"Paid Revenue"}})'))
        tables[0]["measures"].append(measure("Selected KPI"))
    relationships = [
        {"name": "sessions_channel", "fromTable": "Sessions", "fromColumn": "sales_channel",
         "toTable": "Sales Channel", "toColumn": "sales_channel"},
        {"name": "orders_session", "fromTable": "Orders", "fromColumn": "session_id",
         "toTable": "Sessions", "toColumn": "session_id"},
    ]
    for relation in relationships:
        relation.update(fromCardinality="many", toCardinality="one", crossFilteringBehavior="oneDirection", isActive=True)
    return {"name": "ConversionDemoSwitch" if dynamic else "ConversionDemoCore", "compatibilityLevel": 1601,
            "model": {"culture": "en-US", "sourceQueryCulture": "en-US",
                      "defaultPowerBIDataSourceVersion": "powerBI_V3", "expressions": named_m,
                      "tables": tables, "relationships": relationships}}


def as_text(value):
    return "\n".join(value) if isinstance(value, list) else value


def normalized(value):
    """Ignore legal TMSL string/line-array serialization and named-object ordering."""
    if isinstance(value, dict):
        return {k: as_text(v) if k == "expression" else normalized(v) for k, v in value.items()}
    if isinstance(value, list):
        result = [normalized(v) for v in value]
        return sorted(result, key=lambda v: v["name"]) if all(isinstance(v, dict) and "name" in v for v in result) else result
    return value


def bounded_checks(source, returned):
    left, right = normalized(source), normalized(returned)
    lt = {t["name"]: t for t in left["model"]["tables"]}
    rt = {t["name"]: t for t in right["model"]["tables"]}
    lm = {m["name"]: m for t in lt.values() for m in t.get("measures", [])}
    rm = {m["name"]: m for t in rt.values() for m in t.get("measures", [])}
    dependencies = {name: sorted(set(re.findall(r"\[([^\]]+)\]", m["expression"])) & lm.keys()) for name, m in lm.items()}
    checks = {"table_names_equal": lt.keys() == rt.keys(), "measure_names_equal": lm.keys() == rm.keys(),
              "dax_expressions_equal": all(m["expression"] == rm.get(n, {}).get("expression") for n, m in lm.items()),
              "complete_measure_properties_equal": lm == rm,
              "relationships_equal_including_filter_direction": left["model"]["relationships"] == right["model"]["relationships"],
              "columns_equal": all(t["columns"] == rt.get(n, {}).get("columns") for n, t in lt.items()),
              "partitions_equal": all(t["partitions"] == rt.get(n, {}).get("partitions") for n, t in lt.items()),
              "m_expressions_equal": left["model"]["expressions"] == right["model"].get("expressions"),
              "complete_normalized_document_equal": left == right}
    if "KPI Selector" in lt:
        checks["selector_disconnected"] = not any("KPI Selector" in (r["fromTable"], r["toTable"]) for r in right["model"]["relationships"])
        checks["exactly_two_selector_choices"] = rt["KPI Selector"]["partitions"][0]["source"]["expression"].count('{"') == 2
    return checks, dependencies


def tom_probe(document):
    from ossie_microsoft import TomUnavailableError, validate_tmsl
    try:
        result = validate_tmsl(document)
        return {"status": "EXECUTED", "deserialized": result.deserialized, "valid": result.is_valid,
                "errors": [{"message": e.message, "source": e.source} for e in result.errors]}
    except TomUnavailableError as exc:
        return {"status": "NOT_EXECUTED", "reason": str(exc)}


def main():
    from ossie import OssieDocument
    from ossie_microsoft import convert_ossie_to_semantic_model, convert_semantic_model_to_ossie
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="NEW evidence directory; existing paths are refused")
    args = parser.parse_args()
    if args.output:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
    else:
        scratch = ROOT / ".tmp/task04-powerbi"
        scratch.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="run-", dir=scratch))
    inputs = [*DEFINITION.rglob("*.tmdl"), FIXTURE / "data/sessions.csv", FIXTURE / "data/orders.csv",
              FIXTURE / "contract.json", FIXTURE / "oracle/conversion_rate.sql", FIXTURE / "oracle/paid_revenue.sql",
              TARGET / "validate-task04.dax", Path(__file__)]
    report = {"experiment": "Task 04 Power BI-origin official round trip", "ossie_commit": OSSIE_COMMIT,
              "converter_versions": {n: importlib.metadata.version(n) for n in ("apache-ossie", "apache-ossie-microsoft")},
              "input_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(inputs)}, "cases": {}}
    # These remain independent expected results. No DAX output is manufactured.
    report["duckdb_oracle"] = {}
    report["planned_selector_tests"] = []
    with duckdb.connect() as conn:
        load_data(conn)
        for metric in ("conversion_rate", "paid_revenue"):
            for grain in ("overall", "channel", "month", "month_channel"):
                columns, rows, sql = oracle(conn, metric, grain)
                key = f"{metric}__{grain}"
                (output / "oracle").mkdir(exist_ok=True)
                (output / "oracle" / f"{key}.sql").write_text(sql, encoding="utf-8")
                report["duckdb_oracle"][key] = table(columns, rows)
        for channel in (None, "Direct", "Indirect"):
            predicate = f"s.sales_channel = '{channel}'" if channel else None
            for selection in ("none", "Conversion Rate", "Paid Revenue", "multiple"):
                metric = "paid_revenue" if selection == "Paid Revenue" else "conversion_rate"
                value = oracle(conn, metric, cohort_filter=predicate)[1][0][0]
                report["planned_selector_tests"].append({"selection": selection, "channel": channel or "overall",
                    "expected_from_duckdb": float(value), "unit": "EUR" if metric == "paid_revenue" else "ratio",
                    "DAX_execution": "NOT_EXECUTED", "actual_DAX_value": None, "oracle_comparison": "NOT_EXECUTED"})
    for case, dynamic in (("case-a", False), ("case-b", True)):
        directory = output / case
        directory.mkdir()
        source = authored_input(dynamic)
        write_json(directory / "authored.model.bim", source)
        log = io.StringIO()
        logger = logging.getLogger("ossie_microsoft")
        handler = logging.StreamHandler(log)
        logger.addHandler(handler)
        try:
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                encoded = convert_semantic_model_to_ossie(source)
                (directory / "generated.ossie.yml").write_text(encoded, encoding="utf-8")
                document = OssieDocument.model_validate(yaml.safe_load(encoded))
                returned = convert_ossie_to_semantic_model(encoded)
                write_json(directory / "reconstructed.model.bim", returned)
        finally:
            logger.removeHandler(handler)
            (directory / "converter.log").write_text(log.getvalue(), encoding="utf-8")
        notices = [{"category": w.category.__name__, "message": str(w.message)} for w in captured]
        write_json(directory / "warnings.json", notices)
        checks, dependencies = bounded_checks(source, returned)
        metrics = document.model_dump(mode="json", exclude_none=True).get("metrics", [])
        record = {"conversion_completed": True, "ossie_pydantic_structure_valid": True,
                  "bounded_structural_checks": checks, "measure_dependencies": dependencies,
                  "metric_dialects": {m["name"]: [x["dialect"] for x in m["expression"]["dialects"]] for m in metrics},
                  "power_bi_extensions_present": "vendor_name: POWER_BI" in encoded,
                  "source_TOM_validation": tom_probe(source), "reconstructed_TOM_validation": tom_probe(returned),
                  "DAX_execution": "NOT_EXECUTED", "oracle_comparison": "NOT_EXECUTED",
                  "visual_formatting": "NOT_EXECUTED", "warnings": notices,
                  "semantics": "Bounded DAX, relationship, M and format metadata preserved; engine semantics NOT_EXECUTED",
                  "provenance": "Explicitly authored minimal TMSL input; unmodified official converters; generated output unedited"}
        report["cases"][case] = record
        write_json(directory / "validation.json", record)
    report["artifact_sha256"] = {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()}
    write_json(output / "report.json", report)
    print(json.dumps({"output": output.relative_to(ROOT).as_posix(), "cases": report["cases"]}, indent=2))
    return 0 if all(all(c["bounded_structural_checks"].values()) for c in report["cases"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
