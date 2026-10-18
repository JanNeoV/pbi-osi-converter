"""Typed, order-independent multiset comparisons; never coerce NULL to zero."""
from datetime import date, datetime
from decimal import Decimal
import json
import math


def cell(value):
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": repr(value)}
    if isinstance(value, (datetime, date)):
        return {"type": "datetime" if isinstance(value, datetime) else "date", "value": value.isoformat()}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    raise TypeError(f"Unsupported result type: {type(value).__name__}")


def table(columns, rows):
    encoded = [[cell(v) for v in row] for row in rows]
    return {"columns": columns, "rows": sorted(encoded, key=lambda r: json.dumps(r, sort_keys=True))}


def difference(expected, observed):
    numeric = (int, Decimal, float)
    if isinstance(expected, numeric) and isinstance(observed, numeric) and not isinstance(expected, bool) and not isinstance(observed, bool):
        if isinstance(expected, (int, Decimal)) and isinstance(observed, float):
            return False, {"absolute": None, "relative": None, "reason": "exact_numeric_became_float"}
        if isinstance(expected, float) or isinstance(observed, float):
            if not math.isfinite(float(expected)) or not math.isfinite(float(observed)):
                return False, {"reason": "non_finite"}
            absolute = abs(float(expected) - float(observed))
            equal = absolute <= 1e-9 + 1e-9 * abs(float(expected))
            return equal, {"absolute": str(absolute), "relative": None if expected == 0 else str(absolute / abs(float(expected)))}
        absolute = abs(Decimal(expected) - Decimal(observed))
        return absolute == 0, {"absolute": str(absolute), "relative": None if expected == 0 else str(absolute / abs(Decimal(expected)))}
    return type(expected) is type(observed) and expected == observed, {"absolute": None, "relative": None}


def compare(expected_columns, expected_rows, observed_columns, observed_rows, keys=()):
    if len(set(observed_columns)) != len(observed_columns) or set(expected_columns) != set(observed_columns):
        return {"equal": False, "diagnostics": ["column_set_mismatch"], "differences": []}
    positions = [observed_columns.index(c) for c in expected_columns]
    actual = [tuple(row[i] for i in positions) for row in observed_rows]
    key_positions = [expected_columns.index(k) for k in keys]
    # Find a maximum one-to-one matching. Greedy pairing can misclassify overlapping float tolerances.
    edges = [[j for j, other in enumerate(actual) if all(difference(a, b)[0] for a, b in zip(row, other))]
             for row in expected_rows]
    owners = {}

    def assign(i, visited):
        for j in edges[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in owners or assign(owners[j], visited):
                owners[j] = i
                return True
        return False

    for i in range(len(expected_rows)):
        assign(i, set())
    matches = {i: j for j, i in owners.items()}
    pending = {j: row for j, row in enumerate(actual) if j not in owners}
    differences, diagnostics = [], []
    for i, row in enumerate(expected_rows):
        if i in matches:
            other = actual[matches[i]]
        else:
            candidates = [j for j, candidate in pending.items() if all(difference(row[p], candidate[p])[0] for p in key_positions)]
            if not candidates:
                diagnostics.append("missing_row")
                continue
            other = pending.pop(candidates[0])
        for col, a, b in zip(expected_columns, row, other):
            equal, delta = difference(a, b)
            differences.append({"column": col, "keys": [cell(row[p]) for p in key_positions], "expected": cell(a), "observed": cell(b), "equal": equal, **delta})
    if pending:
        diagnostics.extend(["unexpected_row"] * len(pending))
    return {"equal": not diagnostics and all(d["equal"] for d in differences),
            "diagnostics": sorted(diagnostics), "differences": sorted(differences, key=lambda d: json.dumps(d, sort_keys=True))}


def status(*, structural, unsupported, semantic, executed, comparison):
    if structural is False:
        return "INVALID_MODEL"
    if unsupported:
        return "UNSUPPORTED"
    if executed and comparison is not None and not comparison["equal"]:
        return "NUMERICAL_MISMATCH"
    if semantic is False:
        return "SEMANTIC_MISMATCH"
    if not executed or comparison is None or structural is not True or semantic is not True:
        return "NOT_EXECUTED"
    return "PASS"
