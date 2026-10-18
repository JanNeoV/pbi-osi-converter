"""Small manual lab helper: copy fixture or execute genuine MetricFlow SQL.

Conversion is deliberately done with the official ossie-dbt CLI, outside this file.
This is not a translator; no returned metric, entity or expression is repaired.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import duckdb
import yaml

from semantic_lab.conversion_demo import FIXTURE, load_data, oracle
from semantic_lab.fixtures import Case
from semantic_lab.integrations import compile_queries


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def initialize(work, elementary):
    work.mkdir(parents=True, exist_ok=False)
    project = work / "dbt"
    shutil.copytree(FIXTURE / "dbt", project)
    profile = yaml.safe_load((project / "profile.template.yml").read_text())
    profile["conversion_demo"]["outputs"]["local"]["path"] = str(project / "benchmark.duckdb")
    (project / "profiles.yml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    with duckdb.connect(str(project / "benchmark.duckdb")) as conn:
        load_data(conn)
    if elementary:
        path = project / "models/metrics.yml"
        model = yaml.safe_load(path.read_text())
        model["semantic_models"] = [m for m in model["semantic_models"] if m["name"] == "orders"]
        model["semantic_models"][0]["entities"] = [model["semantic_models"][0]["entities"][0]]
        model["semantic_models"][0]["measures"] = [model["semantic_models"][0]["measures"][-1]]
        model["metrics"] = [{"name": "paid_revenue", "label": "Elementary unfiltered sum (not business Paid Revenue)",
                             "type": "simple", "type_params": {"measure": "revenue"}}]
        path.write_text(yaml.safe_dump(model, sort_keys=False), encoding="utf-8")
    dump(work / "fixture-hashes.json", {
        p.relative_to(FIXTURE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in FIXTURE.rglob("*") if p.is_file()
    })
    print(f"Created {project}; canonical fixture unchanged.")


def execute(args):
    from dbt_metricflow.cli.cli_configuration import CLIConfiguration
    from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
    from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest

    work = args.work.resolve()
    project = work / "dbt"
    output = work / args.label
    output.mkdir(exist_ok=False)
    if args.manifest:
        manifest = PydanticSemanticManifest.parse_raw(args.manifest.read_text())
        provenance = "Unchanged official generated target manifest"
    else:
        manifest = parse_manifest_from_dbt_generated_manifest((project / "target/semantic_manifest.json").read_text())
        provenance = "dbt-generated source manifest"
    if args.spine_experiment:
        source = parse_manifest_from_dbt_generated_manifest((project / "target/semantic_manifest.json").read_text())
        before = manifest.project_configuration.json()
        manifest.project_configuration.time_spines = source.project_configuration.time_spines
        dump(output / "reconstruction.json", {"change": "Copy source time_spines only; no converter repair",
             "before": json.loads(before), "after": json.loads(manifest.project_configuration.json()),
             "unchanged_official_roundtrip": False})
        provenance = "SEPARATE RECONSTRUCTION EXPERIMENT: source time spine copied into target"
    (output / "manifest.json").write_text(manifest.json(by_alias=True, exclude_none=True, indent=2), encoding="utf-8")
    requested = [Case("paid_revenue__overall", "paid_revenue")]
    if not args.elementary:
        requested += [Case("conversion_rate__overall", "conversion_rate"),
                      Case("paid_revenue__channel", "paid_revenue", ("session__sales_channel",)),
                      Case("conversion_rate__channel", "conversion_rate", ("session__sales_channel",))]
    references = {}
    with duckdb.connect() as conn:
        load_data(conn)
        for case in requested:
            if args.elementary:
                # Independent raw-table scalar; intentionally no status/eligibility policy.
                sql = "SELECT SUM(amount_eur) AS paid_revenue FROM raw_orders WHERE order_date < DATE '2026-03-01'"
                cur = conn.execute(sql)
                columns, rows = [c[0] for c in cur.description], cur.fetchall()
            else:
                grain = "channel" if case.groups else "overall"
                columns, rows, sql = oracle(conn, case.metric, grain)
                if args.include_refunded and case.metric == "paid_revenue":
                    # A separately chosen business scenario; do not derive oracle from target.
                    sql = sql.replace("o.order_status = 'paid'", "o.order_status IN ('paid', 'refunded')")
                    cur = conn.execute(sql)
                    columns, rows = [c[0] for c in cur.description], cur.fetchall()
            (output / f"oracle-{case.id}.sql").write_text(sql + "\n", encoding="utf-8")
            references[case.id] = {"columns": columns, "rows": rows}
    config = CLIConfiguration()
    try:
        config.setup(dbt_profiles_path=project, dbt_project_path=project, configure_file_logging=False)
        with duckdb.connect(str(project / "benchmark.duckdb")) as conn:
            observed = compile_queries(manifest, config.sql_client, output / "sql", conn, requested)
    finally:
        if getattr(config, "dbt_artifacts", None):
            config.dbt_artifacts.adapter.cleanup_connections()
    comparisons = {}
    for key, ref in references.items():
        actual = observed[key]
        comparisons[key] = {"oracle": ref, "observed": actual,
                           "equal": sorted(actual["rows"]) == sorted(ref["rows"]) if "rows" in actual else None}
    record = {"provenance": provenance, "elementary_relaxed_business_policy": args.elementary,
              "include_refunded_revenue_only": args.include_refunded, "comparisons": comparisons}
    dump(output / "execution.json", record)
    print(json.dumps(record, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init", help="Copy only the canonical fixture; refuse existing destination")
    init.add_argument("--work", type=Path, required=True)
    init.add_argument("--elementary", action="store_true")
    run = sub.add_parser("execute", help="Compile with MetricFlow, execute generated SQL, compare raw SQL")
    run.add_argument("--work", type=Path, required=True)
    run.add_argument("--label", required=True, help="New evidence subdirectory; refuses overwrites")
    run.add_argument("--manifest", type=Path)
    run.add_argument("--elementary", action="store_true")
    run.add_argument("--include-refunded", action="store_true")
    run.add_argument("--spine-experiment", action="store_true")
    args = parser.parse_args()
    if args.action == "init":
        initialize(args.work.resolve(), args.elementary)
    else:
        if args.spine_experiment and not args.manifest:
            parser.error("--spine-experiment requires a generated --manifest")
        execute(args)
