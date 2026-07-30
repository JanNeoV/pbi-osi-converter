"""Shared deterministic comparison primitives for hash-bound result evidence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping


class ResultEvidenceComparisonError(ValueError):
    """Result evidence is malformed or violates the comparison contract."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def values_match(
    value_type: str,
    left: int | float | None,
    right: int | float | None,
) -> bool:
    """Apply the repository's exact integer and dual-1e-9 decimal policy."""

    normalized = value_type.upper()
    if normalized not in {"INTEGER", "DECIMAL"}:
        raise ResultEvidenceComparisonError(
            "result evidence value_type must be INTEGER or DECIMAL."
        )
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        raise ResultEvidenceComparisonError(
            "Runtime result evidence values cannot be Boolean."
        )
    if isinstance(left, int) and isinstance(right, int):
        return left == right
    if normalized == "INTEGER":
        raise ResultEvidenceComparisonError(
            "INTEGER result evidence values must be integers."
        )
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        raise ResultEvidenceComparisonError(
            "DECIMAL result evidence values must be numeric."
        )
    if not math.isfinite(float(left)) or not math.isfinite(float(right)):
        raise ResultEvidenceComparisonError(
            "Runtime result evidence values must be finite."
        )
    return math.isclose(
        float(left),
        float(right),
        abs_tol=1e-9,
        rel_tol=1e-9,
    )


@dataclass(frozen=True)
class ResultComparison:
    status: str
    mismatch_coordinates: tuple[dict[str, Any], ...]


def _endpoint_rows(
    endpoint: Mapping[str, Any],
    *,
    label: str,
    result_hash_mode: str,
) -> tuple[bool, dict[bytes, int | float | None], dict[bytes, dict[str, Any]]]:
    if set(endpoint) != {"status", "complete", "result_sha256", "rows"}:
        raise ResultEvidenceComparisonError(
            f"{label} must contain exactly status, complete, result_sha256, and rows."
        )
    status = str(endpoint["status"]).upper()
    if status == "NOT_AVAILABLE":
        if (
            endpoint["complete"] is not False
            or endpoint["result_sha256"] is not None
            or endpoint["rows"] != []
        ):
            raise ResultEvidenceComparisonError(
                f"{label} NOT_AVAILABLE evidence must have no rows or result hash."
            )
        return False, {}, {}
    if status != "AVAILABLE" or endpoint["complete"] is not True:
        raise ResultEvidenceComparisonError(
            f"{label} AVAILABLE evidence must be complete."
        )
    rows = endpoint["rows"]
    if not isinstance(rows, list) or not rows:
        raise ResultEvidenceComparisonError(
            f"{label} AVAILABLE evidence must contain rows."
        )
    if result_hash_mode == "ROWS_ONLY":
        expected_hash = sha256_bytes(
            json.dumps(
                rows,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
    elif result_hash_mode == "ENDPOINT":
        expected_hash = sha256_bytes(
            canonical_json_bytes(
                {
                    "complete": endpoint["complete"],
                    "rows": rows,
                    "status": endpoint["status"],
                }
            )
        )
    else:
        raise ResultEvidenceComparisonError("Unknown result hash mode.")
    if endpoint["result_sha256"] != expected_hash:
        raise ResultEvidenceComparisonError(
            f"{label} result_sha256 does not match its canonical rows."
        )
    values: dict[bytes, int | float | None] = {}
    coordinates: dict[bytes, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != {"coordinates", "value"}:
            raise ResultEvidenceComparisonError(
                f"{label}.rows[{index}] must contain coordinates and value."
            )
        raw_coordinates = row["coordinates"]
        if not isinstance(raw_coordinates, Mapping):
            raise ResultEvidenceComparisonError(
                f"{label}.rows[{index}] coordinates must be an object."
            )
        coordinate_value = dict(raw_coordinates)
        identity = canonical_json_bytes(coordinate_value)
        if identity in values:
            raise ResultEvidenceComparisonError(
                f"{label} contains duplicate coordinates."
            )
        value = row["value"]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ResultEvidenceComparisonError(
                f"{label}.rows[{index}] value must be numeric or null."
            )
        values[identity] = value
        coordinates[identity] = coordinate_value
    return True, values, coordinates


def compare_result_record(
    record: Mapping[str, Any],
    *,
    result_hash_mode: str = "ENDPOINT",
) -> ResultComparison:
    """Compare one Power BI/Snowflake result record without retaining values."""

    if set(record) != {
        "source_measure",
        "slice",
        "value_type",
        "power_bi",
        "snowflake",
    }:
        raise ResultEvidenceComparisonError("Result record has invalid fields.")
    pbi_available, pbi_rows, pbi_coordinates = _endpoint_rows(
        record["power_bi"],
        label="power_bi",
        result_hash_mode=result_hash_mode,
    )
    snow_available, snow_rows, snow_coordinates = _endpoint_rows(
        record["snowflake"],
        label="snowflake",
        result_hash_mode=result_hash_mode,
    )
    if not pbi_available or not snow_available:
        return ResultComparison("NOT_AVAILABLE", ())

    identities = sorted(set(pbi_rows) | set(snow_rows))
    mismatches: list[dict[str, Any]] = []
    for identity in identities:
        coordinate = pbi_coordinates.get(identity) or snow_coordinates.get(identity) or {}
        if identity not in pbi_rows or identity not in snow_rows:
            mismatches.append(coordinate)
            continue
        if not values_match(
            str(record["value_type"]),
            pbi_rows[identity],
            snow_rows[identity],
        ):
            mismatches.append(coordinate)
    return ResultComparison(
        "FAILED" if mismatches else "PASSED",
        tuple(mismatches),
    )


__all__ = [
    "ResultComparison",
    "ResultEvidenceComparisonError",
    "canonical_json_bytes",
    "compare_result_record",
    "sha256_bytes",
    "values_match",
]
