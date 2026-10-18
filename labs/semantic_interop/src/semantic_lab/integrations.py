"""Real upstream compilation and conversion. No translated SQL is authored here."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import warnings

import duckdb
import yaml

from .fixtures import CASES, FIXTURES, load_data


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")


def prepare(run):
    project = run / "dbt"
    shutil.copytree(FIXTURES / "dbt", project)
    profile = yaml.safe_load((project / "profile.template.yml").read_text())
    # dbt's adapter also loads this profile from the caller's cwd. Bind the generated DB explicitly.
    profile["semantic_lab"]["outputs"]["local"]["path"] = str(project / "benchmark.duckdb")
    (project / "profiles.yml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    with duckdb.connect(str(project / "benchmark.duckdb")) as conn:
        load_data(conn)
    env = dict(os.environ, DBT_SEND_ANONYMOUS_USAGE_STATS="false", DO_NOT_TRACK="1",
               DBT_PROFILES_DIR=str(project), DBT_PROJECT_DIR=str(project),
               DBT_USE_COLORS="false")
    completed = subprocess.run([sys.executable, "-m", "dbt.cli.main", "build", "--no-partial-parse"],
                               cwd=project, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (run / "dbt-build.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    return project, completed.returncode


def issue_dict(issue):
    return {"type": issue.issue_type.value, "element": issue.element_name}


def compile_queries(manifest, sql_client, directory, conn, cases=CASES):
    from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
    from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

    directory.mkdir(parents=True, exist_ok=True)
    results = {}
    try:
        engine = MetricFlowEngine(semantic_manifest_lookup=SemanticManifestLookup(manifest),
                                  sql_client=sql_client, consistent_id_enumeration=True)
    except Exception as exc:
        return {c.id: {"error": str(exc), "phase": "compiler_initialization"} for c in cases}
    for case in cases:
        try:
            query = MetricFlowQueryRequest.create(metric_names=[case.metric], group_by_names=list(case.groups),
                                                  where_constraints=list(case.where))
            explanation = engine.explain(query)
            statement = explanation.sql_statement
            sql = statement.sql
            (directory / f"{case.id}.sql").write_text(sql + "\n", encoding="utf-8")
            # Query filters are literals in these fixtures. Fail explicitly if upstream introduces bindings.
            bindings = statement.bind_parameter_set
            if bindings.param_dict:
                raise ValueError("Unimplemented parameter binding: " + repr(bindings.param_dict))
            cursor = conn.execute(sql)
            columns = [c[0] for c in cursor.description]
            results[case.id] = {"columns": columns, "rows": cursor.fetchall(), "sql": sql}
        except Exception as exc:
            results[case.id] = {"error": str(exc), "phase": "compile_or_execute"}
    return results


def run_upstreams(project, run):
    from dbt_metricflow.cli.cli_configuration import CLIConfiguration
    from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
    from ossie import OssieDocument
    from ossie_dbt import MSIToOssieConverter, OssieToMSIConverter
    from ossie_microsoft import convert_ossie_to_semantic_model, convert_semantic_model_to_ossie

    config = CLIConfiguration()
    config.setup(dbt_profiles_path=project, dbt_project_path=project, configure_file_logging=False)
    manifest = parse_manifest_from_dbt_generated_manifest((project / "target/semantic_manifest.json").read_text())
    output = {"manifest": manifest, "native": {}, "roundtrip": {}, "issues": [], "conversion_error": None}
    with duckdb.connect(str(project / "benchmark.duckdb")) as conn:
        output["native"] = compile_queries(manifest, config.sql_client, run / "native", conn)
        try:
            outward = MSIToOssieConverter().convert(manifest, ossie_model_name="synthetic_shop")
            output["issues"] = [issue_dict(i) for i in outward.issues]
            encoded = outward.output.to_ossie_yaml()
            (run / "converted.ossie.yml").write_text(encoded, encoding="utf-8")
            doc = OssieDocument.model_validate(yaml.safe_load(encoded))
            output["ossie_valid"] = True
            output["ossie_document"] = doc.model_dump(mode="json", exclude_none=True)
            inward = OssieToMSIConverter().convert(doc)
            output["issues"] += [issue_dict(i) for i in inward.issues]
            (run / "roundtrip.manifest.json").write_text(inward.output.json(by_alias=True, exclude_none=True, indent=2), encoding="utf-8")
            output["roundtrip_manifest"] = inward.output
            output["roundtrip"] = compile_queries(inward.output, config.sql_client, run / "roundtrip", conn)
        except Exception as exc:
            output["conversion_error"] = str(exc)
        if "ossie_document" in output:
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    bim = convert_ossie_to_semantic_model(encoded)
                    write_json(run / "converted.model.bim", bim)
                    returned = convert_semantic_model_to_ossie(bim)
                    (run / "microsoft-returned.ossie.yml").write_text(returned, encoding="utf-8")
                    OssieDocument.model_validate(yaml.safe_load(returned))
                output["microsoft"] = {"bim": bim, "warnings": [str(w.message) for w in caught], "returned_valid": True}
            except Exception as exc:
                output["microsoft"] = {"error": str(exc)}
    config.dbt_artifacts.adapter.cleanup_connections()
    return output
