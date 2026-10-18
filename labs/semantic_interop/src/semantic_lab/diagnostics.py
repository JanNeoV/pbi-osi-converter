"""Offline, additive semantic-loss evidence. Converted artifacts are never patched in place."""
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

import duckdb

from .compare import table
from .fixtures import CASES, LAB, ROOT, load_data, oracle
from .integrations import compile_queries, write_json
from .runner import benchmark, finalize, normalized_rows, numeric_record, record, sanitize
from .semantics import inspect_manifest

CURRENT_COMMIT = "ef91cb61092c60f4b918016a81cfdbe483e113bc"
CLASSIFICATIONS = {"PRESERVED", "CONFIGURATION_GAP", "CONVERTER_DEFECT",
                   "EXPRESSIVENESS_LIMITATION", "EXPECTED_LOSS", "UNKNOWN"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plain(model):
    return json.loads(model.json()) if model is not None else None


def environment():
    import ossie_dbt
    modules = {}
    for name in ("ossie", "ossie_dbt", "ossie_microsoft"):
        module = __import__(name)
        folder = Path(module.__file__).parent
        modules[name] = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_text(encoding="utf-8").encode()).hexdigest()
                         for p in sorted(folder.rglob("*.py"))}
    distribution = importlib.metadata.distribution("apache-ossie-dbt")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    commit = direct.get("vcs_info", {}).get("commit_id")
    source = Path(ossie_dbt.__file__).resolve()
    git_root = (next((p for p in source.parents if (p / ".git").exists()), None)
                if direct.get("dir_info", {}).get("editable") else None)
    if git_root:
        commit = subprocess.check_output(["git", "-C", str(git_root), "rev-parse", "HEAD"], text=True).strip()
    tracked_diff = subprocess.check_output(["git", "-C", str(git_root), "diff", "HEAD"], text=True) if git_root else ""
    return {"ossie_commit": commit, "module_sha256": modules, "source_normalization": "Python source CRLF to LF only",
            "tracked_diff_sha256": hashlib.sha256(tracked_diff.encode()).hexdigest(), "tracked_source_modified": bool(tracked_diff),
            "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions() if d.metadata["Name"]},
            "python": sys.version.split()[0]}


def spine_diagnosis(source, target):
    import inspect
    from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
    from metricflow_semantics.time.time_spine_source import TimeSpineSource
    from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
    results = {}
    legacy_source = deepcopy(source)
    legacy_source.project_configuration.time_spines = []
    legacy_source = reconstructed(source, legacy_source, legacy=True)
    for name, manifest in (("source_modern", source), ("source_legacy", legacy_source),
                           ("returned_unmodified", target), ("empty_manifest", PydanticSemanticManifest(
                               semantic_models=[], metrics=[], project_configuration=deepcopy(target.project_configuration)))):
        try:
            SemanticManifestLookup(manifest)
            results[name] = {"lookup_constructed": True}
        except Exception as exc:
            results[name] = {"lookup_constructed": False, "exception": type(exc).__name__, "message": str(exc)}
    return {"pydantic_returned_manifest_valid": True,
            "lookup_signature": str(inspect.signature(SemanticManifestLookup)),
            "runtime_source_sha256": digest(inspect.getfile(TimeSpineSource)), "probes": results,
            "source_project_configuration": plain(source.project_configuration),
            "target_project_configuration": plain(target.project_configuration),
            "classification": "CONFIGURATION_GAP",
            "causal_chain": ["MSI contains one modern time spine", "Ossie conversion does not carry project configuration",
                             "Return converter constructs empty PydanticProjectConfiguration",
                             "MSI schema permits empty arrays", "SemanticManifestLookup requires modern or legacy spine unconditionally"]}


def inventory(source, target, document):
    """Attribute-level observations, with bounded causal classifications, not equivalence inference."""
    rows = []
    source_inspection, target_inspection = inspect_manifest(source), inspect_manifest(target)
    models = {m.name: m for m in target.semantic_models}
    metrics = {m.name: m for m in target.metrics}

    def add(identifier, construct, before, after, category, reason, source_pointer, target_pointer,
            stage="MSI -> Ossie -> MSI", overlap=None, affected=None):
        assert category in CLASSIFICATIONS
        rows.append({"id": identifier, "construct": construct, "expected": before, "observed": after,
                     "representation_equal": before == after, "classification": category, "rationale": reason,
                     "stage": stage, "source": {"artifact": "source.manifest.normalized.json", "pointer": source_pointer},
                     "target": {"artifact": "roundtrip.manifest.json", "pointer": target_pointer},
                     "upstream": overlap or [], "affected_queries": affected if affected is not None else [c.id for c in CASES],
                     "evidence_quality": "executed_conversion_and_source_inspection"})

    add("project.time_spines", "execution prerequisite", plain(source.project_configuration),
        plain(target.project_configuration), "CONFIGURATION_GAP",
        "Return converter constructs an empty project configuration. Both modern and legacy spines are absent; "
        "the pinned runtime reads only these manifest fields. Core Ossie has no time-spine property. "
        "This is lost runtime configuration, not proof of a numerical error.", "/project_configuration", "/project_configuration")
    for i, model in enumerate(source.semantic_models):
        other = models.get(model.name)
        for attr, category, reason in (
            ("entities", "CONVERTER_DEFECT", "Outgoing keys and relationship endpoints use physical expressions while fields use entity names; reverse classification matches field names."),
            ("defaults", "EXPRESSIVENESS_LIMITATION", "Core time flags do not encode the default aggregation time dimension; the return converter does not reconstruct defaults."),
            ("measures", "EXPECTED_LOSS", "This MSI version supports aggregation parameters directly on metrics; absence of legacy measure objects alone does not prove changed results."),
        ):
            before = [plain(x) for x in getattr(model, attr)] if attr != "defaults" else plain(model.defaults)
            after = ([plain(x) for x in getattr(other, attr)] if attr != "defaults" else plain(other.defaults)) if other else None
            add(f"model.{model.name}.{attr}", attr, before, after, "PRESERVED" if before == after else category,
                reason, f"/semantic_models/{i}/{attr}", f"/semantic_models/{i}/{attr}",
                overlap=["https://github.com/apache/ossie/pull/315"] if attr == "entities" else [])
        before_dims = {d.name: {"type": d.type.value, "expr": d.expr or d.name,
                              "time_granularity": d.type_params.time_granularity.value if d.type_params else None} for d in model.dimensions}
        after_dims = {d.name: {"type": d.type.value, "expr": d.expr or d.name,
                             "time_granularity": d.type_params.time_granularity.value if d.type_params else None} for d in other.dimensions}
        own_after = {n: after_dims.get(n) for n in before_dims}
        add(f"model.{model.name}.dimensions", "dimension expression, type and grain", before_dims, own_after,
            "PRESERVED" if before_dims == own_after else "EXPRESSIVENESS_LIMITATION",
            "Only declared source dimensions are compared here. Return conversion hardcodes DAY; non-day probes are separate.",
            f"/semantic_models/{i}/dimensions", f"/semantic_models/{i}/dimensions")
    for i, metric in enumerate(source.metrics):
        other = metrics.get(metric.name)
        affected = [c.id for c in CASES if c.metric == metric.name]
        before = source_inspection["metrics"][metric.name]["observed"]
        after = target_inspection["metrics"][metric.name]["observed"]
        add(f"metric.{metric.name}.definition", "type, aggregation, expression, filters and recursively resolved ratio inputs",
            before, after, "PRESERVED" if before == after else "CONVERTER_DEFECT",
            "Generated ratio input names are normalized only by recursively comparing their definitions. "
            "The filtered aggregate instead falls through to a guessed SUM on the first dataset; filter-name resolution is also lost.",
            f"/metrics/{i}", f"/metrics/{next((j for j,m in enumerate(target.metrics) if m.name == metric.name), -1)}",
            overlap=[] if before == after else ["https://github.com/apache/ossie/issues/430", "https://github.com/apache/ossie/pull/437"], affected=affected)
        for attr in ("name", "type", "label", "description", "datatype", "config"):
            a, b = plain(metric).get(attr), plain(other).get(attr) if other else None
            category = "PRESERVED" if a == b and a is not None else "EXPECTED_LOSS" if a != b else "UNKNOWN"
            add(f"metric.{metric.name}.{attr}", attr, a, b, category,
                "Attribute comparison only; absent/unspecified metadata is not positive preservation evidence.",
                f"/metrics/{i}/{attr}", f"/metrics[name={metric.name}]/{attr}", affected=affected)
        for attr in ("fill_nulls_with", "is_private", "metrics", "window", "grain_to_date", "join_to_timespine"):
            a, b = plain(metric.type_params).get(attr), plain(other.type_params).get(attr) if other else None
            add(f"metric.{metric.name}.{attr}", attr, a, b, "UNKNOWN" if a in (None, [], False) else "PRESERVED" if a == b else "EXPRESSIVENESS_LIMITATION",
                "Unexercised semantics remain UNKNOWN; numerical null/zero edge evidence is recorded separately.",
                f"/metrics/{i}/type_params/{attr}", f"/metrics[name={metric.name}]/type_params/{attr}", affected=affected)
    for relationship in document.relationships or []:
        datasets = {d.name: d for d in document.datasets}
        endpoints = {"from": relationship.from_columns, "to": relationship.to_columns}
        fields = {"from": [f.name for f in datasets[relationship.from_dataset].fields or []],
                  "to": [f.name for f in datasets[relationship.to].fields or []]}
        missing = {side: [n for n in cols if n not in fields[side]] for side, cols in endpoints.items()}
        add(f"relationship.{relationship.name}", "join endpoints and cardinality", endpoints, {"fields": fields, "unresolved": missing},
            "CONVERTER_DEFECT" if any(missing.values()) else "PRESERVED",
            "Physical identifiers are not exported field names. Downstream relationship rejection originates in the dbt export.",
            "/semantic_models/*/entities", f"/relationships[name={relationship.name}]", stage="MSI -> Ossie",
            overlap=["https://github.com/apache/ossie/pull/315", "https://github.com/apache/ossie/pull/486"])
        rows[-1]["target"]["artifact"] = "converted.ossie.yml"
    return rows


def changes(before, after, pointer=""):
    """Exact intervention ledger. List replacements deliberately retain all before/after values."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [item for key in sorted(before.keys() | after.keys())
                for item in changes(before.get(key), after.get(key), pointer + "/" + key)]
    return [{"pointer": pointer, "before": before, "after": after}]


def reconstructed(source, target, defaults=False, legacy=False):
    from metricflow_semantic_interfaces.implementations.time_spine_table_configuration import PydanticTimeSpineTableConfiguration
    result = deepcopy(target)
    if legacy:
        result.project_configuration.time_spine_table_configurations = [
            PydanticTimeSpineTableConfiguration(location=s.node_relation.relation_name,
                                               column_name=s.primary_column.name, grain=s.primary_column.time_granularity)
            for s in source.project_configuration.time_spines]
    else:
        result.project_configuration.time_spines = deepcopy(source.project_configuration.time_spines)
    if defaults:
        by_name = {s.name: s for s in source.semantic_models}
        for model in result.semantic_models:
            model.defaults = deepcopy(by_name[model.name].defaults)
    return result


def execute_lane(source, manifest, config, connection, run, name, references, level, parent):
    folder = run / name
    folder.mkdir(exist_ok=False)
    write_json(folder / "manifest.json", plain(manifest))
    ledger = {"schema_version": 1, "level": level, "label": name, "lossless_roundtrip_claim": False,
              "source_manifest": "source.manifest.normalized.json", "parent_manifest": parent,
              "parent_sha256": digest(run / parent), "manifest_sha256": digest(folder / "manifest.json"),
              "interventions": changes(json.loads((run / parent).read_text()), plain(manifest))}
    write_json(folder / "experiment.json", ledger)
    outcomes = compile_queries(manifest, config.sql_client, folder, connection)
    inspection = inspect_manifest(manifest)
    records = []
    for case in CASES:
        cols, rows, _ = references[case.id]
        r = record(case.id, case.metric, name, "official converted MSI + explicit ledger", "DuckDB result",
                   "MetricFlowEngine.explain -> DuckDB", table(cols, rows))
        outcome = outcomes[case.id]
        r.update(structural_validity=True, semantic_preservation=(inspection["metrics"][case.metric]["equal"] and
                 all(v["equal"] for v in inspection["models"].values())), scenario=name,
                 semantic_coverage={"scope": inspection["scope"], "metric": inspection["metrics"][case.metric], "models": inspection["models"]})
        r["artifacts"] = [f"{name}/manifest.json", f"{name}/experiment.json"]
        sql_file = folder / f"{case.id}.sql"
        if sql_file.exists():
            r["artifacts"].append(f"{name}/{case.id}.sql")
        r["source_sql"] = f"native/{case.id}.sql"
        if "rows" in outcome:
            actual_cols, actual_rows = normalized_rows(case, outcome["columns"], outcome["rows"])
            numeric_record(r, actual_cols, actual_rows, cols, rows, [c for c in cols if c != case.metric])
        else:
            r["diagnostics"] = [{"phase": "sql_execution" if sql_file.exists() else outcome.get("phase"),
                                 "error": outcome.get("error")}]
        records.append(finalize(r))
    return records


def summarize(cases):
    return {path: {"cases": len(items), "executed": sum(r["execution_status"] == "EXECUTED" for r in items),
                   "matching_oracle": sum(bool(r.get("comparison", {}).get("equal")) for r in items if r.get("comparison")),
                   "statuses": dict(sorted(Counter(r["status"] for r in items).items()))}
            for path in sorted({r["path"] for r in cases}) for items in [[r for r in cases if r["path"] == path]]}


def worker():
    from dbt_metricflow.cli.cli_configuration import CLIConfiguration
    from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest
    from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
    from ossie_dbt import MSIToOssieConverter
    from .regressions import run_regressions, microsoft_probe

    run, report = benchmark()
    project = run / "dbt"
    source = parse_manifest_from_dbt_generated_manifest((project / "target/semantic_manifest.json").read_text())
    target = PydanticSemanticManifest.parse_raw((run / "roundtrip.manifest.json").read_text())
    document = MSIToOssieConverter().convert(source, ossie_model_name="synthetic_shop").output
    # Canonical serialization permits exact intervention ledgers without omitted-default noise.
    write_json(run / "roundtrip.canonical.json", plain(target))
    findings = inventory(source, target, document)
    config = CLIConfiguration()
    config.setup(dbt_profiles_path=project, dbt_project_path=project, configure_file_logging=False)
    try:
        with duckdb.connect() as oracle_db, duckdb.connect(str(project / "benchmark.duckdb")) as conn:
            load_data(oracle_db)
            refs = {c.id: oracle(oracle_db, c) for c in CASES}
            for name, defaults, legacy in (("level3_spine", False, False), ("level3_legacy_spine", False, True),
                                           ("level3_spine_defaults", True, False)):
                manifest = reconstructed(source, target, defaults=defaults, legacy=legacy)
                report["cases"].extend(execute_lane(source, manifest, config, conn, run, name, refs, 3, "roundtrip.canonical.json"))
            regressions = run_regressions(source, config, conn, run)
        microsoft = microsoft_probe(document, run)
    finally:
        config.dbt_artifacts.adapter.cleanup_connections()
    for finding in findings:
        for side in ("source", "target"):
            finding[side]["sha256"] = digest(run / finding[side]["artifact"])
    report["semantic_diagnostics"] = {"schema_version": 1, "environment": environment(), "findings": findings,
        "time_spine": spine_diagnosis(source, target),
        "implementation_sha256": {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted([
            *(LAB / "scripts").glob("*.py"), *(LAB / "tests").glob("*.py"), LAB / "docs/report.schema.json",
            LAB / "results/task02/upstream-repair-v2/field-name-keys.patch"])},
        "experiments": ["level3_spine", "level3_legacy_spine", "level3_spine_defaults"],
        "level2": {"execution_status": "NOT_EXECUTED", "reason": "No separate external spine argument exists in the pinned SemanticManifestLookup API; it reads manifest project_configuration. Assigning a source spine to the returned manifest is Level 3."},
        "regressions": regressions, "microsoft": microsoft, "summary": summarize(report["cases"])}
    report["ossie_commit"] = report["semantic_diagnostics"]["environment"]["ossie_commit"]
    report["status_counts"] = dict(sorted(Counter(r["status"] for r in report["cases"]).items()))
    report["path_counts"] = {p: s["statuses"] for p, s in summarize(report["cases"]).items()}
    # Existing v1 acceptance remains separate from Task 02 completeness.
    for path in sorted(run.rglob("*")):
        if path.is_file() and "dbt" not in path.relative_to(run).parts and path.name != "report.json":
            if path.suffix in {".json", ".sql", ".yml", ".bim"}:
                content = sanitize(path.read_text(encoding="utf-8")).replace(str(run), "<run>").replace(run.as_posix(), "<run>")
                path.write_text(content, encoding="utf-8")
    report = sanitize(report)
    report = json.loads(json.dumps(report).replace(str(run).replace("\\", "\\\\"), "<run>").replace(run.as_posix(), "<run>"))
    report["artifact_sha256"] = {p.relative_to(run).as_posix(): digest(p) for p in sorted(run.rglob("*"))
                                if p.is_file() and "dbt" not in p.relative_to(run).parts and p.name != "report.json"
                                and p.suffix in {".json", ".sql", ".yml", ".bim"}}
    write_json(run / "report.json", report)
    return run, report


def capture(run, destination):
    """Only portable, hash-bound artifacts; never refresh an existing capture."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    report = json.loads((Path(run) / "report.json").read_text())
    # Preflight every artifact before creating a directory that could look like a complete capture.
    for relative in report["artifact_sha256"]:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Artifact escapes run directory")
        source = Path(run) / path
        if digest(source) != report["artifact_sha256"][relative]:
            raise ValueError("Artifact hash mismatch: " + relative)
    destination.mkdir(parents=True, exist_ok=False)
    for relative in [*report["artifact_sha256"], "report.json"]:
        path = Path(relative)
        source = Path(run) / path
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def diagnose(upstream_python=None, repair_python=None):
    base_run, report = worker()
    siblings = {}
    for label, executable in (("current", upstream_python), ("repair", repair_python)):
        if not executable:
            continue
        completed = subprocess.run([str(Path(executable).resolve()), "-m", "semantic_lab", "diagnose-worker"],
                                   cwd=LAB, capture_output=True, text=True, encoding="utf-8", timeout=300)
        if completed.returncode:
            raise RuntimeError(label + " diagnostic worker failed: " + completed.stdout + completed.stderr)
        summary = json.loads(completed.stdout)
        sibling_run = ROOT / summary["report"]
        target = base_run / label
        capture(sibling_run.parent, target)
        siblings[label] = json.loads((target / "report.json").read_text())
    report["semantic_diagnostics"]["comparisons"] = {
        label: {"report": f"{label}/report.json", "environment": r["semantic_diagnostics"]["environment"],
                "summary": r["semantic_diagnostics"]["summary"], "regressions": r["semantic_diagnostics"]["regressions"]}
        for label, r in siblings.items()}
    failures = []
    if not report["acceptance"]["accepted"]:
        failures.append("Original benchmark acceptance failed")
    if "current" not in siblings:
        failures.append("Current-upstream execution missing; provide --upstream-python")
    elif siblings["current"]["ossie_commit"] != CURRENT_COMMIT:
        failures.append("Current-upstream environment does not match the recorded immutable commit")
    elif siblings["current"]["semantic_diagnostics"]["environment"]["tracked_source_modified"]:
        failures.append("Current-upstream checkout has tracked changes")
    for label, r in siblings.items():
        if not r["acceptance"]["accepted"]:
            failures.append(label + " original acceptance failed")
        actual = r["semantic_diagnostics"]["environment"]["packages"]
        expected = report["semantic_diagnostics"]["environment"]["packages"]
        differences = {n: [v, actual.get(n)] for n,v in expected.items() if not n.lower().startswith("apache-ossie") and actual.get(n) != v}
        if differences:
            failures.append(label + " changes baseline runtime dependencies: " + repr(differences))
    for label, r in [("baseline", report), *siblings.items()]:
        regressions = r["semantic_diagnostics"]["regressions"]
        if len(regressions) != 3 or not all(x["native_matches_oracle"] for x in regressions):
            failures.append(label + " missing or invalid independently checked regression sources")
    if "repair" in siblings and not siblings["repair"]["semantic_diagnostics"]["summary"]["level3_spine_defaults"]["executed"]:
        failures.append("Repair experiment has no executed round-trip queries")
    report["task02_acceptance"] = {"accepted": not failures, "failures": failures}
    report["artifact_sha256"].update({p.relative_to(base_run).as_posix(): digest(p) for label in siblings
                                     for p in sorted((base_run / label).rglob("*")) if p.is_file()})
    write_json(base_run / "report.json", report)
    return base_run, report
