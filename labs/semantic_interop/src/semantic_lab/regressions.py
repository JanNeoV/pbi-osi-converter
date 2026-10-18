"""Small, replayable counterexamples and Microsoft metadata probes; no DAX runtime."""
from copy import deepcopy
import json
import warnings

from .compare import compare, table
from .fixtures import Case, FIXTURES
from .integrations import compile_queries, write_json


def small_source(source, spec):
    from metricflow_semantic_interfaces.implementations.filters.where_filter import PydanticWhereFilter, PydanticWhereFilterIntersection
    result = deepcopy(source)
    result.semantic_models = [deepcopy(next(m for m in source.semantic_models if m.name == "items"))]
    model = result.semantic_models[0]
    model.entities = [deepcopy(model.entities[0])]
    model.entities[0].name = spec["entity"]
    model.measures = []  # The parsed metric has its own aggregation parameters in this pinned MSI.
    model.node_relation.alias = "regression_items"
    model.node_relation.relation_name = '"benchmark"."main"."regression_items"'
    result.metrics = [deepcopy(next(m for m in source.metrics if m.name == "revenue"))]
    metric = result.metrics[0]
    metric.type_params.expr = spec["expr"]
    metric.type_params.measure = None
    metric.type_params.input_measures = []
    if spec["filter"]:
        metric.filter = PydanticWhereFilterIntersection(where_filters=[PydanticWhereFilter(where_sql_template=spec["filter"])])
    return result


def evaluate_small(source, spec, config, conn, directory):
    from ossie_dbt import MSIToOssieConverter, OssieToMSIConverter
    from .diagnostics import plain, reconstructed, changes
    directory.mkdir(parents=True, exist_ok=False)
    case = Case(spec["id"], "revenue")
    native = compile_queries(source, config.sql_client, directory / "native", conn, [case])[case.id]
    outward = MSIToOssieConverter().convert(source)
    inward = OssieToMSIConverter().convert(outward.output)
    target = inward.output
    ready = reconstructed(source, target, defaults=True)
    observed = compile_queries(ready, config.sql_client, directory / "returned", conn, [case])[case.id]
    expected = conn.execute(spec["oracle"]).fetchall()
    write_json(directory / "source.json", plain(source))
    (directory / "converted.yml").write_text(outward.output.to_ossie_yaml(), encoding="utf-8")
    write_json(directory / "returned.json", plain(target))
    write_json(directory / "configured.json", plain(ready))
    write_json(directory / "interventions.json", {"level": 3, "changes": changes(plain(target), plain(ready))})
    (directory / "oracle.sql").write_text(spec["oracle"] + "\n", encoding="utf-8")
    equal_native = "rows" in native and compare(["revenue"], expected, native["columns"], native["rows"])["equal"]
    equal_target = "rows" in observed and compare(["revenue"], expected, observed["columns"], observed["rows"])["equal"]
    return {"id": spec["id"], "mechanism": spec["mechanism"], "native_matches_oracle": equal_native,
            "oracle": table(["revenue"], expected), "native": table(native["columns"], native["rows"]) if "rows" in native else native,
            "returned": table(observed["columns"], observed["rows"]) if "rows" in observed else observed,
            "returned_executed": "rows" in observed, "returned_matches_oracle": equal_target,
            "entity_names": [e.name for e in target.semantic_models[0].entities],
            "source_entity_names": [e.name for e in source.semantic_models[0].entities],
            "metric_expression": target.metrics[0].type_params.expr if target.metrics else None,
            "issues": [i.issue_type.value for i in outward.issues + inward.issues]}


def run_regressions(source, config, conn, run):
    fixture = json.loads((FIXTURES / "regressions/cases.json").read_text())
    conn.execute("CREATE TABLE regression_items (item_id INTEGER, order_date DATE, status VARCHAR, amount DECIMAL(18,2))")
    conn.executemany("INSERT INTO regression_items VALUES (?, ?, ?, ?)",
                     [(r["item_id"], r["order_date"], r["status"], r["amount"]) for r in fixture["rows"]])
    results = []
    for spec in fixture["cases"]:
        model = small_source(source, spec)
        base = run / "regressions" / spec["id"]
        initial = evaluate_small(model, spec, config, conn, base / "two_rows")
        # Retest each data reduction. Keep only a valid source with the same error mechanism.
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM regression_items WHERE item_id = 2")
            reduced = evaluate_small(model, spec, config, conn, base / "one_row")
        finally:
            conn.execute("ROLLBACK")
        def failure_key(item):
            error = item["returned"].get("error", "")
            if "No primary entity" in error: return "missing_primary"
            if "nested" in error.lower(): return "nested_aggregation"
            if "not found" in error.lower(): return "unresolved_column"
            if not item["entity_names"]: return "missing_entity"
            return "other_error" if error else "equal" if item["returned_matches_oracle"] else "numerical_mismatch"
        kept = (initial["native_matches_oracle"] and reduced["native_matches_oracle"] and
                not initial["returned_matches_oracle"] and failure_key(initial) == failure_key(reduced))
        result = {**initial, "artifact_directory": f"regressions/{spec['id']}",
                  "reduction": {"structural": "one metric, one model, one primary entity, only time/status dimensions; source is reexecuted",
                                "attempted": "remove cancelled row", "kept": kept,
                                "initial_failure": failure_key(initial), "reduced_failure": failure_key(reduced),
                                "selected": "one_row" if kept else "two_rows"}}
        write_json(base / "result.json", result)
        results.append(result)
    return results


def microsoft_probe(document, run):
    from ossie_microsoft import convert_ossie_to_semantic_model, convert_semantic_model_to_ossie
    folder = run / "microsoft-probes"
    folder.mkdir(exist_ok=False)
    with warnings.catch_warnings(record=True) as warnings_seen:
        warnings.simplefilter("always")
        bim = convert_ossie_to_semantic_model(document.to_ossie_yaml())
    measures = {m["name"]: m for t in bim["model"]["tables"] for m in t.get("measures", [])}
    original = {m.name: m.model_dump(mode="json") for m in document.metrics or []}
    matrix = []
    for name, measure in sorted(measures.items()):
        placeholder = measure["expression"] == "BLANK()"
        cause = ("CONVERTER_DEFECT" if name == "order_count" and placeholder else
                 "EXPECTED_LOSS" if placeholder else "UNKNOWN")
        matrix.append({"metric": name, "source": original.get(name), "dax": measure["expression"],
                       "annotations": measure.get("annotations", []), "classification": cause,
                       "warnings": [str(w.message) for w in warnings_seen if f"'{name}'" in str(w.message)],
                       "execution_status": "NOT_EXECUTED"})
    # Fully authored TMSL probe, including a calculated measure-only host (#444/#564).
    tmsl = {"name": "static_probe", "compatibilityLevel": 1600, "model": {"tables": [
        {"name": "Fact", "columns": [{"name": "id", "dataType": "int64", "sourceColumn": "id"},
                                       {"name": "amount", "dataType": "decimal", "sourceColumn": "amount"}],
         "partitions": [{"name": "Fact", "mode": "directLake", "source": {"type": "entity", "entityName": "Fact", "schemaName": "main", "expressionSource": "DatabaseQuery"}}]},
        {"name": "Dim", "columns": [{"name": "id", "dataType": "int64", "sourceColumn": "id"}],
         "partitions": [{"name": "Dim", "mode": "directLake", "source": {"type": "entity", "entityName": "Dim", "schemaName": "main", "expressionSource": "DatabaseQuery"}}]},
        {"name": "Measures", "columns": [{"name": "_", "dataType": "int64", "type": "calculatedTableColumn", "isHidden": True}],
         "partitions": [{"name": "Measures", "mode": "import", "source": {"type": "calculated", "expression": 'ROW("_", 1)'}}],
         "measures": [{"name": "Revenue", "expression": "SUM(Fact[amount])", "formatString": "0.00", "displayFolder": "Finance"},
                      {"name": "DoubleRevenue", "expression": "[Revenue] * 2", "isHidden": True}]}],
         "relationships": [{"name": "fact_dim", "fromTable": "Fact", "fromColumn": "id", "toTable": "Dim", "toColumn": "id",
                            "fromCardinality": "many", "toCardinality": "one", "crossFilteringBehavior": "oneDirection", "isActive": True}]}}
    with warnings.catch_warnings(record=True) as probe_warnings:
        warnings.simplefilter("always")
        ossie = convert_semantic_model_to_ossie(tmsl)
        returned = convert_ossie_to_semantic_model(ossie)
    write_json(folder / "source.bim", tmsl)
    (folder / "converted.yml").write_text(ossie, encoding="utf-8")
    write_json(folder / "returned.bim", returned)
    returned_measures = {m["name"]: {"table": t["name"], **m} for t in returned["model"]["tables"] for m in t.get("measures", [])}
    import yaml
    imported = yaml.safe_load(ossie)
    result = {"matrix": matrix, "relationship_source": [r.model_dump(mode="json", by_alias=True) for r in document.relationships or []],
              "relationship_target": bim["model"].get("relationships", []),
              "warnings": [str(w.message) for w in warnings_seen], "execution_status": "NOT_EXECUTED",
              "dax_probe": {"imported_metric_names": sorted(m["name"] for m in imported.get("metrics", [])),
                            "returned_measures": returned_measures,
                            "relationships": returned["model"].get("relationships", []),
                            "warnings": [str(w.message) for w in probe_warnings], "runtime": "NOT_EXECUTED"}}
    write_json(folder / "result.json", result)
    return result
