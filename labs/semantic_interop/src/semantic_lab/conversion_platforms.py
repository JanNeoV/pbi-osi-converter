"""Optional official conversions; target definitions and target runtime are distinct."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import warnings

import duckdb
import yaml

from .conversion_demo import METRICS, QUESTIONS, cases, normalize, result_record, run_demo
from .fixtures import OSSIE_COMMIT, ROOT
from .integrations import compile_queries, issue_dict, write_json


def inventory(manifest):
    return {
        "time_spines": len(manifest.project_configuration.time_spines),
        "models": {m.name: {"entities": [(e.name, str(e.type), e.expr) for e in m.entities],
                            "default_time_dimension": m.defaults.agg_time_dimension if m.defaults else None}
                   for m in manifest.semantic_models},
    }


def unexecuted(reference, metric, target, path, evidence, provenance, reason):
    return {"case_id": f"{metric}__overall", "business_question": QUESTIONS[metric],
            "source_platform": "MetricFlow", "target_platform": target, "conversion_path": path,
            "execution_status": "NOT_EXECUTED", "semantic_status": "UNKNOWN",
            "oracle_result": reference, "target_result": None, "comparison_result": None,
            "evidence_reference": evidence, "provenance": provenance, "diagnostics": [reason]}


def run_platforms(databricks_jar=None, java=None):
    from dbt_metricflow.cli.cli_configuration import CLIConfiguration
    from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
    from ossie import OssieDocument
    from ossie_dbt import MSIToOssieConverter, OssieToMSIConverter
    from ossie_microsoft import convert_ossie_to_semantic_model

    run, report = run_demo()
    if not report["stage_a"]["accepted"]:
        raise RuntimeError(f"Stage A failed; Stage B was not executed. Inspect {run / 'report.json'}")
    folder = run / "platforms"
    folder.mkdir()
    source = PydanticSemanticManifest.parse_raw((run / "source.manifest.json").read_text())
    integration = {"source_model": {"authorship": "Manually authored dbt/MetricFlow source",
                                   "dbt_build_valid": True, "native_executed_matches": 31},
                   "ossie_commit": OSSIE_COMMIT,
                   "tool_versions": {n: importlib.metadata.version(n) for n in
                                     ("apache-ossie", "apache-ossie-dbt", "apache-ossie-microsoft")},
                   "ossie_conversion": {"execution_status": "NOT_EXECUTED"},
                   "roundtrip_conversion": {"execution_status": "NOT_EXECUTED"},
                   "microsoft_conversion": {"execution_status": "NOT_EXECUTED"},
                   "databricks_conversion": {"execution_status": "NOT_EXECUTED"},
                   "target_runtime": {"Power BI/DAX": "NOT_EXECUTED", "Databricks": "NOT_EXECUTED", "TOM": "NOT_EXECUTED"}}
    document, encoded = None, None
    try:
        outward = MSIToOssieConverter().convert(source, ossie_model_name="conversion_demo")
        encoded = outward.output.to_ossie_yaml()
        (folder / "converted.ossie.yml").write_text(encoded, encoding="utf-8")
        document = OssieDocument.model_validate(yaml.safe_load(encoded))
        integration["ossie_conversion"] = {"execution_status": "EXECUTED", "conversion_succeeded": True, "pydantic_structure_valid": True,
                                            "issues": [issue_dict(i) for i in outward.issues],
                                            "provenance": "Unmodified official MSIToOssieConverter output"}
        report["platform_execution"]["Apache Ossie conversion"] = "EXECUTED"
    except Exception as exc:
        integration["ossie_conversion"] = {"execution_status": "EXECUTED", "conversion_succeeded": False, "error": str(exc)}

    if document is not None:
        try:
            inward = OssieToMSIConverter().convert(document)
            (folder / "roundtrip.manifest.json").write_text(inward.output.json(by_alias=True, exclude_none=True, indent=2), encoding="utf-8")
            source_inventory, target_inventory = inventory(source), inventory(inward.output)
            integration["roundtrip_conversion"] = {"execution_status": "EXECUTED", "pydantic_structure_valid": True,
                "source_inventory": source_inventory, "target_inventory": target_inventory,
                "metadata_changed": source_inventory != target_inventory,
                "issues": [issue_dict(i) for i in inward.issues], "configuration_reconstructed": False,
                "provenance": "Unmodified official OssieToMSIConverter output"}
            config = CLIConfiguration()
            try:
                project = run / "dbt"
                config.setup(dbt_profiles_path=project, dbt_project_path=project, configure_file_logging=False)
                with duckdb.connect(str(project / "benchmark.duckdb")) as conn:
                    returned = compile_queries(inward.output, config.sql_client, folder / "roundtrip", conn, cases())
            finally:
                if getattr(config, "dbt_artifacts", None):
                    config.dbt_artifacts.adapter.cleanup_connections()
            native_records = {r["case_id"]: r for r in report["cases"]}
            for case in cases():
                observed = normalize(case, returned[case.id])
                # Retain the original typed oracle without decoding it through a new comparator.
                # Independent reference SQL is executed again in the same typed fixture database.
                with duckdb.connect(str(project / "benchmark.duckdb")) as conn:
                    cursor = conn.execute((run / "oracle" / f"{case.id}.sql").read_text())
                    reference = ([c[0] for c in cursor.description], cursor.fetchall())
                record = result_record(case.id, case.metric, reference, observed,
                    path="MetricFlow -> Apache Ossie -> MetricFlow -> DuckDB",
                    evidence=[f"oracle/{case.id}.sql", "platforms/converted.ossie.yml", "platforms/roundtrip.manifest.json"],
                    provenance="Official converter output; no repairs or configuration reconstruction")
                record["source_platform"] = "MetricFlow"
                record["structural_validity"] = True
                if source_inventory != target_inventory:
                    record["semantic_status"] = "LOSS_DETECTED"
                if "rows" in observed:
                    record["evidence_reference"].append(f"platforms/roundtrip/{case.id}.sql")
                assert record["oracle_result"] == native_records[case.id]["oracle_result"]
                report["cases"].append(record)
        except Exception as exc:
            integration["roundtrip_conversion"] = {"execution_status": "EXECUTED", "error": str(exc)}

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                bim = convert_ossie_to_semantic_model(encoded)
            write_json(folder / "converted.model.bim", bim)
            measures = {m["name"]: m for t in bim["model"]["tables"] for m in t.get("measures", [])}
            integration["microsoft_conversion"] = {"execution_status": "EXECUTED", "json_structure_readable": True,
                "warnings": [str(w.message) for w in caught],
                "emitted_measures": {name: m.get("expression") for name, m in sorted(measures.items())},
                "placeholder_measures": sorted(n for n, m in measures.items() if m.get("expression") == "BLANK()"),
                "relationships": bim["model"].get("relationships", []),
                "provenance": "Unmodified official Ossie Microsoft TMSL export; not the authored TMDL candidate",
                "TOM_validation": "NOT_EXECUTED", "DAX_execution": "NOT_EXECUTED"}
            report["platform_execution"]["Microsoft converter"] = "EXECUTED"
        except Exception as exc:
            measures = {}
            integration["microsoft_conversion"] = {"execution_status": "EXECUTED", "error": str(exc)}
    else:
        measures = {}

    roundtrip_path = "MetricFlow -> Apache Ossie -> MetricFlow -> DuckDB"
    if not any(r["conversion_path"] == roundtrip_path for r in report["cases"]):
        for native in list(report["cases"]):
            missing = unexecuted(native["oracle_result"], native["case_id"].split("__")[0], "MetricFlow / DuckDB",
                roundtrip_path, ["platforms/integration.json"], "Official conversion could not supply an executable target",
                str(integration["roundtrip_conversion"].get("error") or integration["ossie_conversion"].get("error") or "Conversion unavailable"))
            missing["case_id"] = native["case_id"]
            report["cases"].append(missing)

    java_executable = str(java) if java else shutil.which("java")
    if databricks_jar and java_executable and document is not None:
        jar = Path(databricks_jar).resolve()
        command = [java_executable, "-jar", str(jar), "export", str(folder / "converted.ossie.yml"),
                   "-o", str(folder / "converted.metric-view.yml"), "--source", "sessions"]
        try:
            jar_hash = hashlib.sha256(jar.read_bytes()).hexdigest()
            done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            (folder / "databricks-converter.log").write_text(done.stdout + done.stderr, encoding="utf-8")
            integration["databricks_conversion"] = {"execution_status": "EXECUTED", "exit_code": done.returncode,
                "jar_sha256": jar_hash, "target_runtime": "NOT_EXECUTED",
                "provenance": "Caller-supplied Java adapter JAR; verify its upstream build provenance separately. Output is not repaired."}
        except OSError as exc:
            integration["databricks_conversion"] = {"execution_status": "NOT_EXECUTED", "reason": str(exc)}
        except subprocess.TimeoutExpired:
            integration["databricks_conversion"] = {"execution_status": "EXECUTED", "error": "Java conversion timed out", "target_runtime": "NOT_EXECUTED"}
    else:
        integration["databricks_conversion"]["reason"] = (
            "Official Java adapter requires Java 21+ and a built JAR (Maven 3.6+ to build). "
            "No ready JAR/toolchain supplied; no installation or substitute converter attempted.")

    native_overall = {r["case_id"].split("__")[0]: r["oracle_result"] for r in report["cases"]
                      if r["conversion_path"] == "native" and r["case_id"].endswith("__overall")}
    for metric in METRICS:
        ms = unexecuted(native_overall[metric], metric, "Power BI", "MetricFlow -> Apache Ossie -> Microsoft TMSL",
            ["platforms/converted.model.bim"] if (folder / "converted.model.bim").exists() else [],
            "Official converter output; Power BI runtime not executed", "Exported TMSL has not been refreshed or queried in Power BI")
        if measures.get(metric, {}).get("expression") == "BLANK()":
            ms["semantic_status"] = "LOSS_DETECTED"
            ms["diagnostics"].append("Actual converter output replaces the metric expression with BLANK(); this is a structural observation, not a numerical failure")
        report["cases"].append(ms)
        report["cases"].append(unexecuted(native_overall[metric], metric, "Databricks",
            "MetricFlow -> Apache Ossie -> Databricks Metric View",
            ["platforms/converted.metric-view.yml"] if (folder / "converted.metric-view.yml").exists() else [],
            "Authored candidate is separate from official converter output; runtime not executed",
            "No Databricks runtime or compute was used"))
    report["platform_integrations"] = integration
    roundtrip_records = [r for r in report["cases"] if r["conversion_path"] == roundtrip_path]
    integration["roundtrip_conversion"]["requested_comparisons"] = len(roundtrip_records)
    integration["roundtrip_conversion"]["numerically_executed"] = sum(r["execution_status"] == "EXECUTED" for r in roundtrip_records)
    integration["roundtrip_conversion"]["oracle_matches"] = sum(bool(r["comparison_result"] and r["comparison_result"]["equal"]) for r in roundtrip_records)
    write_json(folder / "integration.json", integration)
    report["platform_implementation_sha256"] = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in
        (Path(__file__), ROOT / "labs/semantic_interop/scripts/run_conversion_platforms.py")}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            report["artifact_sha256"][path.relative_to(run).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(run / "report.json", report)
    return run, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, help="NEW capture directory; refuses overwrites")
    parser.add_argument("--databricks-jar", type=Path, help="Optional official built Java converter JAR")
    parser.add_argument("--java", type=Path, help="Optional Java 21+ executable")
    args = parser.parse_args()
    run, report = run_platforms(args.databricks_jar, args.java)
    if args.capture:
        from .diagnostics import capture
        capture(run, args.capture)
    print(json.dumps({"stage_a": report["stage_a"], "platform_integrations": report["platform_integrations"],
                      "report": (run / "report.json").relative_to(ROOT).as_posix()}, indent=2))
    return 0
