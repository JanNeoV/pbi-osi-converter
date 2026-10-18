from collections import Counter
from datetime import datetime, time
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import platform
import tempfile

import duckdb

from .compare import compare, status, table
from .fixtures import CASES, FIXTURES, LAB, ROOT, OSSIE_COMMIT, load_data, oracle
from .integrations import prepare, run_upstreams, write_json
from .semantics import inspect_manifest


def sanitize(value):
    if isinstance(value, str):
        for path, label in ((ROOT, "<repository>"), (Path.home(), "<home>")):
            value = value.replace(str(path), label).replace(path.as_posix(), label)
        return value
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    return value


def record(case_id, metric, path, source, target, method, reference=None):
    return {"case_id": case_id, "metric_id": metric, "path": path,
            "scenario": "baseline", "source_representation": source, "target_representation": target,
            "method": method, "evidence_types": [], "structural_validity": None,
            "semantic_preservation": None, "semantic_coverage": {}, "unsupported_constructs": [],
            "sql_execution_available": False, "execution_status": "NOT_EXECUTED",
            "reference": reference, "observed": None, "comparison": None,
            "diagnostics": [], "artifacts": [], "status": "NOT_EXECUTED",
            "controlled_failure": False, "failure_detection": None}


def finalize(r):
    r["status"] = status(structural=r["structural_validity"], unsupported=bool(r["unsupported_constructs"]),
                         semantic=r["semantic_preservation"], executed=r["execution_status"] == "EXECUTED",
                         comparison=r["comparison"])
    if r["controlled_failure"]:
        r["failure_detection"] = (
            "DETECTED" if r["status"] in {"NUMERICAL_MISMATCH", "SEMANTIC_MISMATCH", "INVALID_MODEL", "UNSUPPORTED"}
            else "UNDETECTED" if r["execution_status"] == "EXECUTED" else "UNTESTABLE"
        )
    return r


def normalized_rows(case, columns, rows):
    aliases = dict(case.aliases)
    normalized_columns = [aliases.get(c, c) for c in columns]
    output = []
    for row in rows:
        values = list(row)
        for i, col in enumerate(normalized_columns):
            if col == "order_date" and isinstance(values[i], datetime):
                if values[i].time() != time() or values[i].tzinfo is not None:
                    raise ValueError("Date contract received a non-midnight or zoned timestamp")
                values[i] = values[i].date()
        output.append(tuple(values))
    return normalized_columns, output


def numeric_record(r, columns, rows, expected_columns, expected_rows, keys=()):
    r["observed"] = table(columns, rows)
    r["execution_status"] = "EXECUTED"
    r["sql_execution_available"] = True
    r["comparison"] = compare(expected_columns, expected_rows, columns, rows, keys)
    r["evidence_types"].append("independent_duckdb_execution")


def upstream_records(output, references):
    records = []
    inspections = {}
    for name, manifest_key in (("native", "manifest"), ("roundtrip", "roundtrip_manifest")):
        if manifest_key in output:
            inspections[name] = inspect_manifest(output[manifest_key])
        inspection = inspections.get(name)
        for case in CASES:
            cols, rows, _ = references[case.id]
            r = record(case.id, case.metric, name, "dbt MetricFlow YAML", "DuckDB result",
                       "MetricFlowEngine.explain -> DuckDB" if name == "native" else
                       "MSIToOssieConverter -> OssieToMSIConverter -> MetricFlowEngine.explain -> DuckDB",
                       table(cols, rows))
            r["query"] = {"dimensions": list(case.groups), "filters": list(case.where), "aliases": dict(case.aliases)}
            r["evidence_types"] = ["native_compilation"] if name == "native" else ["real_conversion", "native_compilation_attempt"]
            r["artifacts"] = [f"{name}/{case.id}.sql"] if "sql" in output.get(name, {}).get(case.id, {}) else []
            r["structural_validity"] = inspection is not None
            if inspection:
                r["semantic_coverage"] = {"scope": inspection["scope"], "metric": inspection["metrics"][case.metric],
                                           "models": inspection["models"]}
                r["semantic_preservation"] = (inspection["metrics"][case.metric]["equal"] and
                                                all(v["equal"] for v in inspection["models"].values()))
            if name == "roundtrip":
                r["artifacts"] += ["converted.ossie.yml", "roundtrip.manifest.json", "semantic-inspection.json"]
                r["diagnostics"] += output.get("issues", [])
                r["upstream_issue_reporting"] = "REPORTED" if output.get("issues") else "NO_ISSUES_REPORTED"
                if r["semantic_preservation"] is False:
                    r["failure_detection"] = "DETECTED"
            observed = output.get(name, {}).get(case.id, {})
            if "rows" in observed:
                actual_cols, actual_rows = normalized_rows(case, observed["columns"], observed["rows"])
                numeric_record(r, actual_cols, actual_rows, cols, rows, [c for c in cols if c != case.metric])
            else:
                r["diagnostics"].append(observed.get("error") or output.get("conversion_error") or output.get("error") or "No output")
            records.append(finalize(r))
    return records, inspections


def microsoft_records(output, references):
    ms = output.get("microsoft", {})
    measures = {m["name"]: m for t in ms.get("bim", {}).get("model", {}).get("tables", []) for m in t.get("measures", [])}
    records = []
    for case in CASES[:8]:
        if any(r["metric_id"] == case.metric for r in records):
            continue
        cols, rows, _ = references[case.id]
        r = record(case.id, case.metric, "microsoft", "generated Ossie", "Microsoft TMSL", "official Ossie Microsoft export and import", table(cols, rows))
        r["evidence_types"] = ["real_conversion", "static_semantic_inspection"]
        r["artifacts"] = ["converted.model.bim", "microsoft-returned.ossie.yml", "microsoft-diagnostics.json"]
        measure = measures.get(case.metric)
        r["semantic_coverage"] = {"dax": measure.get("expression") if measure else None,
                                   "returned_ossie_pydantic_valid": ms.get("returned_valid"),
                                   "TOM_validation": "NOT_EXECUTED", "DAX_execution": "NOT_EXECUTED"}
        r["diagnostics"] = [w for w in ms.get("warnings", []) if f"'{case.metric}'" in w]
        if ms.get("error"):
            r["diagnostics"].append(ms["error"])
        if measure and measure.get("expression") == "BLANK()":
            r["unsupported_constructs"] = ["SQL expression replaced by annotated BLANK()"]
            r["semantic_preservation"] = False
        r["diagnostics"].append("TMSL export is not a refreshed Power BI model; partitions contain placeholder source IDs.")
        records.append(finalize(r))
    r = record("relationship_preservation", None, "microsoft", "generated Ossie", "Microsoft TMSL", "official converter plus endpoint inventory")
    wanted = output.get("ossie_document", {}).get("relationships", [])
    actual = ms.get("bim", {}).get("model", {}).get("relationships", [])
    r["semantic_coverage"] = {"source_relationships": wanted, "target_relationships": actual}
    r["evidence_types"] = ["real_conversion", "static_semantic_inspection"]
    r["diagnostics"] = [w for w in ms.get("warnings", []) if "relationship" in w]
    if wanted and not actual:
        r["semantic_preservation"] = False
    r["artifacts"] = ["converted.model.bim", "microsoft-diagnostics.json"]
    records.append(finalize(r))
    return records


MUTATIONS = {
    "sum_instead_of_distinct": "purchasing_customers", "item_grain_count": "order_count",
    "join_fanout": "total_revenue", "removed_filter": "completed_revenue",
    "average_of_ratios": "completed_revenue_share", "filter_propagation": "completed_revenue",
}


def controlled_records(conn, references):
    records = []
    cases = {c.id: c for c in CASES}
    for mutation, reference_id in MUTATIONS.items():
        case = cases[reference_id]
        cols, rows, _ = references[reference_id]
        r = record(mutation, case.metric, "controlled_sql", "independently authored faulty SQL", "DuckDB result",
                   "deliberate mutation SQL execution; not a converter defect", table(cols, rows))
        r.update(controlled_failure=True, scenario=mutation, structural_validity=True)
        sql = (FIXTURES / "mutations" / f"{mutation}.sql").read_text()
        cursor = conn.execute(sql)
        numeric_record(r, [c[0] for c in cursor.description], cursor.fetchall(), cols, rows)
        r["artifacts"] = [f"fixtures/mutations/{mutation}.sql"]
        records.append(finalize(r))
    r = record("duplicate_dimension", None, "controlled_data", "synthetic duplicate key", "data integrity", "duplicate-key query")
    r.update(controlled_failure=True, scenario="duplicate_dimension", evidence_types=["independent_sql_integrity_check"])
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute((FIXTURES / "mutations/duplicate_dimension.sql").read_text())
        duplicates = conn.execute("SELECT product_id, COUNT(*) AS matches FROM raw_products GROUP BY product_id HAVING COUNT(*) > 1").fetchall()
        r["structural_validity"] = not duplicates
        r["semantic_coverage"] = {"contract": "products.product_id is unique", "duplicate_keys": table(["product_id", "matches"], duplicates)}
        r["diagnostics"] = ["Reject invalid dimension cardinality before interpreting metric equivalence."] if duplicates else []
    finally:
        conn.execute("ROLLBACK")
    records.append(finalize(r))
    return records


def tmdl_records(run):
    from semantic_poc.agent.powerbi_import import extract_powerbi_inventory, analyze_import_relationships
    inventory = extract_powerbi_inventory(FIXTURES / "powerbi/Shop.SemanticModel/definition", ROOT)
    write_json(run / "tmdl-inventory.json", inventory.to_dict())
    records = []
    for measure in inventory.measures:
        r = record(measure.name, measure.name, "tmdl_static", "manually authored TMDL fixture", "bounded DAX analysis",
                   "existing repository extract_powerbi_inventory; not official TOM")
        r["evidence_types"] = ["static_semantic_inspection"]
        r["semantic_coverage"] = measure.analysis.to_dict()
        r["artifacts"] = ["tmdl-inventory.json"]
        r["diagnostics"] = [d.to_dict() for d in measure.analysis.diagnostics]
        if not measure.analysis.supported:
            r["unsupported_constructs"] = ["Outside existing bounded DAX grammar"]
        if measure.name in {"context_sensitive", "shipping_revenue"}:
            r["controlled_failure"] = True
            r["scenario"] = "unsupported_context"
        records.append(finalize(r))
    # Deliberately claim an active shipping relationship, then verify the existing checker catches the drift.
    findings = analyze_import_relationships(inventory, [{"name": "ship_date", "from_table": "Orders", "from_column": "ship_date",
                                                        "to_table": "Calendar", "to_column": "date_day", "is_active": True}])
    r = record("inactive_relationship", None, "tmdl_static", "active-relationship expectation", "authored inactive TMDL relationship", "existing analyze_import_relationships")
    r.update(controlled_failure=True, scenario="inactive_relationship", evidence_types=["static_semantic_inspection"])
    r["diagnostics"] = [f.to_dict() for f in findings]
    r["semantic_preservation"] = not any(f.code == "PBI_RELATIONSHIP_PROPERTY_MISMATCH" for f in findings)
    r["artifacts"] = ["tmdl-inventory.json"]
    records.append(finalize(r))
    return records


def acceptance(report):
    records = report["cases"]
    failures = []
    native = [r for r in records if r["path"] == "native"]
    if len(native) != len(CASES) or any(r["status"] != "PASS" for r in native):
        failures.append("All 14 native MetricFlow cases must match the independent oracle and bounded contract.")
    if not report["integration"]["ossie_conversion_executed"]:
        failures.append("Official Ossie conversion must execute successfully.")
    if not report["integration"]["microsoft_conversion_executed"]:
        failures.append("Official Microsoft conversion must execute successfully.")
    controls = [r for r in records if r["controlled_failure"]]
    if len(controls) != 10 or any(r["failure_detection"] != "DETECTED" for r in controls):
        failures.append("All ten declared controls must be detected; no untestable control is accepted.")
    if not any(r["status"] == "NUMERICAL_MISMATCH" and r["controlled_failure"] for r in records):
        failures.append("A numerical failure must be demonstrated.")
    if any(r["status"] == "PASS" and (r["execution_status"] != "EXECUTED" or not r["comparison"] or not r["comparison"]["equal"]) for r in records):
        failures.append("PASS without numerical evidence is forbidden.")
    return {"accepted": not failures, "failures": failures}


def benchmark():
    os.environ.update(DBT_SEND_ANONYMOUS_USAGE_STATS="false", DO_NOT_TRACK="1")
    logging.getLogger("ossie_microsoft").setLevel(logging.ERROR)
    output_root = ROOT / ".tmp/semantic-lab"
    output_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=output_root))
    records, output = [], {}
    with duckdb.connect() as oracle_db:
        load_data(oracle_db)
        references = {case.id: oracle(oracle_db, case) for case in CASES}
        for case in CASES:
            cols, rows, sql = references[case.id]
            write_json(run / "oracle" / f"{case.id}.json", table(cols, rows))
        try:
            project, exit_code = prepare(run)
            if exit_code:
                raise RuntimeError(f"dbt build exited {exit_code}; see dbt-build.log")
            output = run_upstreams(project, run)
        except Exception as exc:
            output["error"] = str(exc)
        upstream, inspections = upstream_records(output, references)
        records.extend(upstream)
        write_json(run / "semantic-inspection.json", sanitize(inspections))
        write_json(run / "microsoft-diagnostics.json", sanitize(output.get("microsoft", {})))
        if "manifest" in output:
            write_json(run / "source.manifest.normalized.json", json.loads(output["manifest"].json()))
        records.extend(microsoft_records(output, references))
        records.extend(controlled_records(oracle_db, references))
    records.extend(tmdl_records(run))
    versions = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions() if d.metadata["Name"]}
    inputs = sorted([*FIXTURES.rglob("*"), * (LAB / "src").rglob("*.py"), LAB / "pyproject.toml", LAB / "uv.lock",
                     ROOT / "semantic_poc/agent/powerbi_import.py", ROOT / "semantic_poc/src/semantic_ir.py"])
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in inputs if p.is_file() and "__pycache__" not in p.parts}
    evidence = {}
    for p in sorted(run.rglob("*")):
        if p.is_file() and "dbt" not in p.relative_to(run).parts and p.suffix in {".sql", ".json", ".yml", ".bim"}:
            # Sanitized artifacts are portable; raw dbt logs remain private generated runtime files.
            content = sanitize(p.read_text(encoding="utf-8"))
            p.write_text(content, encoding="utf-8")
            evidence[p.relative_to(run).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    report = sanitize({"schema_version": 1, "scope": "synthetic fixture only; no DAX runtime or general converter correctness claim",
                       "python": platform.python_version(), "tool_versions": versions, "ossie_commit": OSSIE_COMMIT,
                       "input_sha256": hashes, "artifact_sha256": evidence,
                       "integration": {"ossie_conversion_executed": "roundtrip_manifest" in output,
                                       "microsoft_conversion_executed": "bim" in output.get("microsoft", {}),
                                       "tom": "NOT_EXECUTED", "dax_engine": "NOT_EXECUTED"},
                       "cases": records, "status_counts": dict(Counter(r["status"] for r in records)),
                       "path_counts": {path: dict(Counter(r["status"] for r in records if r["path"] == path)) for path in sorted({r["path"] for r in records})}})
    report["acceptance"] = acceptance(report)
    write_json(run / "report.json", report)
    return run, report
