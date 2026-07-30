from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from semantic_poc.src.models import PROJECT_ROOT


REVIEW_MEMORY_SCHEMA_VERSION = 1
DEFAULT_REVIEW_MEMORY_ROOT = PROJECT_ROOT / "semantic_poc" / "review_memory"
DEFAULT_REVIEW_MEMORY_REGISTRY = DEFAULT_REVIEW_MEMORY_ROOT / "registry.json"
DEFAULT_REVIEW_MEMORY_REGISTRY_SCHEMA = (
    DEFAULT_REVIEW_MEMORY_ROOT / "registry.schema.json"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{2,127}$")

SOURCE_PATTERN_FIELDS = {
    "pattern",
    "expression_shape",
    "aggregation",
    "source_table",
    "source_field",
    "observed_operator",
    "observed_divisor",
}
APPLICABILITY_FIELDS = {
    "match_mode",
    "source_platform",
    "source_object",
    "source_unit",
    "target_semantic_label",
    "target_unit",
    "relationship_context",
    "relationship_signature_sha256",
}
FIXTURE_SIGNATURE_FIELDS = {
    "fixture_id",
    "source_snapshot_sha256",
    "source_object_id",
    "semantic_signature_sha256",
}


class ReviewMemoryError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ReviewMemoryError(f"{field} must be non-empty text.")
    return value


def _sha(value: Any, field: str) -> str:
    text = _required_text(value, field)
    if not SHA256.fullmatch(text):
        raise ReviewMemoryError(f"{field} must be a lowercase SHA-256 value.")
    return text


def _exact_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ReviewMemoryError(f"{label} must contain exactly: {', '.join(sorted(fields))}.")
    return dict(value)


def review_match_payload(
    *,
    finding_category: str,
    source_pattern: Mapping[str, Any],
    applicability: Mapping[str, Any],
    fixture_source_signature: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "finding_category": finding_category,
        "source_pattern": dict(source_pattern),
        "applicability": dict(applicability),
        "fixture_source_signature": dict(fixture_source_signature),
    }


def review_match_signature(
    *,
    finding_category: str,
    source_pattern: Mapping[str, Any],
    applicability: Mapping[str, Any],
    fixture_source_signature: Mapping[str, Any],
) -> str:
    return sha256_value(
        review_match_payload(
            finding_category=finding_category,
            source_pattern=source_pattern,
            applicability=applicability,
            fixture_source_signature=fixture_source_signature,
        )
    )


@dataclass(frozen=True)
class ReviewRule:
    schema_version: int
    rule_id: str
    status: str
    finding_category: str
    source_pattern: Mapping[str, Any]
    applicability: Mapping[str, Any]
    expected_transformation: Mapping[str, Any]
    unsafe_observed_transformation: Mapping[str, Any]
    structured_canonical_operation: Mapping[str, Any]
    evidence_references: tuple[str, ...]
    human_rationale: str
    approval_provenance: Mapping[str, Any]
    fixture_source_signature: Mapping[str, Any]
    match_signature_sha256: str
    version: int
    supersession_status: str
    superseded_by: str | None
    registered_path: str | None = None
    registered_sha256: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewRule":
        expected = {
            "schema_version",
            "rule_id",
            "status",
            "finding_category",
            "source_pattern",
            "applicability",
            "expected_transformation",
            "unsafe_observed_transformation",
            "structured_canonical_operation",
            "evidence_references",
            "human_rationale",
            "approval_provenance",
            "fixture_source_signature",
            "match_signature_sha256",
            "version",
            "supersession_status",
            "superseded_by",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ReviewMemoryError("Accepted review-memory entry has unexpected or missing fields.")
        if value["schema_version"] != REVIEW_MEMORY_SCHEMA_VERSION:
            raise ReviewMemoryError("Unsupported review-memory schema version.")
        rule_id = _required_text(value["rule_id"], "rule_id")
        if not SAFE_ID.fullmatch(rule_id):
            raise ReviewMemoryError("rule_id must be a safe lowercase identifier.")
        if value["status"] != "ACCEPTED":
            raise ReviewMemoryError("Only ACCEPTED review-memory entries may be loaded.")
        if value["finding_category"] != "UNIT_CONVERSION_MISMATCH":
            raise ReviewMemoryError("This POC registry supports only UNIT_CONVERSION_MISMATCH.")
        source_pattern = _exact_mapping(value["source_pattern"], SOURCE_PATTERN_FIELDS, "source_pattern")
        applicability = _exact_mapping(value["applicability"], APPLICABILITY_FIELDS, "applicability")
        fixture = _exact_mapping(
            value["fixture_source_signature"], FIXTURE_SIGNATURE_FIELDS, "fixture_source_signature"
        )
        if source_pattern != {
            "pattern": "SCALED_SUM",
            "expression_shape": "DIVIDE_SUM_COLUMN_BY_LITERAL",
            "aggregation": "SUM",
            "source_table": "fact_traps",
            "source_field": "duration_seconds",
            "observed_operator": "DIVIDE",
            "observed_divisor": "60",
        }:
            raise ReviewMemoryError("The POC review rule source pattern must match the exact registered AST.")
        if applicability["match_mode"] != "EXACT":
            raise ReviewMemoryError("Review-memory match_mode must be EXACT.")
        _sha(applicability["relationship_signature_sha256"], "relationship_signature_sha256")
        _sha(fixture["source_snapshot_sha256"], "source_snapshot_sha256")
        _sha(fixture["semantic_signature_sha256"], "semantic_signature_sha256")
        expected_transformation = _exact_mapping(
            value["expected_transformation"], {"operator", "divisor"}, "expected_transformation"
        )
        unsafe_transformation = _exact_mapping(
            value["unsafe_observed_transformation"],
            {"operator", "divisor"},
            "unsafe_observed_transformation",
        )
        operation = _exact_mapping(
            value["structured_canonical_operation"],
            {"kind", "canonical_metric", "source_field", "scale_divisor"},
            "structured_canonical_operation",
        )
        if expected_transformation != {"operator": "DIVIDE", "divisor": "3600"}:
            raise ReviewMemoryError("Expected transformation must divide seconds by 3600.")
        if unsafe_transformation != {"operator": "DIVIDE", "divisor": "60"}:
            raise ReviewMemoryError("Unsafe observed transformation must divide seconds by 60.")
        if operation != {
            "kind": "USE_CANONICAL_SCALED_SUM",
            "canonical_metric": "duration_hours",
            "source_field": "duration_seconds",
            "scale_divisor": "3600",
        }:
            raise ReviewMemoryError("Structured correction does not match the accepted fixture operation.")
        evidence = value["evidence_references"]
        if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence) or not evidence:
            raise ReviewMemoryError("evidence_references must contain at least one path or finding reference.")
        evidence_values = tuple(_required_text(item, "evidence_references") for item in evidence)
        provenance = _exact_mapping(
            value["approval_provenance"],
            {"decision_id", "actor", "accepted_at", "decision_path", "decision_sha256"},
            "approval_provenance",
        )
        _sha(provenance["decision_sha256"], "decision_sha256")
        if value["version"] != 1 or value["supersession_status"] != "CURRENT":
            raise ReviewMemoryError("The POC rule must be version 1 and CURRENT.")
        if value["superseded_by"] is not None:
            raise ReviewMemoryError("A CURRENT review rule cannot name a superseding rule.")
        match_signature = _sha(value["match_signature_sha256"], "match_signature_sha256")
        actual_signature = review_match_signature(
            finding_category=value["finding_category"],
            source_pattern=source_pattern,
            applicability=applicability,
            fixture_source_signature=fixture,
        )
        if match_signature != actual_signature:
            raise ReviewMemoryError("match_signature_sha256 does not match the exact applicability payload.")
        return cls(
            schema_version=1,
            rule_id=rule_id,
            status="ACCEPTED",
            finding_category=value["finding_category"],
            source_pattern=source_pattern,
            applicability=applicability,
            expected_transformation=expected_transformation,
            unsafe_observed_transformation=unsafe_transformation,
            structured_canonical_operation=operation,
            evidence_references=evidence_values,
            human_rationale=_required_text(value["human_rationale"], "human_rationale"),
            approval_provenance=provenance,
            fixture_source_signature=fixture,
            match_signature_sha256=match_signature,
            version=1,
            supersession_status="CURRENT",
            superseded_by=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rule_id": self.rule_id,
            "status": self.status,
            "finding_category": self.finding_category,
            "source_pattern": dict(self.source_pattern),
            "applicability": dict(self.applicability),
            "expected_transformation": dict(self.expected_transformation),
            "unsafe_observed_transformation": dict(self.unsafe_observed_transformation),
            "structured_canonical_operation": dict(self.structured_canonical_operation),
            "evidence_references": list(self.evidence_references),
            "human_rationale": self.human_rationale,
            "approval_provenance": dict(self.approval_provenance),
            "fixture_source_signature": dict(self.fixture_source_signature),
            "match_signature_sha256": self.match_signature_sha256,
            "version": self.version,
            "supersession_status": self.supersession_status,
            "superseded_by": self.superseded_by,
        }


@dataclass(frozen=True)
class ReviewRuleV2:
    schema_version: int
    rule_id: str
    status: str
    version: int
    lifecycle: str
    superseded_by: str | None
    applicability_scope: str
    review_class: str
    finding_reason_codes: tuple[str, ...]
    applicability: Mapping[str, Any]
    structured_answer: Mapping[str, Any]
    provenance: Mapping[str, Any]
    confirmation_required: bool
    registered_path: str | None = None
    registered_sha256: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewRuleV2":
        if value.get("schema_version") != 2:
            raise ReviewMemoryError("Unsupported review-memory schema version.")
        rule_id = _required_text(value.get("rule_id"), "rule_id")
        if not SAFE_ID.fullmatch(rule_id):
            raise ReviewMemoryError("rule_id must be a safe lowercase identifier.")
        status = value.get("status")
        lifecycle = value.get("lifecycle")
        superseded_by = value.get("superseded_by")
        if lifecycle == "CURRENT" and superseded_by is not None:
            raise ReviewMemoryError("A CURRENT review rule cannot name a superseding rule.")
        if lifecycle == "SUPERSEDED" and not isinstance(superseded_by, str):
            raise ReviewMemoryError("A SUPERSEDED rule must name its successor.")
        scope = value.get("applicability_scope")
        applicability = dict(value.get("applicability") or {})
        if scope == "EXACT_OBJECT":
            if (
                not applicability.get("canonical_object_id")
                or not applicability.get("source_identifier")
                or applicability.get("parameter_role_constraints") != {}
            ):
                raise ReviewMemoryError(
                    "EXACT_OBJECT requires concrete canonical/source identity and no role constraints."
                )
        elif scope == "EXACT_TYPED_PATTERN":
            constraints = applicability.get("parameter_role_constraints")
            fixtures = (value.get("provenance") or {}).get("fixture_evidence")
            if (
                not isinstance(constraints, Mapping)
                or not constraints
                or not isinstance(fixtures, Mapping)
                or not fixtures.get("positive")
                or not fixtures.get("near_misses")
            ):
                raise ReviewMemoryError(
                    "EXACT_TYPED_PATTERN requires role constraints and reviewed positive/near-miss fixtures."
                )
        else:
            raise ReviewMemoryError("Unknown review-memory applicability scope.")
        concrete = applicability.get("semantic_signature_payload")
        pattern = applicability.get("typed_pattern_signature_payload")
        if (
            applicability.get("semantic_signature_sha256") != sha256_value(concrete)
            or applicability.get("typed_pattern_signature_sha256")
            != sha256_value(pattern)
        ):
            raise ReviewMemoryError(
                "Review-memory semantic signature hashes do not match their canonical payloads."
            )
        return cls(
            schema_version=2,
            rule_id=rule_id,
            status=str(status),
            version=int(value["version"]),
            lifecycle=str(lifecycle),
            superseded_by=superseded_by,
            applicability_scope=str(scope),
            review_class=str(value["review_class"]),
            finding_reason_codes=tuple(value["finding_reason_codes"]),
            applicability=applicability,
            structured_answer=dict(value["structured_answer"]),
            provenance=dict(value["provenance"]),
            confirmation_required=bool(value["confirmation_required"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rule_id": self.rule_id,
            "status": self.status,
            "version": self.version,
            "lifecycle": self.lifecycle,
            "superseded_by": self.superseded_by,
            "applicability_scope": self.applicability_scope,
            "review_class": self.review_class,
            "finding_reason_codes": list(self.finding_reason_codes),
            "applicability": dict(self.applicability),
            "structured_answer": dict(self.structured_answer),
            "provenance": dict(self.provenance),
            "confirmation_required": self.confirmation_required,
        }


def _load_schema(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewMemoryError(f"Review-memory schema could not be read: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ReviewMemoryError("Review-memory schema must contain a JSON object.")
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as exc:
        raise ReviewMemoryError(f"Review-memory schema is invalid: {exc.message}") from exc
    return value


def load_review_rule(
    path: str | Path,
    *,
    schema_path: str | Path | None = None,
) -> ReviewRule:
    candidate = Path(path)
    try:
        value = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ReviewMemoryError(f"Review-memory entry could not be read: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ReviewMemoryError("Review-memory entry must contain a YAML object.")
    schema_file = Path(schema_path) if schema_path is not None else DEFAULT_REVIEW_MEMORY_ROOT / "schema.json"
    validator = Draft202012Validator(_load_schema(schema_file), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda item: tuple(str(part) for part in item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ReviewMemoryError(
            f"Review-memory entry fails committed schema at {location}: {first.message}"
        )
    return ReviewRule.from_dict(value)


def load_review_rule_v2(
    path: str | Path,
    *,
    schema_path: str | Path | None = None,
) -> ReviewRuleV2:
    candidate = Path(path)
    try:
        value = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ReviewMemoryError(f"Review-memory entry could not be read: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ReviewMemoryError("Review-memory entry must contain a YAML object.")
    schema_file = (
        Path(schema_path)
        if schema_path is not None
        else DEFAULT_REVIEW_MEMORY_ROOT / "schema-v2.json"
    )
    validator = Draft202012Validator(
        _load_schema(schema_file), format_checker=FormatChecker()
    )
    errors = sorted(
        validator.iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ReviewMemoryError(
            f"Review-memory entry fails committed schema at {location}: {first.message}"
        )
    return ReviewRuleV2.from_dict(value)


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(
            os.lstat(path).st_file_attributes
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
    except (AttributeError, FileNotFoundError, OSError):
        return False


def _registry_rule_path(
    value: str,
    *,
    root: Path,
    default_registry: bool,
) -> Path:
    if "\\" in value:
        raise ReviewMemoryError("Review-memory registry paths must use POSIX separators.")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ReviewMemoryError("Review-memory registry path is not contained.")
    base = PROJECT_ROOT if default_registry else root
    candidate = base.joinpath(*pure.parts)
    base_resolved = base.resolve(strict=True)
    cursor = base
    for part in pure.parts:
        cursor = cursor / part
        if cursor.exists() and _is_link_or_reparse(cursor):
            raise ReviewMemoryError("Review-memory registry path traverses a link.")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReviewMemoryError("Registered review-memory rule is missing.") from exc
    if not resolved.is_relative_to(base_resolved) or not resolved.is_file():
        raise ReviewMemoryError("Registered review-memory rule is not a contained file.")
    return resolved


def _provenance_path(value: Any, *, label: str) -> Path:
    text = _required_text(value, label)
    if "\\" in text:
        raise ReviewMemoryError(f"{label} must use repository-relative POSIX syntax.")
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ReviewMemoryError(f"{label} is not a contained repository path.")
    root = PROJECT_ROOT.resolve(strict=True)
    candidate = PROJECT_ROOT.joinpath(*pure.parts)
    cursor = PROJECT_ROOT
    for part in pure.parts:
        cursor = cursor / part
        if cursor.exists() and _is_link_or_reparse(cursor):
            raise ReviewMemoryError(f"{label} traverses a link.")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReviewMemoryError(f"{label} is stale or missing.") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ReviewMemoryError(f"{label} is not a contained regular file.")
    return resolved


def _validate_v2_provenance(rule: ReviewRuleV2) -> None:
    provenance = rule.provenance
    decision_path = _provenance_path(
        provenance.get("decision_path"), label="Review decision provenance"
    )
    if (
        hashlib.sha256(decision_path.read_bytes()).hexdigest()
        != provenance.get("decision_sha256")
    ):
        raise ReviewMemoryError("Review decision provenance is stale.")
    references = provenance.get("evidence_references")
    if (
        isinstance(references, (str, bytes))
        or not isinstance(references, Sequence)
        or not references
    ):
        raise ReviewMemoryError("Review evidence provenance is incomplete.")
    for reference in references:
        if not isinstance(reference, Mapping):
            raise ReviewMemoryError("Review evidence provenance is malformed.")
        evidence_path = _provenance_path(
            reference.get("path"), label="Review evidence provenance"
        )
        if (
            hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            != reference.get("sha256")
        ):
            raise ReviewMemoryError("Review evidence provenance is stale.")
    fixture_evidence = provenance.get("fixture_evidence")
    if fixture_evidence is not None:
        for group in ("positive", "near_misses"):
            entries = fixture_evidence.get(group)
            if (
                isinstance(entries, (str, bytes))
                or not isinstance(entries, Sequence)
                or not entries
            ):
                raise ReviewMemoryError("Review fixture provenance is incomplete.")
            for reference in entries:
                if not isinstance(reference, Mapping):
                    raise ReviewMemoryError("Review fixture provenance is malformed.")
                fixture_path = _provenance_path(
                    reference.get("path"), label="Review fixture provenance"
                )
                if (
                    hashlib.sha256(fixture_path.read_bytes()).hexdigest()
                    != reference.get("sha256")
                ):
                    raise ReviewMemoryError("Review fixture provenance is stale.")


def _load_registry_json(path: Path, schema_path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewMemoryError(f"Review-memory registry could not be read: {exc}") from exc
    validator = Draft202012Validator(_load_schema(schema_path))
    errors = sorted(
        validator.iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ReviewMemoryError(
            f"Review-memory registry fails committed schema at {location}: {first.message}"
        )
    return value


def load_review_registry(
    root: str | Path = DEFAULT_REVIEW_MEMORY_ROOT,
) -> tuple[ReviewRule | ReviewRuleV2, ...]:
    registry_root = Path(root)
    default_registry = (
        registry_root.resolve(strict=False)
        == DEFAULT_REVIEW_MEMORY_ROOT.resolve(strict=False)
    )
    registry_path = registry_root / "registry.json"
    registry_schema = registry_root / "registry.schema.json"
    if not registry_path.is_file() or not registry_schema.is_file():
        raise ReviewMemoryError("Review-memory registry is incomplete.")
    value = _load_registry_json(registry_path, registry_schema)
    entries = value["rules"]
    if not entries:
        raise ReviewMemoryError("Review-memory registry contains no accepted rules.")
    ids = [entry["rule_id"] for entry in entries]
    paths = [entry["path"] for entry in entries]
    hashes = [entry["sha256"] for entry in entries]
    if (
        ids != sorted(ids)
        or len(ids) != len(set(ids))
        or len(paths) != len(set(paths))
        or len(hashes) != len(set(hashes))
    ):
        raise ReviewMemoryError(
            "Review-memory registry entries must be sorted and have unique IDs, paths, and hashes."
        )
    families: set[str] = set()
    rules: list[ReviewRule | ReviewRuleV2] = []
    for entry in entries:
        family = re.sub(r"_v[0-9]+$", "", entry["rule_id"])
        if family in families:
            raise ReviewMemoryError(
                "Review-memory registry contains multiple current versions of one rule."
            )
        families.add(family)
        path = _registry_rule_path(
            entry["path"], root=registry_root, default_registry=default_registry
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            raise ReviewMemoryError(
                f"Registered review-memory bytes are stale: {entry['rule_id']}"
            )
        if entry["schema_version"] == 1:
            rule: ReviewRule | ReviewRuleV2 = load_review_rule(
                path, schema_path=registry_root / "schema.json"
            )
            lifecycle = rule.supersession_status
        else:
            rule = load_review_rule_v2(
                path, schema_path=registry_root / "schema-v2.json"
            )
            _validate_v2_provenance(rule)
            lifecycle = rule.lifecycle
        if (
            rule.rule_id != entry["rule_id"]
            or rule.schema_version != entry["schema_version"]
            or rule.status != "ACCEPTED"
            or lifecycle != "CURRENT"
        ):
            raise ReviewMemoryError(
                f"Registered review-memory lifecycle conflicts: {entry['rule_id']}"
            )
        rules.append(
            replace(
                rule,
                registered_path=entry["path"],
                registered_sha256=entry["sha256"],
            )
        )
    return tuple(rules)


def _manual(reason_code: str, message: str, *, matched_rule_ids: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "status": "MANUAL_REVIEW_REQUIRED",
        "reason_code": reason_code,
        "message": message,
        "matched_rule_ids": list(matched_rule_ids),
        "human_confirmation": "REQUIRED",
        "approval_state": "NOT_REQUESTED",
        "application_state": "NOT_REQUESTED",
    }


def suggest_review_rule(context: Mapping[str, Any], rules: Sequence[ReviewRule]) -> dict[str, Any]:
    required = {
        "finding_category",
        "source_pattern",
        "applicability",
        "fixture_source_signature",
        "match_signature_sha256",
    }
    if not isinstance(context, Mapping) or set(context) != required:
        return _manual("REVIEW_CONTEXT_INCOMPLETE", "Exact review context is missing or has unknown fields.")
    try:
        source_pattern = _exact_mapping(context["source_pattern"], SOURCE_PATTERN_FIELDS, "source_pattern")
        applicability = _exact_mapping(context["applicability"], APPLICABILITY_FIELDS, "applicability")
        fixture = _exact_mapping(
            context["fixture_source_signature"], FIXTURE_SIGNATURE_FIELDS, "fixture_source_signature"
        )
    except ReviewMemoryError as exc:
        return _manual("REVIEW_CONTEXT_INCOMPLETE", str(exc))
    units = (applicability.get("source_unit"), applicability.get("target_unit"))
    if any(value is None or value == "" for value in units):
        return _manual("REVIEW_CONTEXT_UNITS_MISSING", "Source and target units are required for safe reuse.")
    if any(not isinstance(value, str) or value.upper() in {"UNKNOWN", "AMBIGUOUS"} for value in units):
        return _manual("REVIEW_CONTEXT_UNITS_AMBIGUOUS", "Source or target units are ambiguous.")
    actual = review_match_signature(
        finding_category=str(context["finding_category"]),
        source_pattern=source_pattern,
        applicability=applicability,
        fixture_source_signature=fixture,
    )
    supplied = context.get("match_signature_sha256")
    if supplied != actual:
        return _manual("REVIEW_SIGNATURE_NOT_EXACT", "The supplied fixture/source signature is not exact.")
    matches = [
        rule
        for rule in rules
        if rule.status == "ACCEPTED"
        and rule.supersession_status == "CURRENT"
        and rule.match_signature_sha256 == actual
    ]
    if not matches:
        return _manual("NO_EXACT_REVIEW_RULE", "No accepted review rule matches every applicability constraint.")
    if len(matches) != 1:
        return _manual(
            "AMBIGUOUS_REVIEW_RULE",
            "Multiple accepted review rules match the same exact context.",
            matched_rule_ids=sorted(rule.rule_id for rule in matches),
        )
    rule = matches[0]
    return {
        "status": "REVIEW_RULE_SUGGESTED",
        "matched_rule_id": rule.rule_id,
        "prior_rationale": rule.human_rationale,
        "prior_evidence": list(rule.evidence_references),
        "proposed_structured_correction": dict(rule.structured_canonical_operation),
        "human_confirmation": "REQUIRED",
        "approval_state": "NOT_REQUESTED",
        "application_state": "NOT_REQUESTED",
    }


def _signature_complete(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    required = {
        "typed_expression",
        "typed_parameter_roles",
        "source_units",
        "target_units",
        "population",
        "grain",
        "filter_context",
        "relationship_context",
    }
    if set(payload) != required:
        return False

    def invalid(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value or value.upper() in {"UNKNOWN", "AMBIGUOUS"}
        if isinstance(value, Mapping):
            return any(invalid(item) for item in value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return any(invalid(item) for item in value)
        return False

    return not any(invalid(payload[key]) for key in required)


def _v1_matches_finding(rule: ReviewRule, finding: Mapping[str, Any]) -> bool:
    if finding.get("source_identifier") != rule.applicability["source_object"]:
        return False
    payload = finding.get("semantic_signature_payload")
    if not _signature_complete(payload):
        return False
    source_table = rule.source_pattern["source_table"]
    source_field = rule.source_pattern["source_field"]
    expected = {
        "filter_context": {"behavior": "PRESERVE_EXISTING"},
        "grain": {"table": source_table},
        "population": {"kind": "NON_NULL_SOURCE_VALUES"},
        "relationship_context": {
            "mode": "ACTIVE_ONLY",
            "relationships": [],
        },
        "source_units": rule.applicability["source_unit"],
        "target_units": rule.applicability["target_unit"],
        "typed_expression": {
            "aggregation": rule.source_pattern["aggregation"],
            "column": source_field,
            "divisor": rule.source_pattern["observed_divisor"],
            "kind": rule.source_pattern["pattern"],
            "operator": rule.source_pattern["observed_operator"],
            "table": source_table,
        },
        "typed_parameter_roles": {
            "$SOURCE_FIELD": {
                "aggregation_role": "SUM_INPUT",
                "allowed_binding_constraints": [
                    "EXACT_NUMERIC_COLUMN_ROLE",
                    "EXACT_SOURCE_CONTEXT",
                ],
                "data_type": "NUMERIC",
                "semantic_role": "SOURCE_FIELD",
            },
            "$SOURCE_TABLE": {
                "aggregation_role": "SOURCE_RELATION",
                "allowed_binding_constraints": ["EXACT_SOURCE_CONTEXT"],
                "data_type": "TABLE",
                "semantic_role": "SOURCE_TABLE",
            },
        },
    }
    return payload == expected


def _bindings_satisfy_constraints(
    bindings: Any, constraints: Any
) -> bool:
    if (
        not isinstance(bindings, Mapping)
        or not isinstance(constraints, Mapping)
        or set(bindings) != set(constraints)
    ):
        return False
    serialized = [canonical_json(bindings[key]) for key in sorted(bindings)]
    if len(serialized) != len(set(serialized)):
        return False
    recognized_constraints = {
        "EXACT_CANONICAL_MAPPING",
        "EXACT_COLUMN_ROLE",
        "EXACT_DATA_TYPE",
        "EXACT_NUMERIC_COLUMN_ROLE",
        "EXACT_OPERATOR",
        "EXACT_RELATIONSHIP_TOPOLOGY",
        "EXACT_SEMANTIC_ROLE",
        "EXACT_SOURCE_CONTEXT",
        "EXACT_TYPE",
        "EXACT_UNIQUE_MEASURE_NAME",
    }
    source_context_tables: set[str] = set()
    for token, expected in constraints.items():
        if not isinstance(expected, Mapping):
            return False
        binding = bindings[token]
        if not isinstance(binding, Mapping):
            return False
        allowed = expected.get("allowed_binding_constraints")
        if (
            isinstance(allowed, (str, bytes))
            or not isinstance(allowed, Sequence)
            or not allowed
            or len(set(allowed)) != len(allowed)
            or any(item not in recognized_constraints for item in allowed)
        ):
            return False
        role = expected.get("semantic_role")
        if role == "SOURCE_FIELD" and set(binding) != {"column", "table"}:
            return False
        if role == "SOURCE_TABLE" and set(binding) != {"table"}:
            return False
        if any(not isinstance(value, str) or not value for value in binding.values()):
            return False
        if "EXACT_NUMERIC_COLUMN_ROLE" in allowed and (
            role != "SOURCE_FIELD"
            or expected.get("data_type") != "NUMERIC"
            or set(binding) != {"column", "table"}
        ):
            return False
        if "EXACT_SOURCE_CONTEXT" in allowed:
            table = binding.get("table")
            if not isinstance(table, str) or not table:
                return False
            source_context_tables.add(table)
    if len(source_context_tables) > 1:
        return False
    return True


def _v2_matches_finding(rule: ReviewRuleV2, finding: Mapping[str, Any]) -> bool:
    if (
        finding.get("review_class") != rule.review_class
        or tuple(finding.get("reason_codes") or ()) != rule.finding_reason_codes
    ):
        return False
    concrete = finding.get("semantic_signature_payload")
    pattern = finding.get("typed_pattern_signature_payload")
    if (
        not _signature_complete(concrete)
        or not _signature_complete(pattern)
        or finding.get("semantic_signature_sha256") != sha256_value(concrete)
        or finding.get("typed_pattern_signature_sha256") != sha256_value(pattern)
    ):
        return False
    applicability = rule.applicability
    if rule.applicability_scope == "EXACT_OBJECT":
        identity = finding.get("finding_identity_payload") or {}
        canonical_id = identity.get("canonical_object_id")
        return (
            canonical_id == applicability["canonical_object_id"]
            and finding.get("source_identifier")
            == applicability["source_identifier"]
            and concrete == applicability["semantic_signature_payload"]
            and finding["semantic_signature_sha256"]
            == applicability["semantic_signature_sha256"]
        )
    return (
        pattern == applicability["typed_pattern_signature_payload"]
        and finding["typed_pattern_signature_sha256"]
        == applicability["typed_pattern_signature_sha256"]
        and _bindings_satisfy_constraints(
            finding.get("concrete_role_bindings"),
            applicability["parameter_role_constraints"],
        )
    )


def suggest_review_for_finding(
    finding: Mapping[str, Any],
    rules: Sequence[ReviewRule | ReviewRuleV2],
) -> dict[str, Any]:
    concrete = finding.get("semantic_signature_payload")
    if (
        not _signature_complete(concrete)
        or finding.get("semantic_signature_sha256") != sha256_value(concrete)
    ):
        return {
            **_manual(
                "REVIEW_SIGNATURE_INCOMPLETE",
                "The Task 03 semantic signature is incomplete, ambiguous, or stale.",
            ),
            "finding_id": finding.get("finding_id"),
            "match_status": "MANUAL_REVIEW_REQUIRED",
        }
    matches: list[ReviewRule | ReviewRuleV2] = []
    for rule in rules:
        if (
            rule.status != "ACCEPTED"
            or rule.registered_sha256 is None
            or (
                isinstance(rule, ReviewRule)
                and rule.supersession_status != "CURRENT"
            )
            or (
                isinstance(rule, ReviewRuleV2)
                and rule.lifecycle != "CURRENT"
            )
        ):
            continue
        if isinstance(rule, ReviewRule):
            matched = _v1_matches_finding(rule, finding)
        else:
            matched = _v2_matches_finding(rule, finding)
        if matched:
            matches.append(rule)
    if len(matches) != 1:
        reason = "NO_EXACT_REVIEW_RULE" if not matches else "AMBIGUOUS_REVIEW_RULE"
        return {
            **_manual(
                reason,
                (
                    "No accepted review rule matches every semantic constraint."
                    if not matches
                    else "Multiple accepted review rules match the same semantic signature."
                ),
                matched_rule_ids=sorted(rule.rule_id for rule in matches),
            ),
            "finding_id": finding.get("finding_id"),
            "match_status": "MANUAL_REVIEW_REQUIRED",
        }
    rule = matches[0]
    if isinstance(rule, ReviewRule):
        rationale = rule.human_rationale
        evidence: list[Any] = list(rule.evidence_references)
        operation = dict(rule.structured_canonical_operation)
    else:
        rationale = str(rule.provenance.get("rationale") or "")
        evidence = list(rule.provenance.get("evidence_references") or ())
        operation = dict(rule.structured_answer)
    permitted = {
        "answer_id": "CONFIRM_REGISTERED_REVIEW_RULE",
        "parameters": {
            "rule_id": rule.rule_id,
            "sha256": rule.registered_sha256,
            "semantic_signature_sha256": finding["semantic_signature_sha256"],
        },
    }
    return {
        "finding_id": finding["finding_id"],
        "match_status": "EXACT",
        "reason_code": "EXACT_REVIEW_RULE",
        "status": "REVIEW_RULE_SUGGESTED",
        "registered_rule_id": rule.rule_id,
        "registered_rule_sha256": rule.registered_sha256,
        "prior_rationale": rationale,
        "prior_evidence": evidence,
        "resolved_structured_operation": operation,
        "permitted_structured_answer": permitted,
        "human_confirmation": "REQUIRED",
        "approval_state": "NOT_REQUESTED",
        "application_state": "NOT_REQUESTED",
    }


__all__ = [
    "DEFAULT_REVIEW_MEMORY_ROOT",
    "DEFAULT_REVIEW_MEMORY_REGISTRY",
    "DEFAULT_REVIEW_MEMORY_REGISTRY_SCHEMA",
    "ReviewMemoryError",
    "ReviewRule",
    "ReviewRuleV2",
    "load_review_registry",
    "load_review_rule",
    "load_review_rule_v2",
    "review_match_signature",
    "sha256_value",
    "suggest_review_for_finding",
    "suggest_review_rule",
]
