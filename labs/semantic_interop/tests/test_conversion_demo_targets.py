"""Bounded static checks of authored targets; no Microsoft or Databricks execution.

These tests inspect candidate text/inventory only. Target validation queries are
provided for future real engines and remain NOT_EXECUTED.
"""

import json
from pathlib import Path

import pytest
import yaml

from semantic_poc.agent.powerbi_import import extract_powerbi_inventory


ROOT = Path(__file__).resolve().parents[3]
TARGETS = ROOT / "labs/semantic_interop/fixtures/conversion_demo/targets"
POWERBI = TARGETS / "powerbi"
DATABRICKS = TARGETS / "databricks"


@pytest.fixture(scope="module")
def inventory():
    return extract_powerbi_inventory(
        POWERBI / "ConversionDemo.SemanticModel/definition", ROOT
    )


@pytest.mark.parametrize("directory", [POWERBI, DATABRICKS])
def test_authored_target_provenance_never_claims_conversion_or_runtime(directory):
    provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["artifact_origin"] == "MANUALLY_AUTHORED_CANDIDATE"
    assert provenance["conversion_execution_status"] == "NOT_EXECUTED"
    assert provenance["runtime_execution_status"] == "NOT_EXECUTED"
    assert provenance["validation_queries_execution_status"] == "NOT_EXECUTED"
    assert provenance["comparison_result"] == "NOT_EXECUTED"


def test_tmdl_inventory_has_disconnected_selector_and_one_way_filter_path(inventory):
    assert {table.name for table in inventory.tables} == {
        "Sessions", "Orders", "Sales Channel", "KPI Selector"
    }
    assert {
        (rel.from_table, rel.from_column, rel.to_table, rel.to_column)
        for rel in inventory.relationships
    } == {
        ("Sessions", "sales_channel", "Sales Channel", "sales_channel"),
        ("Orders", "session_id", "Sessions", "session_id"),
    }
    assert all(rel.is_active for rel in inventory.relationships)
    assert all(rel.from_cardinality == "many" and rel.to_cardinality == "one"
               for rel in inventory.relationships)
    assert all(rel.cross_filter_direction == "oneDirection" for rel in inventory.relationships)


def test_tmdl_inventory_keeps_session_and_order_grains_distinct(inventory):
    measures = {measure.name: measure for measure in inventory.measures}
    assert set(measures) == {
        "Eligible Sessions", "Converted Sessions", "Conversion Rate", "Paid Revenue",
        "Paid Order Count", "Average Paid Order Value", "Selected KPI"
    }
    assert "DISTINCTCOUNT(Sessions[session_id])" in measures["Eligible Sessions"].expression
    assert "Orders[" not in measures["Eligible Sessions"].expression
    assert "DISTINCTCOUNT(Orders[session_id])" in measures["Converted Sessions"].expression
    assert "DISTINCTCOUNT(Orders[order_id])" in measures["Paid Order Count"].expression
    assert measures["Conversion Rate"].expression == "DIVIDE([Converted Sessions], [Eligible Sessions])"
    assert measures["Average Paid Order Value"].expression == "DIVIDE([Paid Revenue], [Paid Order Count])"
    for name in ("Converted Sessions", "Paid Order Count", "Paid Revenue"):
        expression = measures[name].expression
        assert 'KEEPFILTERS(Orders[order_status] = "paid")' in expression
        assert "KEEPFILTERS(Sessions[is_eligible] = TRUE())" in expression
        assert "Orders[order_date] < DATE(2026, 3, 1)" in expression
        assert "Sessions[session_date] < DATE(2026, 3, 1)" in expression
        assert "Sessions[session_date] >= DATE(2026, 1, 1)" in expression
    assert "COALESCE" not in measures["Paid Revenue"].expression


def test_dynamic_selector_is_separate_numeric_presentation_with_explicit_default(inventory):
    selector = next(measure for measure in inventory.measures if measure.name == "Selected KPI")
    assert selector.display_folder == "Power BI Presentation"
    assert 'SELECTEDVALUE(\'KPI Selector\'[KPI], "Conversion Rate")' in selector.expression
    assert "SWITCH(" in selector.expression
    assert "FORMAT(" not in selector.expression
    assert selector.dynamic_format_string is not None
    # The existing inventory deliberately marks rather than evaluates dynamic formats.
    source = (POWERBI / "ConversionDemo.SemanticModel/definition/tables/Sessions.tmdl").read_text(encoding="utf-8")
    dynamic_format = source.split("formatStringDefinition =", 1)[1].split("displayFolder:", 1)[0]
    assert 'SELECTEDVALUE(\'KPI Selector\'[KPI], "Conversion Rate")' in dynamic_format
    assert '"0.0%"' in dynamic_format
    assert '"#,0"' in dynamic_format
    database = POWERBI / "ConversionDemo.SemanticModel/definition/database.tmdl"
    assert "compatibilityLevel: 1601" in database.read_text(encoding="utf-8")


def test_dax_queries_cover_selection_and_channel_cases_without_claiming_execution():
    queries = (POWERBI / "validate.dax").read_text(encoding="utf-8")
    assert "Execution: NOT_EXECUTED" in queries
    for case_id in (
        "selector_none", "selector_single_rate", "selector_single_revenue",
        "selector_single_sessions", "selector_multiple", "selector_direct_default",
        "selector_indirect_revenue", "selector_direct_multiple", "empty_cohort", "zero_conversion"
    ):
        assert f'"{case_id}"' in queries


def test_metric_view_yaml_preserves_sessions_and_uses_native_aggregate_ratios():
    candidate = yaml.safe_load((DATABRICKS / "metric_view.yaml").read_text(encoding="utf-8"))
    assert candidate["version"] == "1.1"
    assert candidate["filter"] == "is_eligible = true"
    source = candidate["source"]
    assert "FROM demo.semantic_interop.sessions AS s" in source
    assert "LEFT JOIN demo.semantic_interop.orders AS o" in source
    on_clause, where_clause = source.split("WHERE")
    assert "o.order_date < DATE '2026-03-01'" in on_clause
    assert "s.session_date < DATE '2026-03-01'" in where_clause
    assert "s.session_date >= DATE '2026-01-01'" in where_clause
    assert "o." not in where_clause
    dimensions = {dimension["name"]: dimension["expr"] for dimension in candidate["dimensions"]}
    assert dimensions == {"sales_channel": "sales_channel", "session_month": "CAST(DATE_TRUNC('MONTH', session_date) AS DATE)"}
    measures = {measure["name"]: measure["expr"] for measure in candidate["measures"]}
    assert measures["eligible_sessions"] == "COUNT(DISTINCT session_id)"
    assert measures["converted_sessions"] == "COUNT(DISTINCT session_id) FILTER (WHERE order_status = 'paid')"
    assert measures["paid_order_count"] == "COUNT(DISTINCT order_id) FILTER (WHERE order_status = 'paid')"
    assert "NULLIF(COUNT(DISTINCT session_id), 0)" in measures["conversion_rate"]
    assert "AVG(" not in measures["conversion_rate"].upper()
    assert "NULLIF(COUNT(DISTINCT order_id)" in measures["average_paid_order_value"]
    assert "COALESCE" not in measures["paid_revenue"]


def test_target_query_files_require_real_engines_and_document_empty_and_cohort_slices():
    sql = (DATABRICKS / "validate.sql").read_text(encoding="utf-8")
    assert "Execution: NOT_EXECUTED" in sql
    assert "MEASURE(conversion_rate)" in sql
    assert "GROUP BY session_month, sales_channel" in sql
    assert "session_month < DATE '2020-01-01'" in sql
    assert "session_month = DATE '2026-02-01' AND sales_channel = 'Indirect'" in sql
