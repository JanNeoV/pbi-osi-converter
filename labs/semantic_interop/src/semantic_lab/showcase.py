"""Coordinate the existing benchmark and unmodified official converters.

This is an evidence driver, not a translator or a target-engine emulator.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import shutil
import sys
import traceback
import warnings

import duckdb
import yaml

from .conversion_demo import cases, normalize, result_record, run_demo
from .conversion_platforms import inventory
from .diagnostics import capture
from .fixtures import OSSIE_COMMIT, ROOT
from .integrations import compile_queries, issue_dict, write_json

HEADLINES = ("conversion_rate", "paid_revenue")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def versions():
    result = {}
    for name in ("apache-ossie", "apache-ossie-dbt", "apache-ossie-microsoft"):
        dist = importlib.metadata.distribution(name)
        source = json.loads(dist.read_text("direct_url.json"))
        if source.get("vcs_info", {}).get("commit_id") != OSSIE_COMMIT:
            raise RuntimeError(f"Refusing unpinned {name}: {source}")
        result[name] = {"version": dist.version, "source": source}
    return result


def metricflow_leg(source, output, direction):
    """One converter call, with its own untouched output and diagnostics."""
    from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
    from ossie import OssieDocument
    from ossie_dbt import MSIToOssieConverter, OssieToMSIConverter

    output.mkdir(parents=True, exist_ok=False)
    local_source = output / ("input.manifest.json" if direction == "to-ossie" else "input.ossie.yml")
    shutil.copyfile(source, local_source)
    artifact = output / ("output.ossie.yml" if direction == "to-ossie" else "output.manifest.json")
    record = {"direction": direction, "conversion": "FAIL", "semantic_preservation": "UNKNOWN",
              "numerical_equivalence": "NOT_EXECUTED", "input": local_source.name,
              "input_sha256": digest(local_source), "output": None, "issues": []}
    record["official_api"] = ("ossie_dbt.MSIToOssieConverter.convert" if direction == "to-ossie"
                              else "ossie_dbt.OssieToMSIConverter.convert")
    stdout, stderr = io.StringIO(), io.StringIO()
    with warnings.catch_warnings(record=True) as caught, redirect_stdout(stdout), redirect_stderr(stderr):
        warnings.simplefilter("always")
        try:
            if direction == "to-ossie":
                manifest = PydanticSemanticManifest.parse_raw(local_source.read_text(encoding="utf-8"))
                result = MSIToOssieConverter().convert(manifest, ossie_model_name="conversion_demo")
                artifact.write_text(result.output.to_ossie_yaml(), encoding="utf-8")
                OssieDocument.model_validate(yaml.safe_load(artifact.read_text(encoding="utf-8")))
                record["source_inventory"] = inventory(manifest)
                record["semantic_preservation"] = "MISMATCH"
                record["findings"] = ["Ossie output omits the source MetricFlow time-spine configuration and default aggregation-time settings; reconstruction is not applied. Entity/key information is inspected separately on return."]
            else:
                document = OssieDocument.model_validate(yaml.safe_load(local_source.read_text(encoding="utf-8")))
                result = OssieToMSIConverter().convert(document)
                artifact.write_text(result.output.json(by_alias=True, exclude_none=True, indent=2), encoding="utf-8")
                record["target_inventory"] = inventory(result.output)
                record["target_metric_names"] = [m.name for m in result.output.metrics]
            record.update(conversion="PASS", output=artifact.name, output_sha256=digest(artifact),
                          issues=[issue_dict(i) for i in result.issues])
        except Exception as exc:
            record["error"] = str(exc)
            stderr.write(traceback.format_exc())
    record["warnings"] = [{"category": w.category.__name__, "message": str(w.message)} for w in caught]
    (output / "stdout.log").write_text(stdout.getvalue(), encoding="utf-8")
    (output / "stderr.log").write_text(stderr.getvalue(), encoding="utf-8")
    write_json(output / "leg.json", record)
    return record


def execute_metricflow(leg_directory, native_run, native_report):
    """Attempt actual MetricFlow initialization/queries; never restore lost config."""
    from dbt_metricflow.cli.cli_configuration import CLIConfiguration
    from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
    requested = [c for c in cases() if c.metric in HEADLINES]
    manifest = PydanticSemanticManifest.parse_raw((leg_directory / "output.manifest.json").read_text(encoding="utf-8"))
    config = CLIConfiguration()
    project = native_run / "dbt"
    try:
        config.setup(dbt_profiles_path=project, dbt_project_path=project, configure_file_logging=False)
        with duckdb.connect(str(project / "benchmark.duckdb")) as conn:
            actual = compile_queries(manifest, config.sql_client, leg_directory / "queries", conn, requested)
            records = []
            for case in requested:
                sql_path = native_run / "oracle" / f"{case.id}.sql"
                sql = sql_path.read_text(encoding="utf-8")
                cur = conn.execute(sql)
                ref = ([c[0] for c in cur.description], cur.fetchall())
                record = result_record(case.id, case.metric, ref, normalize(case, actual[case.id]),
                    path="Unchanged official target -> MetricFlow -> DuckDB",
                    provenance="No repairs; canonical query identifiers; exact returned manifest retained")
                record["source_platform"] = {"metricflow": "MetricFlow", "powerbi": "Power BI",
                    "databricks": "Databricks", "switch": "Power BI SWITCH"}.get(leg_directory.name, leg_directory.name)
                record["target_platform"] = "MetricFlow / DuckDB"
                record["conversion_path"] = record["source_platform"] + " -> Apache Ossie -> MetricFlow -> DuckDB"
                records.append(record)
    finally:
        if getattr(config, "dbt_artifacts", None):
            config.dbt_artifacts.adapter.cleanup_connections()
    executed = [r for r in records if r["execution_status"] == "EXECUTED"]
    matched = [r for r in executed if r["comparison_result"]["equal"]]
    status = "BLOCKED" if len(executed) != len(records) else "PASS" if len(matched) == len(records) else "MISMATCH"
    result = {"numerical_equivalence": status, "requested": len(records), "executed": len(executed),
              "oracle_matches": len(matched), "cases": records,
              "canonical_query_metric_names": list(HEADLINES),
              "actual_target_metric_names": [m.name for m in manifest.metrics],
              "scope": "MetricFlow renderer with DuckDB backend; no target reconstruction"}
    write_json(leg_directory / "execution.json", result)
    return result


def assess(output):
    """Bounded observations from actual artifacts, separate from raw output."""
    def read(relative):
        path = output / relative
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "conversion": "NOT_EXECUTED", "semantic_preservation": "UNKNOWN",
            "reason": "Upstream output unavailable; see preceding conversion diagnostics"}

    paths = []

    def add(source, target, first, second, semantic, reason, numerical="BLOCKED", scope="Static contract-loss evidence; no numerical equivalence established"):
        legs = [first, second]
        statuses = []
        for relative in legs:
            value = read(relative)
            statuses.append(value.get("conversion", "NOT_EXECUTED"))
        conversion = "FAIL" if "FAIL" in statuses else "NOT_EXECUTED" if "NOT_EXECUTED" in statuses else "PASS"
        paths.append({"source": source, "target": target, "via": "Apache Ossie", "conversion": conversion,
                      "semantic_preservation": semantic, "numerical_equivalence": numerical,
                      "semantic_scope": scope, "reason": reason, "legs": legs})

    mf_first = "metricflow/to-ossie/leg.json"
    pb_first = "powerbi/core/to-ossie/report.json"
    db_first = "databricks/databricks-roundtrip/to-ossie.leg.json"
    mf_back = read("to-metricflow/metricflow/leg.json")
    source_inventory = read(mf_first).get("source_inventory")
    target_inventory = mf_back.get("target_inventory")
    mf_loss = source_inventory is not None and target_inventory is not None and source_inventory != target_inventory
    add("MetricFlow", "MetricFlow", mf_first, "to-metricflow/metricflow/leg.json",
        "MISMATCH" if mf_loss else "UNKNOWN", "Time spine, entities and default aggregation-time settings absent on return; target initialization blocked." if mf_loss else "Insufficient return evidence",
        numerical=read("to-metricflow/metricflow/execution.json").get("numerical_equivalence", "BLOCKED"))
    pb = read("powerbi/core/report.json")
    add("Power BI", "Power BI", pb_first, "powerbi/core/from-ossie/report.json", pb["semantic_preservation"],
        "Exact normalized native definitions preserved, including DAX and Power BI extensions. DAX execution UNKNOWN.",
        scope=pb.get("semantic_scope", "UNKNOWN"))
    db = read("databricks/databricks-roundtrip/report.json")
    add("Databricks", "Databricks", db_first, "databricks/databricks-roundtrip/from-ossie.leg.json",
        db["semantic_preservation"], "Native SQL source, top-level filter, dimensions and measures replayed; trailing source whitespace ignored. Databricks execution UNKNOWN.",
        scope=db.get("semantic_scope", "UNKNOWN"))
    for source, first, short in (("MetricFlow", mf_first, "metricflow"), ("Databricks", db_first, "databricks")):
        leg = f"to-powerbi/{short}/report.json"
        value = read(leg)
        add(source, "Power BI", first, leg, value["semantic_preservation"],
            "Headline measures replaced by annotated BLANK(); warnings preserve the untranslated expressions." if value["semantic_preservation"] == "MISMATCH" else value.get("reason", "No preservation claim"))
    for source, first, short in (("MetricFlow", mf_first, "metricflow"), ("Power BI", pb_first, "powerbi")):
        lane = read(f"databricks/{short}-to-databricks/report.json")
        messages = [leg.get("command", {}).get("warnings", []) for leg in lane.get("legs", [])]
        add(source, "Databricks", first, f"databricks/{short}-to-databricks/from-ossie.leg.json",
            lane["semantic_preservation"], lane.get("static_findings") or messages or lane.get("reason"))
    for source, first, short in (("Power BI", pb_first, "powerbi"), ("Databricks", db_first, "databricks")):
        artifact_path = output / f"to-metricflow/{short}/output.manifest.json"
        if not artifact_path.is_file():
            add(source, "MetricFlow", first, f"to-metricflow/{short}/leg.json", "UNKNOWN", "No converted target artifact available")
            continue
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        if short == "powerbi":
            expressions = {m["name"]: m.get("type_params", {}).get("expr") for m in artifact.get("metrics", [])}
            reason = {"finding": "Vendor DAX copied into MetricFlow expressions without translation; native M partitions and Power BI semantics not reconstructed.", "returned_expressions": expressions}
        else:
            reason = {"finding": "Native Metric View top-level eligibility filter has no returned MetricFlow filter; SQL query source also split into relation-name components.",
                      "source_filter": yaml.safe_load((output / "databricks/databricks-roundtrip/source.metric-view.yml").read_text())["filter"],
                      "returned_model_relation": artifact["semantic_models"][0]["node_relation"],
                      "returned_metric_filters": {m["name"]: m.get("filter") for m in artifact.get("metrics", [])}}
        add(source, "MetricFlow", first, f"to-metricflow/{short}/leg.json", "MISMATCH", reason,
            numerical=read(f"to-metricflow/{short}/execution.json").get("numerical_equivalence", "BLOCKED"))
    result = {"paths": paths, "claim_boundaries": {
        "conversion": "PASS means both official converter directions emitted parseable artifacts; warnings and dropped constructs may still exist.",
        "semantic": "PASS is bounded to inspected declarative-definition preservation, not execution. MISMATCH records concrete contract/configuration loss. UNKNOWN means insufficient evidence.",
        "numerical": "PASS requires actual target-generated/executed queries matching the independent oracle; BLOCKED means prerequisites or target initialization prevent comparison.",
        "outbound_numerical": "Ossie is an interchange artifact, so individual source-to-Ossie legs have no numerical execution; NOT_EXECUTED is deliberate."}}
    write_json(output / "paths.json", result)
    return result


def assess_switch(output):
    native = json.loads((output / "powerbi/switch/report.json").read_text(encoding="utf-8"))
    mf_leg = json.loads((output / "to-metricflow/switch/leg.json").read_text(encoding="utf-8"))
    mf_path = output / "to-metricflow/switch/output.manifest.json"
    manifest = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.is_file() else {}
    selected = [m for m in manifest.get("metrics", []) if m["name"] == "Selected KPI"]
    literal = selected[0].get("type_params", {}).get("expr") if selected else None
    db = json.loads((output / "databricks/powerbi-switch-to-databricks/report.json").read_text(encoding="utf-8"))
    result = {"Power BI": {key: native[key] for key in ("conversion", "semantic_preservation", "numerical_equivalence", "semantic_scope", "switch")},
              "MetricFlow": {"conversion": mf_leg["conversion"], "semantic_preservation": "MISMATCH" if literal else "UNKNOWN",
                  "numerical_equivalence": "BLOCKED", "returned_selected_kpi_expression": literal,
                  "selector_schema_present": any(m["name"] == "KPI Selector" for m in manifest.get("semantic_models", [])),
                  "finding": "DAX selector expression carried as a MetricFlow metric expression; M data partition and executable selector interaction not reconstructed.",
                  "execution": "NOT_EXECUTED", "evidence": "to-metricflow/switch/execution.json"},
              "Databricks": {key: db[key] for key in ("conversion", "semantic_preservation", "numerical_equivalence", "legs")},
              "portable_interaction": "No executable cross-platform selector interaction demonstrated. The observed Databricks failure is graph reachability of the disconnected table; it occurs before expression translation.",
              "expected_selector_cases": "powerbi/oracle/report.json", "actual_target_selector_values": None}
    write_json(output / "switch-assessment.json", result)
    return result


def run(output):
    """Nine routes using one native source per platform and a separate SWITCH lane."""
    from .showcase_powerbi import native_demo, convert_leg
    from .showcase_databricks import run_databricks

    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "python-converters.json", versions())
    print("Executing unchanged canonical MetricFlow benchmark", flush=True)
    native_run, native_report = run_demo()
    capture(native_run, output / "native")
    shutil.copyfile(native_run / "dbt-build.log", output / "native/dbt-build.log")
    if not native_report["stage_a"]["accepted"]:
        raise RuntimeError("Existing canonical benchmark failed; see native/report.json")
    mf = output / "metricflow/to-ossie"
    metricflow_leg(native_run / "source.manifest.json", mf, "to-ossie")
    mf_ossie = mf / "output.ossie.yml"
    print("Executing Power BI core and SWITCH round trips", flush=True)
    pbi = native_demo(output / "powerbi")
    pbi_ossie = output / "powerbi/core/to-ossie/output.ossie.yml"
    switch_ossie = output / "powerbi/switch/to-ossie/output.ossie.yml"
    print("Executing Databricks native and cross-platform converter legs", flush=True)
    db = run_databricks(output / "databricks", {name: path for name, path in
        {"metricflow": mf_ossie, "powerbi": pbi_ossie, "powerbi-switch": switch_ossie}.items() if path.is_file()})
    write_json(output / "databricks/report.json", db)
    db_ossie = output / "databricks/databricks-roundtrip/imported.ossie.yml"
    mf_targets = {}
    for name, source in (("metricflow", mf_ossie), ("powerbi", pbi_ossie), ("databricks", db_ossie), ("switch", switch_ossie)):
        directory = output / f"to-metricflow/{name}"
        if not source.is_file():
            continue
        record = metricflow_leg(source, directory, "from-ossie")
        if record["conversion"] == "PASS":
            try:
                execute_metricflow(directory, native_run, native_report)
            except Exception as exc:
                write_json(directory / "execution.json", {"numerical_equivalence": "BLOCKED", "error": str(exc),
                    "traceback": traceback.format_exc(), "executed": 0, "oracle_matches": 0})
        mf_targets[name] = record
    for name, source in (("metricflow", mf_ossie), ("databricks", db_ossie)):
        if source.is_file():
            convert_leg(source, output / f"to-powerbi/{name}", "from-ossie")
    report = {"schema_version": 1, "fixture": "fixtures/conversion_demo (unchanged)",
              "native_gate": native_report["stage_a"], "powerbi_report": "powerbi/report.json",
              "databricks_report": "databricks/report.json", "python_converters": "python-converters.json",
              "engines": {"MetricFlow/DuckDB": "EXECUTED", "Power BI/DAX": "NOT_EXECUTED", "Databricks": "NOT_EXECUTED"},
              "status_boundary": "Conversion PASS means converter emitted a parseable artifact; numerical PASS requires executed oracle comparisons. Semantic findings are bounded static observations.",
              "command": [str(sys.executable).replace(str(ROOT), "<repository>"), "labs/semantic_interop/scripts/run_showcase.py", "--output", str(output).replace(str(ROOT), "<repository>")]}
    report["comparison"] = assess(output)
    report["switch"] = assess_switch(output)
    report["implementation_sha256"] = {p.relative_to(ROOT).as_posix(): digest(p) for p in
        [Path(__file__), Path(__file__).with_name("showcase_powerbi.py"), Path(__file__).with_name("showcase_databricks.py"), ROOT / "labs/semantic_interop/scripts/run_showcase.py"]}
    report["artifact_sha256"] = {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob("*")) if p.is_file()}
    write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory; refuses overwrite")
    parser.add_argument("--scope", choices=("all", "three"), default="all",
                        help="Keep all nine routes, or run the three public experiments")
    parser.add_argument("--check", action="store_true", help="Fail if demo acceptance is incomplete")
    args = parser.parse_args()
    if args.scope == "three":
        from .showcase_three import run_three
        report = run_three(args.output.resolve())
    else:
        report = run(args.output.resolve())
    print(json.dumps({"output": args.output.as_posix(), "native_gate": report["native_gate"]}, indent=2))
    return 1 if args.check and not report.get("accepted", report["native_gate"]["accepted"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
