"""Evidence-only Power BI legs for the Task 04 interoperability showcase.

Native definitions are explicitly authored from the accepted, unchanged fixture.
All conversions call the pinned, unmodified Apache Ossie Microsoft converter.
This module neither translates DAX nor deploys or emulates a Power BI engine.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import traceback
import warnings

import duckdb
import yaml

from semantic_lab.compare import table
from semantic_lab.conversion_demo import FIXTURE, load_data, oracle
from semantic_lab.fixtures import LAB, OSSIE_COMMIT, ROOT
from semantic_lab.integrations import write_json


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_builder():
    """Reuse the historical authored-input builder without executing its runner."""
    path = LAB / "scripts/run_powerbi_showcase.py"
    spec = importlib.util.spec_from_file_location("_task04_powerbi_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def converter_provenance() -> dict:
    import ossie_microsoft

    distributions = {}
    for name in ("apache-ossie", "apache-ossie-microsoft"):
        dist = importlib.metadata.distribution(name)
        direct_url = json.loads(dist.read_text("direct_url.json") or "{}")
        distributions[name] = {"version": dist.version, "direct_url": direct_url}
        actual_commit = direct_url.get("vcs_info", {}).get("commit_id")
        if actual_commit != OSSIE_COMMIT:
            raise RuntimeError(f"{name} must be installed from {OSSIE_COMMIT}; found {actual_commit!r}")
    package = Path(ossie_microsoft.__file__).parent
    return {
        "official_source": f"https://github.com/apache/ossie/tree/{OSSIE_COMMIT}/converters/microsoft",
        "commit": OSSIE_COMMIT,
        "distributions": distributions,
        "installed_converter_sha256": {
            path.relative_to(package).as_posix(): sha(path) for path in sorted(package.rglob("*.py"))
        },
        "python": sys.version,
    }


def _inventory(model: dict) -> dict:
    tables = model.get("model", {}).get("tables", [])
    measures = {
        measure["name"]: measure
        for table in tables for measure in table.get("measures", [])
    }
    blank_substitutions = []
    for name, measure in measures.items():
        annotations = {entry["name"]: entry.get("value") for entry in measure.get("annotations", [])}
        expression = measure.get("expression")
        expression = "\n".join(expression) if isinstance(expression, list) else expression
        if expression == "BLANK()" and "OssieExpression" in annotations:
            blank_substitutions.append({"measure": name, "original_expression": annotations["OssieExpression"],
                                        "original_dialect": annotations.get("OssieExpressionDialect")})
    return {"tables": [table["name"] for table in tables], "measures": list(measures),
            "untranslated_expressions_replaced_by_blank": blank_substitutions,
            "partitions": {table["name"]: table.get("partitions", []) for table in tables}}


def convert_leg(source: Path, output: Path, direction: str) -> dict:
    """Run one official converter direction and preserve even failed attempts.

    ``to-ossie`` accepts model.bim JSON; ``from-ossie`` accepts official Ossie YAML.
    A PASS here claims that the converter returned an artifact. It does not claim
    that a target engine can run it. Output directories must be new.
    """
    from ossie import OssieDocument
    from ossie_microsoft import convert_ossie_to_semantic_model, convert_semantic_model_to_ossie

    if direction not in ("to-ossie", "from-ossie"):
        raise ValueError("direction must be to-ossie or from-ossie")
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    record = {
        "direction": direction, "source": str(source), "source_sha256": sha(source),
        "conversion": "NOT_EXECUTED", "semantic_preservation": "UNKNOWN",
        "numerical_equivalence": "NOT_EXECUTED", "warnings": [],
        "semantic_scope": "Only directly inspected converter behavior; no target-engine execution.",
        "official_call": "ossie_microsoft.convert_semantic_model_to_ossie(source_json)" if direction == "to-ossie"
                         else "ossie_microsoft.convert_ossie_to_semantic_model(source_yaml)",
    }
    source_copy = output / ("input.model.bim" if direction == "to-ossie" else "input.ossie.yml")
    shutil.copyfile(source, source_copy)
    record["input_artifact"] = str(source_copy)
    stdout, stderr, converter_log = io.StringIO(), io.StringIO(), io.StringIO()
    handler = logging.StreamHandler(converter_log)
    logger = logging.getLogger("ossie_microsoft")
    logger.addHandler(handler)
    captured = []
    try:
        record["converter"] = converter_provenance()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                if direction == "to-ossie":
                    original = json.loads(source.read_text(encoding="utf-8-sig"))
                    result = convert_semantic_model_to_ossie(original)
                    artifact = output / "output.ossie.yml"
                    artifact.write_text(result, encoding="utf-8")
                    document = OssieDocument.model_validate(yaml.safe_load(result))
                    content = document.model_dump(mode="json", exclude_none=True)
                    record["ossie_structure"] = "PASS"
                    record["metric_dialects"] = {
                        metric["name"]: [item["dialect"] for item in metric["expression"]["dialects"]]
                        for metric in content.get("metrics", [])
                    }
                    record["power_bi_vendor_extension_present"] = "vendor_name: POWER_BI" in result
                else:
                    result = convert_ossie_to_semantic_model(source.read_text(encoding="utf-8-sig"))
                    artifact = output / "output.model.bim"
                    write_json(artifact, result)
                    record["target_inventory"] = _inventory(result)
                    if record["target_inventory"]["untranslated_expressions_replaced_by_blank"]:
                        record["semantic_preservation"] = "MISMATCH"
                        record["semantic_reason"] = "Official converter substituted BLANK() for untranslated source expressions."
                record["conversion"] = "PASS"
                record["artifact"] = str(artifact)
                record["artifact_sha256"] = sha(artifact)
    except Exception as exc:
        record["conversion"] = "FAIL"
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        (output / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        logger.removeHandler(handler)
        record["warnings"] = [{"category": item.category.__name__, "message": str(item.message)} for item in captured]
        for name, stream in (("stdout.txt", stdout), ("stderr.txt", stderr), ("converter.log", converter_log)):
            (output / name).write_text(stream.getvalue(), encoding="utf-8")
        write_json(output / "warnings.json", record["warnings"])
        write_json(output / "report.json", record)
    return record


def engine_availability() -> dict:
    """Read-only discovery; do not deploy synthetic models through engine helpers."""
    names = ("PBIDesktop.exe", "msmdsrv.exe")
    discovery = {"platform": platform.system(), "local_processes": [],
                 "provided_xmla_endpoint": False,
                 "reason": "No Power BI/XMLA endpoint or model execution session was supplied for this task."}
    if platform.system() == "Windows":
        process = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False)
        discovery["tasklist_exit_code"] = process.returncode
        discovery["local_processes"] = [line for line in process.stdout.splitlines() if any(name.lower() in line.lower() for name in names)]
    discovery["endpoint_environment_variables_present"] = {
        name: bool(os.environ.get(name)) for name in ("POWERBI_XMLA_ENDPOINT", "PBI_XMLA_ENDPOINT", "POWERBI_WORKSPACE_ID", "FABRIC_WORKSPACE_ID")
    }
    discovery["engine_execution"] = "NOT_EXECUTED"
    discovery["numerical_equivalence"] = "BLOCKED"
    discovery["actual_DAX_values"] = None
    return discovery


def _oracle_evidence(output: Path, *, include_switch: bool = True) -> dict:
    """Execute independent SQL; expected selector values are never DAX results."""
    directory = output / "oracle"
    directory.mkdir()
    result = {"engine": "DuckDB", "version": importlib.metadata.version("duckdb"),
              "execution": "EXECUTED", "headline_queries": {}, "selector_reference_queries": {},
              "planned_selector_tests": []}
    with duckdb.connect() as conn:
        load_data(conn)
        for metric in ("conversion_rate", "paid_revenue"):
            for grain in ("overall", "channel", "month", "month_channel"):
                columns, rows, sql = oracle(conn, metric, grain)
                key = f"{metric}__{grain}"
                (directory / f"{key}.sql").write_text(sql, encoding="utf-8")
                record = {"execution": "EXECUTED", "sql": f"oracle/{key}.sql", "typed_result": table(columns, rows)}
                write_json(directory / f"{key}.json", record)
                result["headline_queries"][key] = record
        for channel in ((None, "Direct", "Indirect") if include_switch else ()):
            predicate = f"s.sales_channel = '{channel}'" if channel else None
            references = {}
            for metric in ("conversion_rate", "paid_revenue"):
                columns, rows, sql = oracle(conn, metric, cohort_filter=predicate)
                key = f"selector__{metric}__{channel or 'overall'}"
                (directory / f"{key}.sql").write_text(sql, encoding="utf-8")
                references[metric] = (key, rows[0][0])
                record = {"execution": "EXECUTED", "sql": f"oracle/{key}.sql", "typed_result": table(columns, rows)}
                write_json(directory / f"{key}.json", record)
                result["selector_reference_queries"][key] = record
            for selection in ("none", "Conversion Rate", "Paid Revenue", "multiple"):
                metric = "paid_revenue" if selection == "Paid Revenue" else "conversion_rate"
                key, value = references[metric]
                result["planned_selector_tests"].append({
                    "selection": selection, "channel": channel or "overall", "oracle_reference": key,
                    "expected_from_duckdb": float(value), "unit": "EUR" if metric == "paid_revenue" else "ratio",
                    "DAX_execution": "NOT_EXECUTED", "actual_DAX_value": None,
                    "numerical_equivalence": "NOT_EXECUTED", "oracle_comparison": "NOT_EXECUTED",
                })
    write_json(directory / "report.json", result)
    return result


def native_demo(output: Path, *, include_switch: bool = True) -> dict:
    """Author core/SWITCH native inputs and run separately recorded native legs."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    builder = _fixture_builder()
    report = {"source_origin": "MANUALLY_AUTHORED_NATIVE_FIXTURE_DEFINITION",
              "fixture_unchanged": True, "converter": converter_provenance(), "cases": {}}
    fixture_hashes = {path.relative_to(FIXTURE).as_posix(): sha(path)
                      for path in sorted(FIXTURE.rglob("*")) if path.is_file()}
    report["fixture_sha256_before"] = fixture_hashes
    report["engine_availability"] = engine_availability()
    report["duckdb_oracle"] = _oracle_evidence(output, include_switch=include_switch)
    queries = FIXTURE / "targets/powerbi/validate-task04.dax"
    shutil.copyfile(queries, output / "planned-validation.dax")
    variants = (("core", False), ("switch", True)) if include_switch else (("core", False),)
    for name, dynamic in variants:
        case = output / name
        case.mkdir()
        source = builder.authored_input(dynamic)
        # Bind an authored input parameter to the exact existing CSV directory.
        # Fixture files and the converter outputs are never edited.
        for expression in source["model"]["expressions"]:
            if expression["name"] == "FixtureRoot":
                expression["expression"] = expression["expression"].replace(
                    "__SET_ABSOLUTE_CONVERSION_DEMO_DATA_DIRECTORY__", (FIXTURE / "data").as_posix())
        source_path = case / "source.model.bim"
        write_json(source_path, source)
        first = convert_leg(source_path, case / "to-ossie", "to-ossie")
        second = convert_leg(Path(first["artifact"]), case / "from-ossie", "from-ossie") if first["conversion"] == "PASS" else None
        result = {"conversion": "PASS" if second and second["conversion"] == "PASS" else "FAIL",
                  "semantic_preservation": "UNKNOWN", "numerical_equivalence": "BLOCKED",
                  "legs": {"to_ossie": first, "from_ossie": second},
                  "execution": {"DAX": "NOT_EXECUTED", "comparison_with_oracle": "NOT_EXECUTED", "actual_values": None},
                  "parameter_binding": {"FixtureRoot": (FIXTURE / "data").as_posix()},
                  "source_TOM_validation": builder.tom_probe(source)}
        if second and second["conversion"] == "PASS":
            returned = json.loads(Path(second["artifact"]).read_text(encoding="utf-8"))
            checks, dependencies = builder.bounded_checks(source, returned)
            result["bounded_structural_checks"] = checks
            result["measure_dependencies"] = dependencies
            result["semantic_preservation"] = "PASS" if all(checks.values()) else "MISMATCH"
            result["semantic_scope"] = "Exact normalized native DAX, relationships, columns, partitions, M and formatting metadata; executable semantics unverified."
            result["target_TOM_validation"] = builder.tom_probe(returned)
            if dynamic:
                source_kpi = next(m for t in source["model"]["tables"] for m in t.get("measures", []) if m["name"] == "Selected KPI")
                result["switch"] = {
                    "DAX_expression": source_kpi["expression"],
                    "expression_preservation": "PASS" if checks["dax_expressions_equal"] else "MISMATCH",
                    "dependencies_preservation": "PASS" if checks["complete_normalized_document_equal"] else "UNKNOWN",
                    "dependencies": dependencies["Selected KPI"],
                    "disconnected_selector_preservation": "PASS" if checks["selector_disconnected"] and checks["partitions_equal"] else "MISMATCH",
                    "exactly_two_selector_choices": checks["exactly_two_selector_choices"],
                    "dynamic_format_metadata_preservation": "PASS" if checks["complete_measure_properties_equal"] else "MISMATCH",
                    "Power_BI_executable": "UNKNOWN",
                    "Power_BI_execution": "NOT_EXECUTED",
                    "portable_business_semantics": "UNKNOWN",
                    "portability_reason": "The expression is DAX and the table/formatting survive in POWER_BI vendor extensions. Their preservation does not provide a target-platform KPI interaction.",
                    "visual_interaction_execution": "NOT_EXECUTED",
                }
        report["cases"][name] = result
        write_json(case / "report.json", result)
    report["fixture_sha256_after"] = {path.relative_to(FIXTURE).as_posix(): sha(path)
                                      for path in sorted(FIXTURE.rglob("*")) if path.is_file()}
    report["fixture_unchanged"] = report["fixture_sha256_before"] == report["fixture_sha256_after"]
    report["artifact_sha256"] = {path.relative_to(output).as_posix(): sha(path)
                                 for path in sorted(output.rglob("*")) if path.is_file()}
    write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    native = commands.add_parser("native")
    native.add_argument("--output", type=Path, required=True)
    leg = commands.add_parser("convert")
    leg.add_argument("--source", type=Path, required=True)
    leg.add_argument("--output", type=Path, required=True)
    leg.add_argument("--direction", choices=("to-ossie", "from-ossie"), required=True)
    args = parser.parse_args()
    report = native_demo(args.output) if args.command == "native" else convert_leg(args.source, args.output, args.direction)
    print(json.dumps({"output": str(args.output.resolve()), "conversion": report.get("conversion"),
                      "cases": {name: {key: case[key] for key in ("conversion", "semantic_preservation", "numerical_equivalence")}
                                for name, case in report.get("cases", {}).items()}}, indent=2))
    return 0 if report.get("conversion", "PASS") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
