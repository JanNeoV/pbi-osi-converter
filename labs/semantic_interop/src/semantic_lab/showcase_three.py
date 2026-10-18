"""Focused presentation of existing evidence drivers; no conversion or oracle logic."""
import json
import platform
from pathlib import Path
import shutil
import sys
import traceback

import yaml

from .conversion_demo import FIXTURE, run_demo
from .diagnostics import capture
from .integrations import write_json
from .showcase import digest, execute_metricflow, metricflow_leg, versions
from .showcase_powerbi import convert_leg, native_demo


ROUTES = (("MetricFlow", "MetricFlow"), ("Power BI", "Power BI"), ("MetricFlow", "Power BI"))


def read(directory, relative):
    path = directory / relative
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def assess_three(output):
    """Derive claims from this run, including explicit missing-evidence states."""
    first = read(output, "metricflow/to-ossie/leg.json")
    returned = read(output, "to-metricflow/metricflow/leg.json")
    execution = read(output, "to-metricflow/metricflow/execution.json")
    powerbi = read(output, "powerbi/core/report.json")
    cross = read(output, "to-powerbi/metricflow/report.json")
    source_inventory, target_inventory = first.get("source_inventory"), returned.get("target_inventory")
    inventory_available = source_inventory is not None and target_inventory is not None
    write_json(output / "static-comparison.json", {
        "metricflow_source": source_inventory, "metricflow_returned": target_inventory,
        "powerbi_bounded_checks": powerbi.get("bounded_structural_checks"),
        "metricflow_to_powerbi_inventory": cross.get("target_inventory"),
    })
    records = execution.get("cases", [])
    executed = [r for r in records if r.get("execution_status") == "EXECUTED" and r.get("target_result") is not None]
    matches = [r for r in executed if (r.get("comparison_result") or {}).get("equal") is True and r.get("oracle_result") is not None]
    complete = len(records) == execution.get("requested", 0) > 0
    numerical = ("PASS" if complete and len(matches) == len(records) else
                 "MISMATCH" if any(r.get("comparison_result") and
                                   r["comparison_result"].get("equal") is False for r in executed) else "BLOCKED")
    target_execution = "EXECUTED" if complete and len(executed) == len(records) else "BLOCKED" if execution else "NOT_EXECUTED"
    details = [
        (first, returned, "MISMATCH" if inventory_available and source_inventory != target_inventory else "UNKNOWN",
         target_execution, numerical,
         {"inventory_changed": source_inventory != target_inventory if inventory_available else None,
          "execution_diagnostics": sorted({d for r in records for d in r.get("diagnostics", [])}),
          "scope": "Inventory of time spines, entities and default time dimensions; no configuration reconstruction."},
         ["metricflow/to-ossie/leg.json", "to-metricflow/metricflow/leg.json",
          "to-metricflow/metricflow/execution.json"]),
        (read(output, "powerbi/core/to-ossie/report.json"), read(output, "powerbi/core/from-ossie/report.json"),
         powerbi.get("semantic_preservation", "UNKNOWN"), "NOT_EXECUTED", "BLOCKED",
         {"scope": powerbi.get("semantic_scope", "No complete static comparison available"),
          "runtime": "No Power BI Desktop refresh or DAX query was executed. TOM deserialization, if available, is separate."},
         ["powerbi/core/to-ossie/report.json", "powerbi/core/from-ossie/report.json", "powerbi/core/report.json"]),
        (first, cross, cross.get("semantic_preservation", "UNKNOWN"), "NOT_EXECUTED", "BLOCKED",
         {"blank_substitutions": cross.get("target_inventory", {}).get("untranslated_expressions_replaced_by_blank", []),
          "warnings": cross.get("warnings", []), "runtime": "No Power BI Desktop refresh or DAX query was executed."},
         ["metricflow/to-ossie/leg.json", "to-powerbi/metricflow/report.json", "to-powerbi/metricflow/warnings.json"]),
    ]
    paths = []
    for (source, target), (a, b, semantic, runtime, numeric, findings, evidence) in zip(ROUTES, details):
        statuses = [a.get("conversion"), b.get("conversion")]
        conversion = "PASS" if statuses == ["PASS", "PASS"] else "FAIL" if "FAIL" in statuses else "NOT_EXECUTED"
        paths.append(dict(source=source, target=target, via="Apache Ossie", conversion=conversion,
                          semantic_preservation=semantic, target_execution=runtime,
                          numerical_equivalence=numeric, findings=findings, evidence=evidence))
    return paths


def acceptance(output, paths, native_gate, errors):
    execution = read(output, "to-metricflow/metricflow/execution.json")
    records = execution.get("cases", [])
    # Only the observed missing-spine initialization failure is an accepted blocker.
    # A different runtime error must be investigated, not silently reclassified.
    known_blocker = (len(records) == execution.get("requested", 0) > 0 and all(
        r.get("execution_status") == "NOT_EXECUTED" and any(
            "At least one time spine must be configured" in d for d in r.get("diagnostics", []))
        for r in records))
    evidence = [p for path in paths for p in path["evidence"]]
    required = ["native/report.json", "native/source.manifest.json", "native/dbt-build.log",
                "python-converters.json", "definitions.json", "static-comparison.json",
                "metricflow/to-ossie/output.ossie.yml", "to-metricflow/metricflow/output.manifest.json",
                "powerbi/core/source.model.bim", "powerbi/core/to-ossie/output.ossie.yml",
                "powerbi/core/from-ossie/output.model.bim", "to-powerbi/metricflow/output.model.bim"]
    for directory in ("metricflow/to-ossie", "to-metricflow/metricflow"):
        required += [f"{directory}/{name}" for name in ("stdout.log", "stderr.log", "leg.json")]
    for directory in ("powerbi/core/to-ossie", "powerbi/core/from-ossie", "to-powerbi/metricflow"):
        required += [f"{directory}/{name}" for name in ("stdout.txt", "stderr.txt", "converter.log", "warnings.json")]
    checks = {
        "source_gate": native_gate.get("accepted") is True,
        "conversions_completed": all(p["conversion"] == "PASS" for p in paths),
        "evidence_complete": all((output / p).is_file() for p in evidence + required),
        "powerbi_bounded_comparison": paths[1]["semantic_preservation"] == "PASS",
        "returned_metricflow_attempt": known_blocker or paths[0]["numerical_equivalence"] == "PASS",
        "no_numerical_mismatch": all(p["numerical_equivalence"] != "MISMATCH" for p in paths),
        "no_driver_errors": not errors,
    }
    return {"accepted": all(checks.values()), "checks": checks}


def definitions(output):
    """Extract actual definitions, retaining dependencies rather than paraphrasing them."""
    result = {}
    for relative in ("native/source.manifest.json", "to-metricflow/metricflow/output.manifest.json"):
        manifest = read(output, relative)
        metrics = manifest.get("metrics", [])
        result[relative] = {"metrics": metrics, "measures": {
            m["name"]: m.get("measures", []) for m in manifest.get("semantic_models", [])}}
    for relative in ("metricflow/to-ossie/output.ossie.yml", "powerbi/core/to-ossie/output.ossie.yml"):
        if (output / relative).is_file():
            model = yaml.safe_load((output / relative).read_text(encoding="utf-8"))
            result[relative] = {"metrics": model.get("metrics", [])}
    for relative in ("powerbi/core/source.model.bim", "powerbi/core/from-ossie/output.model.bim",
                     "to-powerbi/metricflow/output.model.bim"):
        model = read(output, relative)
        result[relative] = {m["name"]: {k: v for k, v in m.items() if k in ("expression", "annotations")}
                            for t in model.get("model", {}).get("tables", []) for m in t.get("measures", [])}
    write_json(output / "definitions.json", result)
    return result


def render(output, report, excerpts):
    lines = ["# Three interoperability experiments", "",
             "Acceptance describes a reproducible demonstration, not successful numerical conversion.", "",
             "| Through Apache Ossie | Conversion | Static preservation | Target execution | Target oracle agreement |",
             "|---|---|---|---|---|"]
    for p in report["paths"]:
        lines.append(f"| {p['source']} → {p['target']} | {p['conversion']} | {p['semantic_preservation']} | {p['target_execution']} | {p['numerical_equivalence']} |")
    lines += ["", "## Measured source and independent oracle", "",
              "See [source results](native/report.json). These values are not converted-target results.", ""]
    for case in read(output, "native/report.json").get("cases", []):
        if case["case_id"] in ("conversion_rate__overall", "paid_revenue__overall"):
            result = case.get("target_result")
            value = result["rows"][0][0]["value"] if result and result.get("rows") else None
            readable = (f"{float(value):.0%}" if case["case_id"] == "conversion_rate__overall" else f"EUR {value}") if value is not None else "NOT_EXECUTED"
            matched = (case.get("comparison_result") or {}).get("equal")
            lines.append(f"- `{case['case_id']}`: **{readable}**; independent oracle match: `{matched}`.")
    lines += ["", "The five controlled SQL faults in that report are deliberately injected teaching cases, not upstream converter defects.",
              "", "## Definitions before and after", "",
              "Exact snippets below come from this run; [all definitions and dependencies](definitions.json) and [static inventories](static-comparison.json) retain the wider context."]
    for artifact, content in excerpts.items():
        selected = {k: {"expression": v.get("expression")} for k, v in content.items()
                    if k in ("Conversion Rate", "Paid Revenue", "conversion_rate", "paid_revenue")}
        if "metrics" in content:
            selected = {}
            for metric in content["metrics"]:
                if metric["name"] not in ("conversion_rate", "paid_revenue", "paid_revenue_base", "converted_sessions", "eligible_sessions"):
                    continue
                params = metric.get("type_params", {})
                selected[metric["name"]] = {k: v for k, v in {
                    "expression": metric.get("expression", params.get("expr")),
                    "numerator": params.get("numerator"), "denominator": params.get("denominator"),
                    "filter": metric.get("filter"), "measure": params.get("measure"),
                }.items() if v is not None}
        if selected:
            lines += ["", f"### [{artifact}]({artifact})", "", "```text"]
            lines += [f"{name}: {json.dumps(value, ensure_ascii=False)}" for name, value in selected.items()]
            lines += ["```"]
    for p in report["paths"]:
        findings = p["findings"]
        lines += ["", f"## {p['source']} → Ossie → {p['target']}", ""]
        if "inventory_changed" in findings:
            lines += [f"- Inspected inventory changed: `{findings['inventory_changed']}`."]
            lines += [f"- {d}" for d in findings["execution_diagnostics"]]
        if "blank_substitutions" in findings:
            lines += ["- Observed annotated BLANK() measures: " + ", ".join(
                f"`{item['measure']}`" for item in findings["blank_substitutions"]) + ".",
                f"- {len(findings['warnings'])} captured warnings; see the exact warning file below."]
        lines += [findings[k] for k in ("scope", "runtime") if k in findings]
        lines += [""]
        lines += [f"- [Evidence: {e}]({e})" for e in p["evidence"] if (output / e).is_file()]
    lines += ["", "Power BI static PASS covers only the inspected definitions. DAX results are unverified.",
              "No returned model was repaired. A missing time spine is an observed execution blocker; later possible failures were not observed.",
              "", "## Acceptance", "", "```json", json.dumps(report["acceptance"], indent=2), "```", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_three(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    errors = []

    def attempt(name, operation):
        try:
            return operation()
        except Exception as exc:
            errors.append({"step": name, "type": type(exc).__name__, "message": str(exc)})
            (output / f"{name}.error.log").write_text(traceback.format_exc(), encoding="utf-8")
            return None

    before = {p.relative_to(FIXTURE).as_posix(): digest(p) for p in FIXTURE.rglob("*") if p.is_file()}
    shutil.copytree(FIXTURE / "dbt", output / "inputs/dbt")
    shutil.copytree(FIXTURE / "data", output / "inputs/data")
    shutil.copyfile(FIXTURE / "contract.json", output / "inputs/contract.json")
    attempt("versions", lambda: write_json(output / "python-converters.json", versions()))
    native_gate = {"accepted": False}
    print("Running canonical source and independent oracle", flush=True)
    native = attempt("native", run_demo)
    if native:
        native_run, native_report = native
        native_gate = native_report["stage_a"]
        attempt("capture-native", lambda: capture(native_run, output / "native"))
        attempt("capture-build-log", lambda: shutil.copyfile(native_run / "dbt-build.log", output / "native/dbt-build.log"))
        if (native_run / "source.manifest.json").is_file():
            attempt("metricflow-outbound", lambda: metricflow_leg(native_run / "source.manifest.json", output / "metricflow/to-ossie", "to-ossie"))
    print("Running Power BI core round trip (static comparison only)", flush=True)
    attempt("powerbi", lambda: native_demo(output / "powerbi", include_switch=False))
    ossie = output / "metricflow/to-ossie/output.ossie.yml"
    if ossie.is_file():
        directory = output / "to-metricflow/metricflow"
        attempt("metricflow-return", lambda: metricflow_leg(ossie, directory, "from-ossie"))
        if (directory / "output.manifest.json").is_file() and native:
            attempt("metricflow-execution", lambda: execute_metricflow(directory, native_run, native_report))
        attempt("metricflow-to-powerbi", lambda: convert_leg(ossie, output / "to-powerbi/metricflow", "from-ossie"))
    paths = assess_three(output)
    excerpts = definitions(output)
    after = {p.relative_to(FIXTURE).as_posix(): digest(p) for p in FIXTURE.rglob("*") if p.is_file()}
    if before != after:
        errors.append({"step": "custody", "message": "Canonical fixture changed"})
    report = {"schema_version": 1, "scope": "three", "fixture": "fixtures/conversion_demo",
              "python": sys.version, "platform": platform.platform(), "native_gate": native_gate,
              "paths": paths, "errors": errors, "fixture_sha256": before, "fixture_unchanged": before == after,
              "tool_versions": read(output, "native/report.json").get("tool_versions", {}),
              "acceptance": acceptance(output, paths, native_gate, errors)}
    report["accepted"] = report["acceptance"]["accepted"]
    render(output, report, excerpts)
    report["implementation_sha256"] = {p.name: digest(p) for p in (
        Path(__file__), Path(__file__).with_name("showcase.py"), Path(__file__).with_name("showcase_powerbi.py"))}
    report["artifact_sha256"] = {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob("*")) if p.is_file()}
    write_json(output / "report.json", report)
    print(f"Read {output / 'report.md'}", flush=True)
    return report
