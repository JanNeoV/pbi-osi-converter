from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from semantic_poc.agent.conversion_review import (
    FailureCategory,
    FindingSeverity,
    build_conversion_findings,
)
from semantic_poc.agent.import_models import canonical_json_text
from semantic_poc.agent.import_store import ImportStore
from semantic_poc.agent.import_workflow import create_import_run
from semantic_poc.agent.powerbi_import import DaxAnalysis, analyze_dax_measure
from semantic_poc.src.models import PROJECT_ROOT, load_yaml
from semantic_poc.src.semantic_ir import (
    build_canonical_metric_ir_index,
    generate_dax_definition,
    generate_snowflake_definition,
)


BENCHMARK_ROOT = PROJECT_ROOT / "semantic_poc" / "tests" / "fixtures" / "conversion_benchmark"
DEFAULT_OUTPUT = PROJECT_ROOT / "semantic_poc" / "benchmark" / "output"
ABSOLUTE_TOLERANCE = Decimal("1e-9")
RELATIVE_TOLERANCE = Decimal("1e-9")


@dataclass(frozen=True)
class FixtureSpec:
    fixture_id: str
    model_dir: Path
    dataset: Path
    canonical: Path
    autopilot: Path | None
    expected_failures: tuple[str, ...]


FIXTURES = (
    FixtureSpec(
        "A_SUPPORTED",
        BENCHMARK_ROOT / "a_supported.SemanticModel",
        BENCHMARK_ROOT / "a_supported.csv",
        BENCHMARK_ROOT / "a_expected_canonical.yml",
        None,
        (),
    ),
    FixtureSpec(
        "B_SEMANTIC_TRAPS",
        BENCHMARK_ROOT / "b_semantic_traps.SemanticModel",
        BENCHMARK_ROOT / "b_semantic_traps.csv",
        BENCHMARK_ROOT / "b_expected_canonical.yml",
        BENCHMARK_ROOT / "b_synthetic_autopilot.yml",
        ("Hours", "Identifier Total"),
    ),
    FixtureSpec(
        "C_UNSUPPORTED",
        BENCHMARK_ROOT / "c_unsupported.SemanticModel",
        BENCHMARK_ROOT / "c_unsupported.csv",
        BENCHMARK_ROOT / "c_expected_canonical.yml",
        None,
        (),
    ),
)


def _parse_cell(value: str) -> Any:
    if value == "":
        return None
    lowered = value.casefold()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return Decimal(value)
    except InvalidOperation:
        return value


def _rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [
            {str(key): _parse_cell(str(value)) for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _truthy_equal(left: Any, right: Any) -> bool:
    if isinstance(right, bool):
        if isinstance(left, Decimal):
            return left == Decimal(1 if right else 0)
        return bool(left) is right
    if isinstance(right, (int, float)):
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except InvalidOperation:
            return False
    return left == right


def evaluate_dax_subset(
    analysis: DaxAnalysis,
    rows: Sequence[Mapping[str, Any]],
    dependency_values: Mapping[str, Any],
) -> Any:
    if not analysis.supported or analysis.ast is None or analysis.pattern is None:
        raise ValueError("Only supported DAX ASTs can be evaluated by the benchmark.")
    ast = analysis.ast
    pattern = analysis.pattern
    if pattern == "COUNT":
        return len(rows)
    if pattern == "COLUMN_COUNT":
        return sum(row.get(str(ast["column"])) is not None for row in rows)
    if pattern == "SUM":
        values = [row.get(str(ast["column"])) for row in rows]
        return sum((Decimal(str(value)) for value in values if value is not None), Decimal(0))
    if pattern == "DISTINCT_COUNT":
        return len({row.get(str(ast["column"])) for row in rows})
    if pattern == "SCALED_SUM":
        total = sum(
            (
                Decimal(str(row.get(str(ast["column"]))))
                for row in rows
                if row.get(str(ast["column"])) is not None
            ),
            Decimal(0),
        )
        return total / Decimal(str(ast["divisor"]))
    if pattern == "FILTERED_COUNT":
        return sum(
            all(_truthy_equal(row.get(item.column), item.value) for item in analysis.filters)
            for row in rows
        )
    if pattern == "RATIO":
        numerator = dependency_values[str(ast["numerator"]).casefold()]
        denominator = dependency_values[str(ast["denominator"]).casefold()]
        if denominator in {None, 0, Decimal(0)}:
            return None
        return Decimal(str(numerator)) / Decimal(str(denominator))
    raise ValueError(f"Unsupported benchmark DAX pattern: {pattern}")


def _sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sqlite_value(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _sqlite_reference(
    analysis: DaxAnalysis,
    rows: Sequence[Mapping[str, Any]],
    dependency_values: Mapping[str, Any],
) -> Any:
    if analysis.ast is None or analysis.pattern is None:
        raise ValueError("Reference SQL requires a supported AST.")
    columns = sorted({key for row in rows for key in row})
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE fixture (" + ", ".join(f"{_sqlite_identifier(name)}" for name in columns) + ")"
        )
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO fixture VALUES ({placeholders})",
            [[_sqlite_value(row.get(name)) for name in columns] for row in rows],
        )
        ast = analysis.ast
        pattern = analysis.pattern
        parameters: list[Any] = []
        if pattern == "COUNT":
            expression = "COUNT(*)"
            where = ""
        elif pattern == "COLUMN_COUNT":
            expression = f"COUNT({_sqlite_identifier(str(ast['column']))})"
            where = ""
        elif pattern == "SUM":
            expression = f"SUM({_sqlite_identifier(str(ast['column']))})"
            where = ""
        elif pattern == "DISTINCT_COUNT":
            field = _sqlite_identifier(str(ast["column"]))
            expression = (
                f"COUNT(DISTINCT {field}) + CASE WHEN SUM(CASE WHEN {field} IS NULL THEN 1 ELSE 0 END) > 0 "
                "THEN 1 ELSE 0 END"
            )
            where = ""
        elif pattern == "SCALED_SUM":
            expression = (
                f"SUM({_sqlite_identifier(str(ast['column']))}) / {str(ast['divisor'])}"
            )
            where = ""
        elif pattern == "FILTERED_COUNT":
            expression = "COUNT(*)"
            clauses = []
            for item in analysis.filters:
                clauses.append(f"{_sqlite_identifier(item.column)} = ?")
                parameters.append(_sqlite_value(item.value))
            where = " WHERE " + " AND ".join(clauses)
        elif pattern == "RATIO":
            numerator = dependency_values[str(ast["numerator"]).casefold()]
            denominator = dependency_values[str(ast["denominator"]).casefold()]
            if denominator in {None, 0, Decimal(0)}:
                return None
            return Decimal(str(numerator)) / Decimal(str(denominator))
        else:
            raise ValueError(f"Unsupported reference-SQL pattern: {pattern}")
        value = connection.execute(f"SELECT {expression} FROM fixture{where}", parameters).fetchone()[0]
        if value is None:
            return None
        if pattern in {"COUNT", "COLUMN_COUNT", "DISTINCT_COUNT", "FILTERED_COUNT"}:
            return int(value)
        return Decimal(str(value))
    finally:
        connection.close()


def evaluate_snowflake_subset(
    expression: str,
    rows: Sequence[Mapping[str, Any]],
    metric_values: Mapping[str, Any],
) -> Any:
    compact = re.sub(r"\s+", " ", expression.strip())
    if compact == "COUNT(*)":
        return len(rows)
    match = re.fullmatch(r"COUNT\((?!DISTINCT )([A-Za-z_][A-Za-z0-9_]*)\)", compact, re.IGNORECASE)
    if match:
        return sum(row.get(match.group(1)) is not None for row in rows)
    match = re.fullmatch(r"SUM\(([A-Za-z_][A-Za-z0-9_]*)\)", compact, re.IGNORECASE)
    if match:
        return sum(
            (Decimal(str(row.get(match.group(1)))) for row in rows if row.get(match.group(1)) is not None),
            Decimal(0),
        )
    match = re.fullmatch(
        r"SUM\(([A-Za-z_][A-Za-z0-9_]*)\) / (\d+(?:\.\d+)?)",
        compact,
        re.IGNORECASE,
    )
    if match:
        total = sum(
            (Decimal(str(row.get(match.group(1)))) for row in rows if row.get(match.group(1)) is not None),
            Decimal(0),
        )
        return total / Decimal(match.group(2))
    match = re.fullmatch(
        r"COUNT\(DISTINCT ([A-Za-z_][A-Za-z0-9_]*)\) \+ IFF\(COUNT_IF\(\1 IS NULL\) > 0, 1, 0\)",
        compact,
        re.IGNORECASE,
    )
    if match:
        return len({row.get(match.group(1)) for row in rows})
    match = re.fullmatch(r"COUNT_IF\((.+)\)", compact, re.IGNORECASE)
    if match:
        predicate = match.group(1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", predicate):
            return sum(bool(row.get(predicate)) for row in rows)
        equality = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_]*) = (TRUE|FALSE|-?\d+(?:\.\d+)?|'(?:''|[^'])*')",
            predicate,
            re.IGNORECASE,
        )
        if equality:
            raw = equality.group(2)
            if raw.upper() in {"TRUE", "FALSE"}:
                expected: Any = raw.upper() == "TRUE"
            elif raw.startswith("'"):
                expected = raw[1:-1].replace("''", "'")
            else:
                expected = Decimal(raw)
            return sum(_truthy_equal(row.get(equality.group(1)), expected) for row in rows)
    ratio = re.fullmatch(
        r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*) / NULLIF\((?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*), 0\)",
        compact,
        re.IGNORECASE,
    )
    if ratio:
        numerator = metric_values[ratio.group(1).casefold()]
        denominator = metric_values[ratio.group(2).casefold()]
        if denominator in {None, 0, Decimal(0)}:
            return None
        return Decimal(str(numerator)) / Decimal(str(denominator))
    raise ValueError(f"Generated Snowflake expression is outside the benchmark evaluator: {expression}")


def _equivalent(left: Any, right: Any, *, count: bool) -> bool:
    if left is None or right is None:
        return left is right
    if count:
        return type(left) is int and type(right) is int and left == right
    left_decimal = Decimal(str(left))
    right_decimal = Decimal(str(right))
    difference = abs(left_decimal - right_decimal)
    scale = max(abs(left_decimal), abs(right_decimal))
    return difference <= max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * scale)


def _json_result(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


def run_golden_tests(run: Any, dataset: Path) -> list[dict[str, Any]]:
    rows = _rows(dataset)
    measures = {
        str(item["name"]).casefold(): item
        for item in run.inventory.get("measures", [])
        if isinstance(item, Mapping)
    }
    candidates = {
        str(item["source_measure"]).casefold(): item for item in run.classifications
    }
    dax_values: dict[str, Any] = {}
    reference_values: dict[str, Any] = {}
    snowflake_values: dict[str, Any] = {}
    pending = set(measures)
    while pending:
        progressed = False
        for key in sorted(tuple(pending)):
            measure = measures[key]
            analysis = DaxAnalysis.from_dict(measure["analysis"])
            dependencies = {name.casefold() for name in analysis.measure_dependencies}
            if not analysis.supported or not dependencies <= set(dax_values):
                if not analysis.supported:
                    pending.remove(key)
                    progressed = True
                continue
            dax_values[key] = evaluate_dax_subset(analysis, rows, dax_values)
            reference_values[key] = _sqlite_reference(analysis, rows, reference_values)
            pending.remove(key)
            progressed = True
        if not progressed:
            break

    snowflake_by_name: dict[str, Mapping[str, Any]] = {}
    source_to_snowflake: dict[str, str] = {}
    for key, candidate in candidates.items():
        definition = candidate.get("regenerated_snowflake")
        if isinstance(definition, Mapping) and definition.get("name") and definition.get("expr"):
            name = str(definition["name"]).casefold()
            snowflake_by_name[name] = definition
            source_to_snowflake[key] = name
    pending_snowflake = set(snowflake_by_name)
    while pending_snowflake:
        progressed = False
        for name in sorted(tuple(pending_snowflake)):
            expression = str(snowflake_by_name[name]["expr"])
            try:
                snowflake_values[name] = evaluate_snowflake_subset(
                    expression, rows, snowflake_values
                )
            except KeyError:
                continue
            pending_snowflake.remove(name)
            progressed = True
        if not progressed:
            break

    results: list[dict[str, Any]] = []
    for key in sorted(dax_values):
        analysis = DaxAnalysis.from_dict(measures[key]["analysis"])
        count = analysis.pattern in {"COUNT", "COLUMN_COUNT", "DISTINCT_COUNT", "FILTERED_COUNT"}
        target_name = source_to_snowflake.get(key)
        target_value = snowflake_values.get(target_name) if target_name else None
        reference_ok = _equivalent(dax_values[key], reference_values[key], count=count)
        target_ok = target_name is not None and _equivalent(dax_values[key], target_value, count=count)
        results.append(
            {
                "source_measure": measures[key]["name"],
                "pattern": analysis.pattern,
                "power_bi_dax_evaluator": _json_result(dax_values[key]),
                "direct_reference_sql": _json_result(reference_values[key]),
                "repository_snowflake_evaluator": _json_result(target_value),
                "reference_match": reference_ok,
                "repository_match": target_ok,
                "status": "PASS" if reference_ok and target_ok else "FAIL",
            }
        )
    return results


def run_candidate_golden_tests(canonical_path: Path, dataset: Path) -> list[dict[str, Any]]:
    """Evaluate compiler-generated targets against the independent fixture reference.

    Unlike ``run_golden_tests``, this evaluates the governed candidate definitions,
    not the original Power BI source expressions.  It is therefore suitable for
    proving reviewed corrections without editing the source fixture.
    """

    rows = _rows(dataset)
    index = build_canonical_metric_ir_index(
        load_yaml(canonical_path), canonical_source="models/semantic/triathlon_semantic.yml"
    )
    known_measures = [metric.power_bi.measure for metric in index.values()]
    measure_to_metric = {
        metric.power_bi.measure.casefold(): name for name, metric in index.items()
    }
    dax_values: dict[str, Any] = {}
    reference_values: dict[str, Any] = {}
    snowflake_values: dict[str, Any] = {}
    analyses: dict[str, DaxAnalysis] = {}
    generated_targets: dict[str, tuple[str, str]] = {}
    pending = set(index)
    while pending:
        progressed = False
        for name in sorted(tuple(pending)):
            metric = index[name]
            dax = generate_dax_definition(metric, index)
            snowflake = generate_snowflake_definition(metric, index)
            if dax.definition is None or snowflake.definition is None:
                pending.remove(name)
                progressed = True
                continue
            analysis = analyze_dax_measure(
                str(dax.definition), known_measure_names=known_measures
            )
            dependencies = {
                measure_to_metric.get(item.casefold())
                for item in analysis.measure_dependencies
            }
            if None in dependencies or not dependencies <= set(dax_values):
                continue
            dax_dependency_values = {
                index[dependency].power_bi.measure.casefold(): dax_values[dependency]
                for dependency in dependencies
                if dependency is not None
            }
            reference_dependency_values = {
                index[dependency].power_bi.measure.casefold(): reference_values[dependency]
                for dependency in dependencies
                if dependency is not None
            }
            dax_values[name] = evaluate_dax_subset(
                analysis, rows, dax_dependency_values
            )
            reference_values[name] = _sqlite_reference(
                analysis, rows, reference_dependency_values
            )
            snowflake_values[str(snowflake.definition["name"]).casefold()] = (
                evaluate_snowflake_subset(
                    str(snowflake.definition["expr"]), rows, snowflake_values
                )
            )
            analyses[name] = analysis
            generated_targets[name] = (
                str(dax.definition),
                str(snowflake.definition["expr"]),
            )
            pending.remove(name)
            progressed = True
        if not progressed:
            raise ValueError(
                "Governed candidate dependencies could not be evaluated deterministically: "
                + ", ".join(sorted(pending))
            )

    results: list[dict[str, Any]] = []
    for name in sorted(generated_targets):
        metric = index[name]
        analysis = analyses[name]
        snowflake_name = generate_snowflake_definition(metric, index).definition["name"]
        snowflake_value = snowflake_values[str(snowflake_name).casefold()]
        count = analysis.pattern in {
            "COUNT",
            "COLUMN_COUNT",
            "DISTINCT_COUNT",
            "FILTERED_COUNT",
        }
        reference_ok = _equivalent(
            dax_values[name], reference_values[name], count=count
        )
        snowflake_ok = _equivalent(
            dax_values[name], snowflake_value, count=count
        )
        results.append(
            {
                "canonical_metric": name,
                "power_bi_measure": metric.power_bi.measure,
                "power_bi_candidate": generated_targets[name][0],
                "snowflake_candidate": generated_targets[name][1],
                "power_bi_value": _json_result(dax_values[name]),
                "direct_reference_value": _json_result(reference_values[name]),
                "snowflake_value": _json_result(snowflake_value),
                "status": "PASS" if reference_ok and snowflake_ok else "FAIL",
            }
        )
    return results


def _maintenance_proof(canonical_path: Path) -> dict[str, Any]:
    canonical = load_yaml(canonical_path)
    before_index = build_canonical_metric_ir_index(
        canonical, canonical_source="models/semantic/triathlon_semantic.yml"
    )
    metric_name = "valid_rows"
    before_metric = before_index[metric_name]
    before_dax = generate_dax_definition(before_metric, before_index)
    before_snowflake = generate_snowflake_definition(before_metric, before_index)
    before_targets = {"dax": before_dax.definition, "snowflake": before_snowflake.definition}
    changed = deepcopy(canonical)
    model = changed["semantic_models"][0]
    measure = next(item for item in model["measures"] if item["name"] == "valid_rows_measure")
    measure["expr"] = "is_valid = FALSE"
    changed_index = build_canonical_metric_ir_index(
        changed, canonical_source="models/semantic/triathlon_semantic.yml"
    )
    changed_metric = changed_index[metric_name]
    changed_dax = generate_dax_definition(changed_metric, changed_index)
    changed_snowflake = generate_snowflake_definition(changed_metric, changed_index)
    changed_targets = {"dax": changed_dax.definition, "snowflake": changed_snowflake.definition}
    unrelated_before = {
        name: generate_snowflake_definition(metric, before_index).definition
        for name, metric in before_index.items()
        if name != metric_name
    }
    unrelated_after = {
        name: generate_snowflake_definition(metric, changed_index).definition
        for name, metric in changed_index.items()
        if name != metric_name
    }
    restored_index = build_canonical_metric_ir_index(
        canonical, canonical_source="models/semantic/triathlon_semantic.yml"
    )
    restored_targets = {
        "dax": generate_dax_definition(restored_index[metric_name], restored_index).definition,
        "snowflake": generate_snowflake_definition(
            restored_index[metric_name], restored_index
        ).definition,
    }
    source_definition = FIXTURES[0].model_dir / "definition"

    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def tree_hash(path: Path) -> str:
        value = hashlib.sha256()
        for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
            value.update(item.relative_to(path).as_posix().encode("utf-8"))
            value.update(b"\0")
            value.update(item.read_bytes())
        return value.hexdigest()

    source_powerbi_before = tree_hash(source_definition)
    with tempfile.TemporaryDirectory(prefix="semantic-m7-maintenance-") as temporary:
        workspace = Path(temporary)
        workspace_canonical = workspace / "triathlon_semantic.yml"
        workspace_canonical.write_bytes(canonical_path.read_bytes())
        copied_definition = workspace / "accepted.SemanticModel" / "definition"
        shutil.copytree(source_definition, copied_definition)
        copied_powerbi_before = tree_hash(copied_definition)
        snowflake_target = workspace / "snowflake_metric.yml"
        snowflake_before_bytes = yaml.safe_dump(
            before_targets["snowflake"], sort_keys=False
        ).encode("utf-8")
        snowflake_target.write_bytes(snowflake_before_bytes)

        changed_canonical_bytes = yaml.safe_dump(changed, sort_keys=False).encode("utf-8")
        workspace_canonical.write_bytes(changed_canonical_bytes)
        copied_measure = copied_definition / "tables" / "Measures.tmdl"
        prior_measure_bytes = copied_measure.read_bytes()
        prior_measure_text = prior_measure_bytes.decode("utf-8")
        old_dax = "CALCULATE(COUNTROWS(fact_activity), fact_activity[is_valid] = TRUE())"
        new_dax = str(changed_targets["dax"])
        if prior_measure_text.count(old_dax) != 1:
            raise ValueError("Maintenance fixture has an ambiguous copied Power BI insertion point.")
        copied_measure.write_text(
            prior_measure_text.replace(old_dax, new_dax), encoding="utf-8", newline="\n"
        )
        snowflake_changed_bytes = yaml.safe_dump(
            changed_targets["snowflake"], sort_keys=False
        ).encode("utf-8")
        snowflake_target.write_bytes(snowflake_changed_bytes)

        applied_hashes = {
            "canonical": digest(workspace_canonical.read_bytes()),
            "power_bi_copy": tree_hash(copied_definition),
            "snowflake": digest(snowflake_target.read_bytes()),
        }
        validation_index = build_canonical_metric_ir_index(
            yaml.safe_load(workspace_canonical.read_bytes()),
            canonical_source="models/semantic/triathlon_semantic.yml",
        )
        validation_metric = validation_index[metric_name]
        validation_passed = (
            generate_dax_definition(validation_metric, validation_index).definition == changed_targets["dax"]
            and generate_snowflake_definition(validation_metric, validation_index).definition
            == changed_targets["snowflake"]
        )

        if digest(workspace_canonical.read_bytes()) != applied_hashes["canonical"]:
            raise ValueError("Maintenance rollback canonical hash guard failed.")
        if tree_hash(copied_definition) != applied_hashes["power_bi_copy"]:
            raise ValueError("Maintenance rollback Power BI copy hash guard failed.")
        if digest(snowflake_target.read_bytes()) != applied_hashes["snowflake"]:
            raise ValueError("Maintenance rollback Snowflake hash guard failed.")
        workspace_canonical.write_bytes(canonical_path.read_bytes())
        copied_measure.write_bytes(prior_measure_bytes)
        snowflake_target.write_bytes(snowflake_before_bytes)
        rollback_restored = (
            workspace_canonical.read_bytes() == canonical_path.read_bytes()
            and tree_hash(copied_definition) == copied_powerbi_before
            and snowflake_target.read_bytes() == snowflake_before_bytes
        )

    return {
        "change_id": "chg_20260718T180000Z_77777777",
        "states": ["PROPOSED", "APPROVED", "APPLIED_LOCAL", "VALIDATED", "ROLLED_BACK"],
        "before_targets": before_targets,
        "changed_targets": changed_targets,
        "workspace_isolated": True,
        "targets_share_changed_signature": (
            changed_dax.signature == changed_snowflake.signature == changed_metric.signature
        ),
        "unrelated_objects_unchanged": unrelated_before == unrelated_after,
        "validation_passed": validation_passed,
        "applied_hashes": applied_hashes,
        "rollback_hash_guarded": True,
        "rollback_restored_previous_targets": rollback_restored and restored_targets == before_targets,
        "source_power_bi_mutated": tree_hash(source_definition) != source_powerbi_before,
        "deployment_state": "NOT_PERFORMED",
    }


def build_maintenance_proof() -> dict[str, Any]:
    """Return the accepted deterministic M7 maintenance/rollback evidence."""

    return _maintenance_proof(FIXTURES[0].canonical)


def _fixture_run(spec: FixtureSpec, import_root: Path, offset: int) -> dict[str, Any]:
    store = ImportStore(import_root)
    run = create_import_run(
        spec.model_dir,
        store=store,
        canonical_yaml_path=spec.canonical,
        now=datetime(2026, 7, 18, 17 + offset, 0, tzinfo=timezone.utc),
        entropy=f"{offset + 1:08x}",
    )
    autopilot = yaml.safe_load(spec.autopilot.read_text(encoding="utf-8")) if spec.autopilot else None
    findings = build_conversion_findings(run, autopilot=autopilot)
    golden = run_golden_tests(run, spec.dataset)
    actual_failures = tuple(
        sorted(item["source_measure"] for item in golden if item["status"] == "FAIL")
    )
    expected_failures = tuple(sorted(spec.expected_failures))
    expected_outcome = actual_failures == expected_failures
    return {
        "fixture_id": spec.fixture_id,
        "import_id": run.import_id,
        "source_snapshot_sha256": run.source_snapshot_hash,
        "semantic_content_sha256": run.semantic_content_hash,
        "autopilot_status": "SYNTHETIC_TEST_FIXTURE" if spec.autopilot else "NOT_AVAILABLE",
        "inventory": run.inventory,
        "recognized_patterns": [
            {
                "source_object": f"{item.get('source_table')}[{item.get('source_measure')}]",
                "pattern": item.get("recognized_pattern"),
                "classification": item.get("classification"),
            }
            for item in run.classifications
        ],
        "candidate_ir": [item.get("candidate_ir") for item in run.classifications],
        "repository_snowflake": [item.get("regenerated_snowflake") for item in run.classifications],
        "findings": [item.to_dict() for item in findings],
        "golden_results": golden,
        "expected_metric_failures": list(expected_failures),
        "actual_metric_failures": list(actual_failures),
        "expected_outcome_observed": expected_outcome,
    }


def build_benchmark() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="semantic-m7-benchmark-") as temporary:
        import_root = Path(temporary) / "imports"
        fixtures = [
            _fixture_run(spec, import_root, index) for index, spec in enumerate(FIXTURES)
        ]
    maintenance = _maintenance_proof(FIXTURES[0].canonical)
    maintenance_passed = all(
        maintenance[key]
        for key in (
            "workspace_isolated",
            "targets_share_changed_signature",
            "unrelated_objects_unchanged",
            "validation_passed",
            "rollback_hash_guarded",
            "rollback_restored_previous_targets",
        )
    ) and not maintenance["source_power_bi_mutated"]
    return {
        "schema_version": 1,
        "benchmark_id": "milestone-7",
        "decimal_tolerance": {
            "absolute": str(ABSOLUTE_TOLERANCE),
            "relative": str(RELATIVE_TOLERANCE),
        },
        "counts_exact": True,
        "fixtures": fixtures,
        "maintenance_equivalence": maintenance,
        "status": (
            "PASSED"
            if all(item["expected_outcome_observed"] for item in fixtures) and maintenance_passed
            else "FAILED"
        ),
    }


def _benchmark_md(benchmark: Mapping[str, Any]) -> str:
    lines = [
        "# Conversion benchmark",
        "",
        f"Status: `{benchmark['status']}`",
        "",
        "| Fixture | Findings | Blocking | Golden failures | Expected outcome |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for fixture in benchmark["fixtures"]:
        blocking = sum(
            item["severity"] == FindingSeverity.BLOCKING.value
            for item in fixture["findings"]
        )
        lines.append(
            f"| `{fixture['fixture_id']}` | {len(fixture['findings'])} | {blocking} | "
            f"{len(fixture['actual_metric_failures'])} | "
            f"`{'PASS' if fixture['expected_outcome_observed'] else 'FAIL'}` |"
        )
    lines.extend(
        [
            "",
            "Syntactic generation is never used as semantic-success evidence; every supported metric is compared numerically.",
            "",
        ]
    )
    return "\n".join(lines)


def _lint_reports(benchmark: Mapping[str, Any]) -> tuple[str, str]:
    findings = [
        {"fixture_id": fixture["fixture_id"], **finding}
        for fixture in benchmark["fixtures"]
        for finding in fixture["findings"]
        if str(finding["rule_id"]).startswith("LINT_")
    ]
    json_report = canonical_json_text(
        {"schema_version": 1, "findings": findings}, pretty=True
    )
    lines = [
        "# Semantic lint report",
        "",
        f"Findings: {len(findings)}",
        "",
        "| Fixture | Rule | Severity | Category | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| `{item['fixture_id']}` | `{item['rule_id']}` | `{item['severity']}` | `{item['category']}` | {item['source_object']} |"
        for item in findings
    )
    return json_report, "\n".join(lines) + "\n"


def _autopilot_report(benchmark: Mapping[str, Any]) -> str:
    lines = [
        "# Snowflake Autopilot comparison",
        "",
        "No live Autopilot operation was performed.",
        "",
        "Fixture B uses a clearly labelled `SYNTHETIC_TEST_FIXTURE` to exercise normalization and failure detection.",
        "",
    ]
    fixture = next(item for item in benchmark["fixtures"] if item["fixture_id"] == "B_SEMANTIC_TRAPS")
    findings = [
        item
        for item in fixture["findings"]
        if "AUTOPILOT" in item["rule_id"] or item["source_support"] == "AUTOPILOT_EVIDENCE"
    ]
    lines.extend(
        [
            "| Finding | Severity | Category | Source |",
            "| --- | --- | --- | --- |",
            *(
                f"| `{item['finding_id']}` | `{item['severity']}` | `{item['category']}` | {item['source_object']} |"
                for item in findings
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _decision_report(benchmark: Mapping[str, Any]) -> str:
    decisions = []
    for fixture in benchmark["fixtures"]:
        for finding in fixture["findings"]:
            if finding["category"] == FailureCategory.EXACT_MATCH.value:
                before = hashlib.sha256(
                    canonical_json_text(
                        {
                            "fixture_id": fixture["fixture_id"],
                            "source_snapshot_sha256": fixture["source_snapshot_sha256"],
                            "finding_id": finding["finding_id"],
                        }
                    ).encode("utf-8")
                ).hexdigest()
                decision = {
                    "fixture_id": fixture["fixture_id"],
                    "finding_id": finding["finding_id"],
                    "action": "ACCEPT",
                    "actor": "benchmark-reviewer",
                    "timestamp": "2026-07-18T20:00:00Z",
                    "source_object": finding["source_object"],
                    "source": {"kind": "REPOSITORY_CANONICAL_MAPPING"},
                    "mapping_file_sha256": None,
                    "rationale": "EXACT_MAPPING_CONFIRMED",
                    "canonical_metric": finding["canonical_metric"],
                    "change_id": None,
                    "before_sha256": before,
                    "lifecycle_state": "DECISION_RECORDED",
                }
                decision["after_sha256"] = hashlib.sha256(
                    canonical_json_text(decision).encode("utf-8")
                ).hexdigest()
                decisions.append(decision)
    return canonical_json_text(
        {
            "schema_version": 1,
            "evidence_kind": "DETERMINISTIC_FIXTURE_REVIEW",
            "decisions": decisions,
        },
        pretty=True,
    )


def _maintenance_md(proof: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Maintenance equivalence report",
            "",
            f"- Change ID: `{proof['change_id']}`",
            f"- Lifecycle: `{' -> '.join(proof['states'])}`",
            f"- Isolated workspace: `{str(proof['workspace_isolated']).upper()}`",
            f"- Both targets share the changed IR: `{str(proof['targets_share_changed_signature']).upper()}`",
            f"- Unrelated objects unchanged: `{str(proof['unrelated_objects_unchanged']).upper()}`",
            f"- Validation passed: `{str(proof['validation_passed']).upper()}`",
            f"- Rollback hash guarded: `{str(proof['rollback_hash_guarded']).upper()}`",
            f"- Rollback restored previous targets: `{str(proof['rollback_restored_previous_targets']).upper()}`",
            f"- Source Power BI mutated: `{str(proof['source_power_bi_mutated']).upper()}`",
            f"- Deployment: `{proof['deployment_state']}`",
            "",
        ]
    )


def render_reports(benchmark: Mapping[str, Any]) -> dict[str, str]:
    lint_json, lint_md = _lint_reports(benchmark)
    return {
        "conversion-benchmark.json": canonical_json_text(benchmark, pretty=True),
        "conversion-benchmark.md": _benchmark_md(benchmark),
        "semantic-lint-report.json": lint_json,
        "semantic-lint-report.md": lint_md,
        "snowflake-autopilot-comparison.md": _autopilot_report(benchmark),
        "human-review-decisions.json": _decision_report(benchmark),
        "maintenance-equivalence-report.md": _maintenance_md(
            benchmark["maintenance_equivalence"]
        ),
    }


def write_reports(output_dir: Path, reports: Mapping[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in sorted(reports.items()):
        (output_dir / name).write_text(content, encoding="utf-8", newline="\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deterministic Milestone 7 conversion benchmark.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--check", action="store_true", help="Fail if committed reports differ from a fresh run.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    benchmark = build_benchmark()
    reports = render_reports(benchmark)
    output_dir = Path(args.output_dir)
    if args.check:
        changed = [
            name
            for name, content in reports.items()
            if not (output_dir / name).is_file()
            or (output_dir / name).read_text(encoding="utf-8") != content
        ]
        if changed:
            print("Conversion benchmark reports are stale: " + ", ".join(changed))
            return 1
    else:
        write_reports(output_dir, reports)
    print(f"Milestone 7 conversion benchmark: {benchmark['status']}")
    return 0 if benchmark["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ABSOLUTE_TOLERANCE",
    "FIXTURES",
    "RELATIVE_TOLERANCE",
    "build_benchmark",
    "evaluate_dax_subset",
    "evaluate_snowflake_subset",
    "render_reports",
    "run_candidate_golden_tests",
    "run_golden_tests",
    "build_maintenance_proof",
]
