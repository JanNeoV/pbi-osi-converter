"""Capture unmodified official Databricks converter legs for the Task 04 showcase.

This module orchestrates the official Java CLI. It does not translate expressions,
repair output, create cloud resources, or emulate Databricks execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import yaml

from .fixtures import LAB, ROOT


CONVERTER_COMMIT = "53418ba8a4b8a20951334fdb9d9638aa34b514e3"
JAVA_TREE = "8309efe7fb695936e35e5f3ddab9ebace1925f63"
RECORDED_JAR_SHA256 = "09af8c0868cd56eac6a2de0e07dd59c1288506d4dd5779c9db2d3fbcec1e490f"
TOOLS = ROOT / ".tmp/task04-databricks"
DEFAULT_JAVA = TOOLS / "tools/extracted/jdk-21.0.12.1+1/bin/java.exe"
DEFAULT_JAR = TOOLS / "build/target/ossie-databricks-converter-0.1.0-SNAPSHOT.jar"
NATIVE = LAB / "fixtures/conversion_demo/targets/databricks/metric_view.yaml"
HEADLINES = {"conversion_rate": {"overall": "0.4", "Direct": "0.5", "Indirect": "0.25"},
             "paid_revenue": {"overall": "390.00", "currency": "EUR"}}
RUNTIME_BLOCKER = ("No authorized Databricks workspace/SQL warehouse session is available. "
                   "Metric View creation, analysis and MEASURE queries were not executed.")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _display(value) -> str:
    return str(value).replace(str(ROOT), "<repository>")


def _run(folder: Path, label: str, command: list, timeout: int = 120) -> dict:
    """Keep stdout and stderr separate, including failed or unavailable commands."""
    argv = [str(arg) for arg in command]
    record = {"label": label, "command": [_display(arg) for arg in argv],
              "cwd": "<repository>", "exit_code": None,
              "stdout": f"{label}.stdout.log", "stderr": f"{label}.stderr.log"}
    try:
        done = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=timeout)
        stdout, stderr = done.stdout, done.stderr
        record.update(execution_status="EXECUTED", exit_code=done.returncode)
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
        record.update(execution_status="EXECUTED", error=f"Timed out after {timeout} seconds")
    except OSError as exc:
        stdout, stderr = b"", str(exc).encode("utf-8")
        record.update(execution_status="NOT_EXECUTED", error=str(exc))
    (folder / record["stdout"]).write_bytes(stdout)
    (folder / record["stderr"]).write_bytes(stderr)
    record["warnings"] = stderr.decode("utf-8", errors="replace").splitlines()
    write_json(folder / f"{label}.command.json", record)
    return record


def _toolchain(folder: Path, java=None, jar=None) -> tuple[Path, Path, dict]:
    java_path = Path(java or (DEFAULT_JAVA if DEFAULT_JAVA.exists() else shutil.which("java") or "java"))
    jar_path = Path(jar or DEFAULT_JAR).resolve()
    provenance = {"converter_commit": CONVERTER_COMMIT, "converter_version": "0.1.0-SNAPSHOT",
                  "official_source": f"https://github.com/apache/ossie/tree/{CONVERTER_COMMIT}/converters/databricks/java",
                  "java": _display(java_path), "jar": _display(jar_path),
                  "jar_sha256": sha256(jar_path) if jar_path.is_file() else None,
                  "converter_modified": None, "runtime": "NOT_EXECUTED"}
    # The existing build's recorded provenance is retained as historical evidence,
    # not misrepresented as a new Maven build or new execution of upstream tests.
    historical = LAB / "results/task04/databricks"
    for name in ("report.json", "source-provenance.json", "build.stdout.log", "build.stderr.log"):
        if (historical / name).is_file():
            shutil.copyfile(historical / name, folder / f"prior-build.{name}")
    provenance["matches_recorded_task04_jar"] = provenance["jar_sha256"] == RECORDED_JAR_SHA256
    provenance["build_evidence"] = "prior-build.report.json (historical build; no fresh Maven build claimed)"
    commands = [_run(folder, "java-version", [java_path, "-version"])]
    checkout = TOOLS / "ossie-current"
    if (checkout / ".git").exists():
        commands.append(_run(folder, "source-commit", ["git", "-C", checkout, "rev-parse", "HEAD"]))
        commands.append(_run(folder, "source-status", ["git", "-C", checkout, "status", "--porcelain", "--", "converters/databricks/java"]))
        commands.append(_run(folder, "source-tree", ["git", "-C", checkout, "rev-parse", "HEAD:converters/databricks/java"]))
        provenance["pinned_source_verified"] = (
            (folder / "source-commit.stdout.log").read_text().strip() == CONVERTER_COMMIT
            and (folder / "source-tree.stdout.log").read_text().strip() == JAVA_TREE
            and not (folder / "source-status.stdout.log").read_text().strip()
            and all(c["exit_code"] == 0 for c in commands[1:]))
    else:
        provenance["pinned_source_verified"] = False
    # Do not label an arbitrary supplied executable as the recorded official build.
    provenance["official_build_verified"] = provenance["matches_recorded_task04_jar"]
    if provenance["official_build_verified"]:
        provenance["converter_modified"] = False
    provenance["commands"] = commands
    write_json(folder / "provenance.json", provenance)
    return java_path, jar_path, provenance


def _leg(folder: Path, label: str, source: Path, target: Path, java: Path, jar: Path,
         direction: str, flags=(), official_build_verified: bool = True) -> dict:
    argv = [java, "-jar", jar, direction, source, "-o", target, *flags]
    if official_build_verified:
        command = _run(folder, label, argv)
    else:
        command = {"label": label, "command": [_display(arg) for arg in argv], "cwd": "<repository>",
                   "execution_status": "NOT_EXECUTED", "exit_code": None, "warnings": [],
                   "stdout": f"{label}.stdout.log", "stderr": f"{label}.stderr.log",
                   "error": "JAR bytes do not match the recorded pinned official build; converter execution withheld"}
        (folder / command["stdout"]).write_bytes(b"")
        (folder / command["stderr"]).write_bytes(b"")
        write_json(folder / f"{label}.command.json", command)
    converted = command["exit_code"] == 0 and target.is_file()
    parsed = None
    error = None
    structure = {"validation": "NOT_EXECUTED"}
    if converted:
        try:
            parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("Output is not a YAML mapping")
            if direction == "import":
                from ossie import OssieDocument
                OssieDocument.model_validate(parsed)
                structure = {"validation": "PASS", "method": "Official OssieDocument.model_validate"}
            else:
                structure = {"validation": "PASS", "method": "YAML_MAPPING_ONLY; Databricks analyzer not executed"}
        except Exception as exc:
            error = str(exc)
            structure = {"validation": "FAIL", "error": error}
    write_json(folder / f"{label}.structure-validation.json", structure)
    return {"direction": "Databricks -> Apache Ossie" if direction == "import" else "Apache Ossie -> Databricks",
            "source": source.name, "target": target.name,
            "conversion": ("PASS" if converted and not error else
                           "NOT_EXECUTED" if command["execution_status"] == "NOT_EXECUTED" else "FAIL"),
            "semantic_preservation": "UNKNOWN", "numerical_equivalence": "BLOCKED",
            "structure_validation": structure,
            "parse_error": error, "command": command, "runtime_blocker": RUNTIME_BLOCKER}


def _seal(folder: Path, report: dict) -> dict:
    for leg in report.get("legs", []):
        label = "to-ossie" if leg.get("direction") == "Databricks -> Apache Ossie" else "from-ossie"
        write_json(folder / f"{label}.leg.json", leg)
    report["artifact_sha256"] = {p.relative_to(folder).as_posix(): sha256(p)
                                  for p in sorted(folder.rglob("*"))
                                  if p.is_file() and p != folder / "report.json"}
    write_json(folder / "report.json", report)
    return report


def compare_native_definitions(source: Path, target: Path) -> dict:
    """Compare declarations; distinguish the exporter's final SQL newline trim."""
    original = yaml.safe_load(source.read_text(encoding="utf-8"))
    reconstructed = yaml.safe_load(target.read_text(encoding="utf-8"))
    source_sql = original.get("source")
    target_sql = reconstructed.get("source")
    normalized_original, normalized_target = dict(original), dict(reconstructed)
    if isinstance(source_sql, str) and isinstance(target_sql, str):
        normalized_original["source"] = source_sql.rstrip()
        normalized_target["source"] = target_sql.rstrip()
    return {"parsed_yaml_equal": original == reconstructed,
            "definitions_equal_ignoring_source_sql_trailing_whitespace": normalized_original == normalized_target,
            "source_sql_trailing_whitespace_changed": source_sql != target_sql and
            normalized_original.get("source") == normalized_target.get("source"),
            "comparison_scope": "Complete YAML values including source SQL, expressions, filter and comment properties; ignores YAML formatting and YAML # comments. A separate comparison ignores only trailing whitespace of the whole source SQL string.",
            "differing_top_level_keys": [key for key in sorted(set(original) | set(reconstructed))
                                          if original.get(key) != reconstructed.get(key)]}


def run_native(output_dir: Path, java=None, jar=None) -> dict:
    """Execute the native import and export, refusing to overwrite prior evidence."""
    folder = Path(output_dir).resolve() / "databricks-roundtrip"
    folder.mkdir(parents=True, exist_ok=False)
    java_path, jar_path, provenance = _toolchain(folder, java, jar)
    source = folder / "source.metric-view.yml"
    ossie = folder / "imported.ossie.yml"
    target = folder / "roundtrip.metric-view.yml"
    shutil.copyfile(NATIVE, source)
    first = _leg(folder, "to-ossie", source, ossie, java_path, jar_path, "import",
                 ("--name", "conversion_demo_databricks"), provenance["official_build_verified"])
    if first["conversion"] == "PASS":
        second = _leg(folder, "from-ossie", ossie, target, java_path, jar_path, "export",
                      official_build_verified=provenance["official_build_verified"])
    else:
        second = {"direction": "Apache Ossie -> Databricks", "conversion": "NOT_EXECUTED",
                  "semantic_preservation": "UNKNOWN", "numerical_equivalence": "BLOCKED",
                  "reason": "Native import did not produce an input", "runtime_blocker": RUNTIME_BLOCKER}
    equality = None
    if second["conversion"] == "PASS":
        equality = compare_native_definitions(source, target)
        write_json(folder / "definition-comparison.json", equality)
    conversion = ("PASS" if first["conversion"] == second["conversion"] == "PASS" else
                  "FAIL" if "FAIL" in {first["conversion"], second["conversion"]} else "NOT_EXECUTED")
    report = {"path_id": "databricks-roundtrip", "path": "Databricks -> Apache Ossie -> Databricks",
              "source_authorship": "Existing manually authored canonical native fixture, copied unchanged",
              "source_fixture": NATIVE.relative_to(ROOT).as_posix(), "expected_business_values": HEADLINES,
              "conversion": conversion,
              "semantic_preservation": ("PASS" if equality and equality["definitions_equal_ignoring_source_sql_trailing_whitespace"] else
                                        "MISMATCH" if equality else "UNKNOWN"),
              "semantic_scope": "Declarative source definition equality; target engine semantics remain UNKNOWN",
              "numerical_equivalence": "BLOCKED", "runtime_blocker": RUNTIME_BLOCKER,
              "legs": [first, second], "definition_comparison": equality, "provenance": provenance}
    return _seal(folder, report)


def export_ossie(source_path: Path, output_dir: Path, source_name: str,
                 source_dataset: str | None = None, java=None, jar=None) -> dict:
    """Export one actual source platform's official Ossie output without repairs."""
    if source_name not in {"metricflow", "powerbi", "powerbi-switch"}:
        raise ValueError("source_name must be metricflow, powerbi or powerbi-switch")
    folder = Path(output_dir).resolve() / f"{source_name}-to-databricks"
    folder.mkdir(parents=True, exist_ok=False)
    java_path, jar_path, provenance = _toolchain(folder, java, jar)
    source = folder / "input.ossie.yml"
    shutil.copyfile(source_path, source)
    target = folder / "generated.metric-view.yml"
    flags = ("--source", source_dataset) if source_dataset else ()
    leg = _leg(folder, "from-ossie", source, target, java_path, jar_path, "export", flags,
               provenance["official_build_verified"])
    findings = []
    missing = []
    if leg["conversion"] == "PASS":
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
        generated = yaml.safe_load(target.read_text(encoding="utf-8"))
        source_metrics = {m["name"] for model in document.get("semantic_model", [document])
                          for m in model.get("metrics", [])}
        target_metrics = {m["name"] for m in generated.get("measures", [])}
        missing = sorted(source_metrics - target_metrics)
        if missing:
            findings.append({"kind": "METRICS_DROPPED", "metrics": missing})
        # Exact known source references are absent from both the raw fixture and
        # the generated dimensions. This records a static defect, not a claimed
        # Databricks analyzer/runtime failure.
        expressions = "\n".join(m.get("expr", "") for m in generated.get("measures", []))
        unresolved = [token for token in ("session__is_eligible", "order__order_status") if token in expressions]
        if unresolved:
            findings.append({"kind": "UNRESOLVED_METRICFLOW_TOKENS", "tokens": unresolved,
                             "scope": "Static comparison with canonical raw/derived column and dimension names; no Databricks analyzer executed"})
    # Declared expression loss is a semantic mismatch even without an engine.
    semantic = "MISMATCH" if missing or findings else "UNKNOWN"
    leg["semantic_preservation"] = semantic
    report = {"path_id": f"{source_name}-to-databricks", "path": f"{source_name} -> Apache Ossie -> Databricks",
              "source_ossie": _display(Path(source_path).resolve()), "source_ossie_sha256": sha256(source),
              "expected_business_values": HEADLINES, "conversion": leg["conversion"],
              "semantic_preservation": semantic, "numerical_equivalence": "BLOCKED",
              "runtime_blocker": RUNTIME_BLOCKER, "legs": [leg], "static_findings": findings,
              "provenance": provenance, "source_dataset_argument": source_dataset}
    return _seal(folder, report)


def run_databricks(output_dir: Path, ossie_sources: dict[str, Path] | None = None,
                   java=None, jar=None) -> dict:
    reports = {"databricks-roundtrip": run_native(output_dir, java=java, jar=jar)}
    for name, source in (ossie_sources or {}).items():
        reports[f"{name}-to-databricks"] = export_ossie(
            source, output_dir, name, source_dataset="sessions" if name == "metricflow" else "Sessions",
            java=java, jar=jar)
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--ossie", action="append", default=[], metavar="PLATFORM=PATH")
    parser.add_argument("--java", type=Path)
    parser.add_argument("--jar", type=Path)
    parser.add_argument("--exports-only", action="store_true", help="Append export lanes after a prior native capture")
    args = parser.parse_args()
    sources = dict(item.split("=", 1) for item in args.ossie)
    if args.exports_only:
        reports = {f"{name}-to-databricks": export_ossie(
            Path(path), args.output, name, source_dataset="sessions" if name == "metricflow" else "Sessions",
            java=args.java, jar=args.jar) for name, path in sources.items()}
    else:
        reports = run_databricks(args.output, {name: Path(path) for name, path in sources.items()}, args.java, args.jar)
    print(json.dumps({key: {name: value.get(name) for name in
                           ("conversion", "semantic_preservation", "numerical_equivalence")}
                      for key, value in reports.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
