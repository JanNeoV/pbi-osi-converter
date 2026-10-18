from datetime import date, datetime
from decimal import Decimal
import pytest

from semantic_lab.compare import cell, compare, difference, status
from semantic_lab.fixtures import CASES
from semantic_lab.runner import normalized_rows


def test_money_is_exact_and_json_is_lossless():
    value = Decimal("9999999999999999.99")
    assert cell(value) == {"type": "decimal", "value": "9999999999999999.99"}
    assert not difference(value, value - Decimal("0.01"))[0]
    assert not difference(Decimal("1.00"), 1.0)[0]
    assert difference(Decimal("1.00"), Decimal("1.000"))[0]


@pytest.mark.parametrize("a,b", [(None, 0), (0, None), (False, 0), ("1", 1), (date(2026, 1, 1), datetime(2026, 1, 1))])
def test_distinct_types_do_not_collapse(a, b):
    assert not difference(a, b)[0]


def test_float_tolerance_and_zero_relative_difference():
    assert difference(1.0, 1.0 + 1e-10)[0]
    assert not difference(1.0, 1.0 + 1e-6)[0]
    assert difference(0.0, 1e-10)[1]["relative"] is None
    assert not difference(float("nan"), float("nan"))[0]
    assert not difference(float("inf"), float("inf"))[0]


def test_multisets_retain_duplicates_and_ignore_row_and_column_order():
    columns, rows = ["category", "revenue"], [("A", Decimal("2")), ("A", Decimal("2")), (None, Decimal("3"))]
    reordered = [(Decimal("3"), None), (Decimal("2"), "A"), (Decimal("2"), "A")]
    assert compare(columns, rows, ["revenue", "category"], reordered, ("category",))["equal"]
    assert not compare(columns, rows, columns, rows[:-1], ("category",))["equal"]
    assert not compare(columns, rows[:-1], columns, rows, ("category",))["equal"]
    assert not compare(columns, rows, ["category", "wrong"], rows)["equal"]
    assert not compare(columns, rows, ["category", "category"], rows)["equal"]
    assert compare(columns, rows, columns, list(reversed(rows)), ("category",)) == compare(columns, list(reversed(rows)), columns, rows, ("category",))


def test_dimension_keys_cannot_be_reassigned_to_make_totals_match():
    assert not compare(["k", "v"], [("a", 2), ("b", 3)], ["k", "v"], [("a", 3), ("b", 2)], ("k",))["equal"]
    assert not compare(["v"], [(None,)], ["v"], [])["equal"]


def test_overlapping_tolerances_use_one_to_one_matching():
    assert compare(["v"], [(1e-9,), (0.0,)], ["v"], [(0.0,), (2e-9,)])["equal"]


def test_time_normalization_is_limited_to_declared_midnight_date():
    case = next(c for c in CASES if c.id == "revenue_over_time")
    assert normalized_rows(case, ["metric_time__day", "revenue"], [(datetime(2026, 1, 1), 1)])[1] == [(date(2026, 1, 1), 1)]
    with pytest.raises(ValueError):
        normalized_rows(case, ["metric_time__day"], [(datetime(2026, 1, 1, 12),)])


@pytest.mark.parametrize("override", [
    {"executed": False}, {"unsupported": True}, {"structural": False},
    {"structural": None}, {"semantic": None}, {"semantic": False},
    {"comparison": None}, {"comparison": {"equal": False}},
])
def test_missing_or_negative_evidence_never_passes(override):
    kwargs = dict(structural=True, unsupported=False, semantic=True, executed=True, comparison={"equal": True})
    kwargs.update(override)
    assert status(**kwargs) != "PASS"
