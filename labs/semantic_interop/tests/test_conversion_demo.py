"""Independent business assertions for the additive conversion teaching fixture."""

from datetime import date, datetime
from decimal import Decimal
import hashlib
import json

import duckdb
import jsonschema
import pytest

from semantic_lab.conversion_demo import METRICS, load_data, oracle, run_demo
from semantic_lab.fixtures import LAB
from semantic_lab.compare import compare


@pytest.fixture
def connection():
    with duckdb.connect() as conn:
        load_data(conn)
        yield conn


def scalar(conn, metric, **kwargs):
    columns, rows, sql = oracle(conn, metric, **kwargs)
    assert sql.strip() and len(columns) == 1 and len(rows) == 1
    return rows[0][0]


def keyed(conn, metric, grain):
    _columns, rows, _sql = oracle(conn, metric, grain=grain)
    return {tuple(str(value)[:10] for value in row[:-1]): row[-1] for row in rows}


def test_canonical_data_has_six_distinct_eligible_ordering_sessions(connection):
    assert connection.execute("SELECT count(*) FROM raw_sessions").fetchone()[0] == 12
    assert connection.execute("SELECT count(*) FROM raw_orders").fetchone()[0] == 6
    assert connection.execute("""
        SELECT count(*), count(DISTINCT o.session_id), bool_and(s.is_eligible)
        FROM raw_orders o LEFT JOIN raw_sessions s USING (session_id)
    """).fetchone() == (6, 6, True)
    assert connection.execute("""
        SELECT count(*) FROM raw_orders o
        LEFT JOIN raw_sessions s USING (session_id) WHERE s.session_id IS NULL
    """).fetchone()[0] == 0
    assert connection.execute("""
        SELECT sales_channel, count(*) FROM raw_sessions WHERE is_eligible
        GROUP BY sales_channel ORDER BY sales_channel
    """).fetchall() == [("Direct", 6), ("Indirect", 4)]


def test_all_core_oracles_have_explicit_overall_business_answers(connection):
    expected = {
        "eligible_sessions": 10,
        "converted_sessions": 4,
        "conversion_rate": 0.4,
        "paid_revenue": Decimal("390.00"),
        "paid_order_count": 4,
        "average_paid_order_value": 97.5,
    }
    assert set(METRICS) == set(expected)
    for metric, value in expected.items():
        actual = scalar(connection, metric)
        if isinstance(value, float):
            assert actual == pytest.approx(value)
        else:
            assert actual == value


@pytest.mark.parametrize("metric,expected", [
    ("eligible_sessions", {("Direct",): 6, ("Indirect",): 4}),
    ("converted_sessions", {("Direct",): 3, ("Indirect",): 1}),
    ("conversion_rate", {("Direct",): 0.5, ("Indirect",): 0.25}),
    ("paid_revenue", {("Direct",): Decimal("300"), ("Indirect",): Decimal("90")}),
    ("paid_order_count", {("Direct",): 3, ("Indirect",): 1}),
    ("average_paid_order_value", {("Direct",): 100.0, ("Indirect",): 90.0}),
])
def test_channel_oracle_contract(connection, metric, expected):
    actual = keyed(connection, metric, "channel")
    assert actual == pytest.approx(expected)


@pytest.mark.parametrize("metric,expected", [
    ("eligible_sessions", {("2026-01-01",): 5, ("2026-02-01",): 5}),
    ("converted_sessions", {("2026-01-01",): 3, ("2026-02-01",): 1}),
    ("conversion_rate", {("2026-01-01",): 0.6, ("2026-02-01",): 0.2}),
    ("paid_revenue", {("2026-01-01",): Decimal("270"), ("2026-02-01",): Decimal("120")}),
    ("paid_order_count", {("2026-01-01",): 3, ("2026-02-01",): 1}),
    ("average_paid_order_value", {("2026-01-01",): 90.0, ("2026-02-01",): 120.0}),
])
def test_cohort_month_oracle_contract(connection, metric, expected):
    assert keyed(connection, metric, "month") == pytest.approx(expected)


@pytest.mark.parametrize("metric,values", [
    ("eligible_sessions", (3, 2, 3, 2)),
    ("converted_sessions", (2, 1, 1, 0)),
    ("conversion_rate", (2 / 3, 0.5, 1 / 3, 0.0)),
    ("paid_revenue", (Decimal("180"), Decimal("90"), Decimal("120"), None)),
    ("paid_order_count", (2, 1, 1, 0)),
    ("average_paid_order_value", (90.0, 90.0, 120.0, None)),
])
def test_channel_month_oracles_preserve_nonconverting_cohorts(connection, metric, values):
    expected = dict(zip([
        ("2026-01-01", "Direct"), ("2026-01-01", "Indirect"),
        ("2026-02-01", "Direct"), ("2026-02-01", "Indirect"),
    ], values))
    actual = keyed(connection, metric, "month_channel")
    # Column order is a presentation choice; the dimensional tuple is not.
    normalized = {tuple(sorted(key)): value for key, value in actual.items()}
    assert normalized == pytest.approx({tuple(sorted(key)): value for key, value in expected.items()})


def test_cohort_revenue_and_transaction_revenue_are_distinct(connection):
    transactions = connection.execute("""
        SELECT date_trunc('month', o.order_date)::DATE, sum(o.amount_eur)
        FROM raw_orders o JOIN raw_sessions s USING (session_id)
        WHERE s.is_eligible AND o.order_status = 'paid'
          AND o.order_date < DATE '2026-03-01'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    assert transactions == [(date(2026, 1, 1), Decimal("190")), (date(2026, 2, 1), Decimal("200"))]
    assert keyed(connection, "paid_revenue", "month") == {
        ("2026-01-01",): Decimal("270"), ("2026-02-01",): Decimal("120")}


def test_second_paid_order_in_same_session_changes_orders_not_conversions():
    with duckdb.connect() as conn:
        load_data(conn, edge=True)
        assert scalar(conn, "eligible_sessions") == 10
        assert scalar(conn, "converted_sessions") == 4
        assert scalar(conn, "conversion_rate") == pytest.approx(0.4)
        assert scalar(conn, "paid_order_count") == 5
        assert scalar(conn, "paid_revenue") == Decimal("420")
        assert scalar(conn, "average_paid_order_value") == pytest.approx(84.0)


def test_observation_cutoff_excludes_later_transactions(connection):
    connection.execute("""
        INSERT INTO raw_orders VALUES (99, 3, DATE '2026-03-01', 1000, 'paid')
    """)
    assert scalar(connection, "converted_sessions") == 4
    assert scalar(connection, "paid_order_count") == 4
    assert scalar(connection, "paid_revenue") == Decimal("390")


def test_orders_from_excluded_sessions_do_not_qualify(connection):
    connection.execute("""
        INSERT INTO raw_orders VALUES (99, 11, DATE '2026-01-09', 1000, 'paid')
    """)
    assert scalar(connection, "eligible_sessions") == 10
    assert scalar(connection, "converted_sessions") == 4
    assert scalar(connection, "paid_order_count") == 4
    assert scalar(connection, "paid_revenue") == Decimal("390")


def test_zero_denominator_is_null_and_zero_conversion_is_zero(connection):
    empty = "s.session_date < DATE '2020-01-01'"
    assert scalar(connection, "conversion_rate", cohort_filter=empty) is None
    assert scalar(connection, "average_paid_order_value", cohort_filter=empty) is None
    assert scalar(connection, "eligible_sessions", cohort_filter=empty) == 0
    assert scalar(connection, "converted_sessions", cohort_filter=empty) == 0
    no_conversion = "s.session_id = 10"
    assert scalar(connection, "eligible_sessions", cohort_filter=no_conversion) == 1
    assert scalar(connection, "converted_sessions", cohort_filter=no_conversion) == 0
    assert scalar(connection, "conversion_rate", cohort_filter=no_conversion) == 0
    assert scalar(connection, "average_paid_order_value", cohort_filter=no_conversion) is None


def test_teaching_failures_have_independently_derived_wrong_answers(connection):
    wrong_denominator = connection.execute("""
        SELECT 1.0 * (SELECT count(DISTINCT o.session_id)
                      FROM raw_orders o JOIN raw_sessions s USING (session_id)
                      WHERE s.is_eligible AND o.order_status = 'paid') / count(*)
        FROM raw_sessions
    """).fetchone()[0]
    all_statuses = connection.execute("""
        SELECT 1.0 * count(DISTINCT o.session_id) /
               (SELECT count(*) FROM raw_sessions WHERE is_eligible)
        FROM raw_orders o JOIN raw_sessions s USING (session_id) WHERE s.is_eligible
    """).fetchone()[0]
    inner_join = connection.execute("""
        SELECT 1.0 * count(DISTINCT CASE WHEN o.order_status='paid' THEN s.session_id END)
             / count(DISTINCT s.session_id)
        FROM raw_sessions s JOIN raw_orders o USING (session_id) WHERE s.is_eligible
    """).fetchone()[0]
    assert wrong_denominator == pytest.approx(4 / 12)
    assert all_statuses == pytest.approx(6 / 10)
    assert inner_join == pytest.approx(4 / 6)
    channel_rates = keyed(connection, "conversion_rate", "channel")
    assert sum(channel_rates.values()) / len(channel_rates) == pytest.approx(0.375)
    assert scalar(connection, "conversion_rate") == pytest.approx(0.4)
    assert all(value != pytest.approx(0.4) for value in channel_rates.values())


@pytest.fixture(scope="module")
def demonstration():
    return run_demo()


def test_native_cases_meet_the_stage_a_acceptance_gate(demonstration):
    run, report = demonstration
    assert report["stage_a"]["accepted"], report["stage_a"]
    expected_ids = {f"{metric}__{grain}" for metric in METRICS
                    for grain in ("overall", "channel", "month", "month_channel")}
    cases = {case["case_id"]: case for case in report["cases"]}
    assert expected_ids <= cases.keys()
    required = {
        "case_id", "business_question", "source_platform", "target_platform",
        "conversion_path", "execution_status", "semantic_status", "oracle_result",
        "target_result", "comparison_result", "evidence_reference", "provenance",
    }
    for case_id in expected_ids:
        case = cases[case_id]
        assert required <= case.keys()
        assert case["execution_status"] == "EXECUTED", case
        assert case["comparison_result"]["equal"], case
        assert case["provenance"] == "MetricFlow-generated SQL", case
        assert f"native/{case_id}.sql" in case["evidence_reference"]
        sql = run / "native" / f"{case_id}.sql"
        assert sql.is_file() and sql.read_text().strip()
    for relative, expected_hash in report["artifact_sha256"].items():
        assert hashlib.sha256((run / relative).read_bytes()).hexdigest() == expected_hash


def test_native_empty_and_nonconverting_cases_are_not_omitted(demonstration):
    _run, report = demonstration
    cases = {case["case_id"]: case for case in report["cases"]}
    for case_id in {f"{metric}__empty" for metric in METRICS} | {"conversion_rate__nonconverting"}:
        assert cases[case_id]["execution_status"] == "EXECUTED", cases[case_id]
        assert cases[case_id]["comparison_result"]["equal"], cases[case_id]


def test_native_multiple_orders_keep_session_conversion_grain():
    _run, report = run_demo(edge=True)
    assert report["edge_case"] is True
    assert report["stage_a"]["accepted"], report["stage_a"]
    assert report["stage_a"]["native_matches"] == report["stage_a"]["native_cases"] == 31


def test_native_generated_sql_retains_cutoff_and_exclusion_filters(demonstration):
    run, report = demonstration
    assert report["stage_a"]["accepted"], report["stage_a"]
    expected = {
        "eligible_sessions": 10, "converted_sessions": 4, "conversion_rate": 0.4,
        "paid_revenue": Decimal("390"), "paid_order_count": 4,
        "average_paid_order_value": 97.5,
    }
    # Source models are views. Re-execute the unchanged official compiler artifacts
    # after raw-data mutations; no handwritten SQL serves as the target query.
    with duckdb.connect(str(run / "dbt" / "benchmark.duckdb")) as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("""
                INSERT INTO raw_orders VALUES
                (99, 3, DATE '2026-03-01', 1000, 'paid'),
                (100, 11, DATE '2026-01-09', 1000, 'paid')
            """)
            for metric, value in expected.items():
                sql = (run / "native" / f"{metric}__overall.sql").read_text()
                rows = conn.execute(sql).fetchall()
                assert len(rows) == 1 and len(rows[0]) == 1
                assert rows[0][0] == pytest.approx(value)
            for metric in METRICS:
                for grain in ("overall", "channel", "month", "month_channel"):
                    sql = (run / "native" / f"{metric}__{grain}.sql").read_text()
                    cursor = conn.execute(sql)
                    aliases = {"metric_time__month": "session_month",
                               "session__sales_channel": "sales_channel"}
                    columns = [aliases.get(column[0], column[0]) for column in cursor.description]
                    rows = [tuple(value.date() if columns[index] == "session_month" and isinstance(value, datetime)
                                  else value for index, value in enumerate(row))
                            for row in cursor.fetchall()]
                    expected_columns, expected_rows, _sql = oracle(conn, metric, grain)
                    assert compare(expected_columns, expected_rows, columns, rows,
                                   [column for column in expected_columns if column != metric])["equal"], (metric, grain)
        finally:
            conn.execute("ROLLBACK")


def test_all_five_injected_failures_are_executed_and_detected(demonstration):
    _run, report = demonstration
    faults = {case["case_id"]: case for case in report["controlled_failures"]}
    assert set(faults) == {
        "wrong_denominator", "incorrect_conversion", "average_of_rates",
        "removed_channel_filter", "inner_join",
    }
    for case in faults.values():
        assert case["execution_status"] == "EXECUTED", case
        assert case["comparison_result"]["equal"] is False, case
        assert case["semantic_status"] == "INJECTED_FAILURE", case
        assert case["provenance"] == "Injected teaching SQL; not converter output", case
    for case_id, value in {
        "wrong_denominator": 4 / 12, "incorrect_conversion": 6 / 10,
        "average_of_rates": 0.375, "inner_join": 4 / 6,
    }.items():
        observed = faults[case_id]["target_result"]["rows"]
        assert len(observed) == 1 and len(observed[0]) == 1
        assert float(observed[0][0]["value"]) == pytest.approx(value)
    lost_channel = {row[0]["value"]: float(row[1]["value"])
                    for row in faults["removed_channel_filter"]["target_result"]["rows"]}
    assert lost_channel == {"Direct": 0.4, "Indirect": 0.4}


def test_shared_comparison_schema_cannot_turn_missing_execution_into_a_match(demonstration):
    _run, report = demonstration
    schema = json.loads((LAB / "docs/conversion-result.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    for case in report["cases"] + report["controlled_failures"]:
        validator.validate(case)
    unexecuted = dict(report["cases"][0], execution_status="NOT_EXECUTED")
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(unexecuted)
