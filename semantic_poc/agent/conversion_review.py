from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from semantic_poc.src.models import (
    DBT_SEMANTIC_YAML,
    PROJECT_ROOT,
    SNOWFLAKE_OUTPUT,
    load_yaml,
)
from semantic_poc.src.semantic_ir import (
    SupportClassification,
    build_canonical_metric_ir_index,
    generate_dax_definition,
    generate_snowflake_definition,
    validate_cross_target,
)

from .change_store import ChangeStore, DEFAULT_CHANGE_DIR
from .import_models import canonical_json_text, utc_timestamp, validate_import_id, validate_timestamp
from .import_store import DEFAULT_IMPORT_DIR, ImportNotFoundError, ImportStore
from .import_workflow import verify_import_source
from .proposal_models import ProposalStatus
from .proposal_engine import ProposalInputError, propose_change
from .schemas import MetricChangeRequest
from .workflow import validate_actor


CONVERSION_SCHEMA_VERSION = 1
FINDING_ID_PATTERN = re.compile(r"^fnd_[0-9a-f]{16}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_TEXT_PATTERN = re.compile(r"^[^\x00-\x08\x0b\x0c\x0e-\x1f]{1,2000}$")
UNCERTAIN_DESCRIPTION_WORDS = frozenset(
    {
        "apparently",
        "appears",
        "assumed",
        "could",
        "inferred",
        "likely",
        "may",
        "might",
        "perhaps",
        "possibly",
        "probably",
        "seems",
        "uncertain",
    }
)
SUPPORTED_IMPORT_PREFIXES = (
    "SUPPORTED_EXACT",
    "SUPPORTED_WITH_MAPPING",
    "SUPPORTED_WITH_ASSUMPTIONS",
)


class FailureCategory(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    NAME_OR_DESCRIPTION_DRIFT = "NAME_OR_DESCRIPTION_DRIFT"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    SEMANTIC_ROLE_MISMATCH = "SEMANTIC_ROLE_MISMATCH"
    AGGREGATION_MISMATCH = "AGGREGATION_MISMATCH"
    FORMULA_MISMATCH = "FORMULA_MISMATCH"
    UNIT_CONVERSION_MISMATCH = "UNIT_CONVERSION_MISMATCH"
    FORMAT_MISMATCH = "FORMAT_MISMATCH"
    RELATIONSHIP_MISMATCH = "RELATIONSHIP_MISMATCH"
    OMITTED_OBJECT = "OMITTED_OBJECT"
    UNSUPPORTED_SOURCE_CONSTRUCT = "UNSUPPORTED_SOURCE_CONSTRUCT"
    GENERATED_DESCRIPTION_UNVERIFIED = "GENERATED_DESCRIPTION_UNVERIFIED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class FindingSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class ReviewAction(str, Enum):
    ACCEPT = "ACCEPT"
    CORRECT = "CORRECT"
    REJECT = "REJECT"


class ConversionState(str, Enum):
    REVIEW_PENDING = "REVIEW_PENDING"
    READY_TO_FINALIZE = "READY_TO_FINALIZE"
    FINALIZED = "FINALIZED"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_hash(value: Any) -> str:
    return _sha256(canonical_json_text(value).encode("utf-8"))


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or not SAFE_TEXT_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be non-empty safe text of at most 2000 characters.")
    return value


def _finding_id(
    *,
    rule_id: str,
    category: FailureCategory,
    source_object: str,
    generated_object: str | None,
) -> str:
    payload = {
        "rule_id": rule_id,
        "category": category.value,
        "source_object": source_object,
        "generated_object": generated_object,
    }
    return "fnd_" + _stable_hash(payload)[:16]


@dataclass(frozen=True)
class ConversionFinding:
    finding_id: str
    rule_id: str
    category: FailureCategory
    severity: FindingSeverity
    source_object_id: str
    source_object: str
    source_expression: str | None
    source_support: str
    generated_object: str | None
    generated_expression: str | None
    evidence: tuple[str, ...]
    recommended_correction: str
    automatic_correction_safe: bool
    canonical_metric: str | None = None

    def __post_init__(self) -> None:
        if not FINDING_ID_PATTERN.fullmatch(self.finding_id):
            raise ValueError(f"Invalid finding_id: {self.finding_id!r}")
        _required_text(self.rule_id, "rule_id")
        _required_text(self.source_object_id, "source_object_id")
        _required_text(self.source_object, "source_object")
        _required_text(self.source_support, "source_support")
        _required_text(self.recommended_correction, "recommended_correction")
        if not self.evidence:
            raise ValueError("A conversion finding requires deterministic evidence.")
        for item in self.evidence:
            _required_text(item, "evidence")
        expected = _finding_id(
            rule_id=self.rule_id,
            category=self.category,
            source_object=self.source_object,
            generated_object=self.generated_object,
        )
        if self.finding_id != expected:
            raise ValueError("finding_id does not match the stable finding identity.")

    @classmethod
    def create(
        cls,
        *,
        rule_id: str,
        category: FailureCategory,
        severity: FindingSeverity,
        source_object_id: str,
        source_object: str,
        source_expression: str | None,
        source_support: str,
        generated_object: str | None,
        generated_expression: str | None,
        evidence: Iterable[str],
        recommended_correction: str,
        automatic_correction_safe: bool = False,
        canonical_metric: str | None = None,
    ) -> "ConversionFinding":
        return cls(
            finding_id=_finding_id(
                rule_id=rule_id,
                category=category,
                source_object=source_object,
                generated_object=generated_object,
            ),
            rule_id=rule_id,
            category=category,
            severity=severity,
            source_object_id=source_object_id,
            source_object=source_object,
            source_expression=source_expression,
            source_support=source_support,
            generated_object=generated_object,
            generated_expression=generated_expression,
            evidence=tuple(evidence),
            recommended_correction=recommended_correction,
            automatic_correction_safe=automatic_correction_safe,
            canonical_metric=canonical_metric,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "source_object_id": self.source_object_id,
            "source_object": self.source_object,
            "source_expression": self.source_expression,
            "source_support": self.source_support,
            "generated_object": self.generated_object,
            "generated_expression": self.generated_expression,
            "evidence": list(self.evidence),
            "recommended_correction": self.recommended_correction,
            "automatic_correction_safe": self.automatic_correction_safe,
            "canonical_metric": self.canonical_metric,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConversionFinding":
        return cls(
            finding_id=str(value["finding_id"]),
            rule_id=str(value["rule_id"]),
            category=FailureCategory(str(value["category"])),
            severity=FindingSeverity(str(value["severity"])),
            source_object_id=str(value["source_object_id"]),
            source_object=str(value["source_object"]),
            source_expression=(
                str(value["source_expression"])
                if value.get("source_expression") is not None
                else None
            ),
            source_support=str(value["source_support"]),
            generated_object=(
                str(value["generated_object"])
                if value.get("generated_object") is not None
                else None
            ),
            generated_expression=(
                str(value["generated_expression"])
                if value.get("generated_expression") is not None
                else None
            ),
            evidence=tuple(str(item) for item in value.get("evidence", [])),
            recommended_correction=str(value["recommended_correction"]),
            automatic_correction_safe=bool(value.get("automatic_correction_safe")),
            canonical_metric=(
                str(value["canonical_metric"])
                if value.get("canonical_metric") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ReviewDecision:
    finding_id: str
    action: ReviewAction
    actor: str
    timestamp: str
    rationale: str
    source_object: str
    source: Mapping[str, Any]
    mapping_file_sha256: str | None
    canonical_metric: str | None
    change_id: str | None
    before_sha256: str
    after_sha256: str
    lifecycle_state: str

    def __post_init__(self) -> None:
        if not FINDING_ID_PATTERN.fullmatch(self.finding_id):
            raise ValueError("Review decision has an invalid finding_id.")
        validate_actor(self.actor)
        validate_timestamp(self.timestamp)
        _required_text(self.rationale, "rationale")
        _required_text(self.source_object, "source_object")
        if not SHA256_PATTERN.fullmatch(self.before_sha256) or not SHA256_PATTERN.fullmatch(
            self.after_sha256
        ):
            raise ValueError("Review decision hashes must be lowercase SHA-256 values.")
        if not isinstance(self.source, Mapping):
            raise ValueError("Review decision source must be structured evidence.")
        if self.mapping_file_sha256 is not None and not SHA256_PATTERN.fullmatch(
            self.mapping_file_sha256
        ):
            raise ValueError("mapping_file_sha256 must be a lowercase SHA-256 value or null.")
        _required_text(self.lifecycle_state, "lifecycle_state")

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "action": self.action.value,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "rationale": self.rationale,
            "source_object": self.source_object,
            "source": dict(self.source),
            "mapping_file_sha256": self.mapping_file_sha256,
            "canonical_metric": self.canonical_metric,
            "change_id": self.change_id,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "lifecycle_state": self.lifecycle_state,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewDecision":
        return cls(
            finding_id=str(value["finding_id"]),
            action=ReviewAction(str(value["action"])),
            actor=str(value["actor"]),
            timestamp=str(value["timestamp"]),
            rationale=str(value["rationale"]),
            source_object=str(value["source_object"]),
            source=dict(value.get("source") or {}),
            mapping_file_sha256=(
                str(value["mapping_file_sha256"])
                if value.get("mapping_file_sha256") is not None
                else None
            ),
            canonical_metric=(
                str(value["canonical_metric"])
                if value.get("canonical_metric") is not None
                else None
            ),
            change_id=str(value["change_id"]) if value.get("change_id") is not None else None,
            before_sha256=str(value["before_sha256"]),
            after_sha256=str(value["after_sha256"]),
            lifecycle_state=str(value["lifecycle_state"]),
        )


@dataclass(frozen=True)
class ConversionReview:
    schema_version: int
    import_id: str
    created_at: str
    state: ConversionState
    source_snapshot_sha256: str
    import_semantic_sha256: str
    canonical_sha256: str
    autopilot_status: str
    autopilot_sha256: str | None
    result_evidence_status: str
    result_evidence_sha256: str | None
    findings: tuple[ConversionFinding, ...]
    decisions: tuple[ReviewDecision, ...]
    accepted_output_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != CONVERSION_SCHEMA_VERSION:
            raise ValueError("Unsupported conversion-review schema version.")
        validate_import_id(self.import_id)
        validate_timestamp(self.created_at)
        for value in (
            self.source_snapshot_sha256,
            self.import_semantic_sha256,
            self.canonical_sha256,
        ):
            if not SHA256_PATTERN.fullmatch(value):
                raise ValueError("Conversion review contains an invalid SHA-256 value.")
        if self.autopilot_sha256 is not None and not SHA256_PATTERN.fullmatch(
            self.autopilot_sha256
        ):
            raise ValueError("autopilot_sha256 is invalid.")
        if self.result_evidence_sha256 is not None and not SHA256_PATTERN.fullmatch(
            self.result_evidence_sha256
        ):
            raise ValueError("result_evidence_sha256 is invalid.")
        if self.accepted_output_sha256 is not None and not SHA256_PATTERN.fullmatch(
            self.accepted_output_sha256
        ):
            raise ValueError("accepted_output_sha256 is invalid.")
        ids = [item.finding_id for item in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("Conversion review findings must have unique IDs.")
        decision_ids = [item.finding_id for item in self.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("Each finding may have at most one review decision.")
        if not set(decision_ids) <= set(ids):
            raise ValueError("Review decisions must reference findings in the same review.")

    @property
    def semantic_content_sha256(self) -> str:
        return _stable_hash(self.semantic_payload())

    @property
    def decisions_by_finding(self) -> dict[str, ReviewDecision]:
        return {item.finding_id: item for item in self.decisions}

    @property
    def unresolved_blockers(self) -> tuple[ConversionFinding, ...]:
        decided = self.decisions_by_finding
        return tuple(
            item
            for item in self.findings
            if item.severity is FindingSeverity.BLOCKING and item.finding_id not in decided
        )

    @property
    def unaccepted_exact_matches(self) -> tuple[ConversionFinding, ...]:
        decisions = self.decisions_by_finding
        return tuple(
            item
            for item in self.findings
            if item.category is FailureCategory.EXACT_MATCH
            and (
                item.finding_id not in decisions
                or decisions[item.finding_id].action not in {ReviewAction.ACCEPT, ReviewAction.CORRECT}
            )
        )

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "import_id": self.import_id,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "import_semantic_sha256": self.import_semantic_sha256,
            "canonical_sha256": self.canonical_sha256,
            "autopilot_status": self.autopilot_status,
            "autopilot_sha256": self.autopilot_sha256,
            "result_evidence_status": self.result_evidence_status,
            "result_evidence_sha256": self.result_evidence_sha256,
            "findings": [item.to_dict() for item in self.findings],
            "decisions": [item.to_dict() for item in self.decisions],
            "accepted_output_sha256": self.accepted_output_sha256,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_payload(),
            "created_at": self.created_at,
            "state": self.state.value,
            "semantic_content_sha256": self.semantic_content_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConversionReview":
        supplied_hash = value.get("semantic_content_sha256")
        result = cls(
            schema_version=int(value["schema_version"]),
            import_id=str(value["import_id"]),
            created_at=str(value["created_at"]),
            state=ConversionState(str(value["state"])),
            source_snapshot_sha256=str(value["source_snapshot_sha256"]),
            import_semantic_sha256=str(value["import_semantic_sha256"]),
            canonical_sha256=str(value["canonical_sha256"]),
            autopilot_status=str(value["autopilot_status"]),
            autopilot_sha256=(
                str(value["autopilot_sha256"])
                if value.get("autopilot_sha256") is not None
                else None
            ),
            result_evidence_status=str(value["result_evidence_status"]),
            result_evidence_sha256=(
                str(value["result_evidence_sha256"])
                if value.get("result_evidence_sha256") is not None
                else None
            ),
            findings=tuple(
                ConversionFinding.from_dict(item) for item in value.get("findings", [])
            ),
            decisions=tuple(
                ReviewDecision.from_dict(item) for item in value.get("decisions", [])
            ),
            accepted_output_sha256=(
                str(value["accepted_output_sha256"])
                if value.get("accepted_output_sha256") is not None
                else None
            ),
        )
        if supplied_hash != result.semantic_content_sha256:
            raise ValueError("Conversion review semantic hash does not match its content.")
        return result


class ConversionStoreError(RuntimeError):
    pass


class ConversionStore:
    REVIEW_NAME = "conversion-review.json"
    REPORT_NAMES = frozenset(
        {
            "conversion-benchmark.json",
            "conversion-benchmark.md",
            "semantic-lint-report.json",
            "semantic-lint-report.md",
            "snowflake-autopilot-comparison.md",
            "human-review-decisions.json",
            "maintenance-equivalence-report.md",
            "accepted-snowflake-semantic-view.yml",
        }
    )

    def __init__(self, import_root: Path = DEFAULT_IMPORT_DIR) -> None:
        self.root = Path(import_root).resolve()

    def _run_dir(self, import_id: str) -> Path:
        validate_import_id(import_id)
        path = self.root / import_id
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != self.root:
            raise ImportNotFoundError(f"Import run does not exist or is unsafe: {import_id}")
        return path

    def review_dir(self, import_id: str) -> Path:
        return self._run_dir(import_id) / "conversion"

    def _path(self, import_id: str, name: str) -> Path:
        if name != self.REVIEW_NAME and name not in self.REPORT_NAMES:
            raise ValueError(f"Unsupported conversion artifact: {name!r}")
        directory = self.review_dir(import_id)
        path = directory / name
        if path.parent != directory:
            raise ConversionStoreError("Conversion artifact path escaped its review directory.")
        return path

    def _lock_path(self, import_id: str) -> Path:
        return self.root / f".{import_id}.conversion.lock"

    def _lock(self, import_id: str) -> int:
        try:
            return os.open(self._lock_path(import_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ConversionStoreError(f"Conversion review is locked: {import_id}") from exc

    def _unlock(self, import_id: str, descriptor: int) -> None:
        os.close(descriptor)
        try:
            self._lock_path(import_id).unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _atomic_replace(path: Path, payload: bytes) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}-", suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def create(
        self,
        review: ConversionReview,
        artifacts: Mapping[str, str | bytes],
    ) -> None:
        descriptor = self._lock(review.import_id)
        staging: Path | None = None
        try:
            directory = self.review_dir(review.import_id)
            if directory.exists() or directory.is_symlink():
                raise ConversionStoreError(
                    f"Conversion review already exists: {review.import_id}"
                )
            staging = Path(
                tempfile.mkdtemp(prefix=".conversion-stage-", dir=self._run_dir(review.import_id))
            )
            payloads: dict[str, bytes] = {
                self.REVIEW_NAME: canonical_json_text(review.to_dict(), pretty=True).encode("utf-8")
            }
            for name, value in artifacts.items():
                if name not in self.REPORT_NAMES:
                    raise ValueError(f"Unsupported conversion report: {name!r}")
                payloads[name] = value.encode("utf-8") if isinstance(value, str) else value
            for name in sorted(payloads):
                with (staging / name).open("xb") as handle:
                    handle.write(payloads[name])
                    handle.flush()
                    os.fsync(handle.fileno())
            os.replace(staging, directory)
            staging = None
        except Exception:
            if staging is not None and staging.is_dir():
                shutil.rmtree(staging)
            raise
        finally:
            self._unlock(review.import_id, descriptor)

    def load(self, import_id: str) -> ConversionReview:
        path = self._path(import_id, self.REVIEW_NAME)
        if path.is_symlink() or not path.is_file():
            raise ImportNotFoundError(f"Conversion review does not exist: {import_id}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConversionStoreError(f"Invalid conversion review: {exc}") from exc
        if not isinstance(value, Mapping):
            raise ConversionStoreError("Conversion review must contain a JSON object.")
        try:
            return ConversionReview.from_dict(value)
        except (TypeError, ValueError) as exc:
            raise ConversionStoreError(f"Invalid conversion review: {exc}") from exc

    def update(self, review: ConversionReview, artifacts: Mapping[str, str | bytes]) -> None:
        descriptor = self._lock(review.import_id)
        try:
            current = self.load(review.import_id)
            if current.state is ConversionState.FINALIZED:
                raise ConversionStoreError("Finalized conversion reviews are immutable.")
            for name, value in artifacts.items():
                if name not in self.REPORT_NAMES:
                    raise ValueError(f"Unsupported conversion report: {name!r}")
                payload = value.encode("utf-8") if isinstance(value, str) else value
                self._atomic_replace(self._path(review.import_id, name), payload)
            self._atomic_replace(
                self._path(review.import_id, self.REVIEW_NAME),
                canonical_json_text(review.to_dict(), pretty=True).encode("utf-8"),
            )
        finally:
            self._unlock(review.import_id, descriptor)

    def find_finding(self, finding_id: str) -> tuple[ConversionReview, ConversionFinding]:
        if not FINDING_ID_PATTERN.fullmatch(finding_id):
            raise ValueError(f"Invalid finding ID: {finding_id!r}")
        matches: list[tuple[ConversionReview, ConversionFinding]] = []
        for run in ImportStore(self.root).list_runs():
            try:
                review = self.load(run.import_id)
            except ImportNotFoundError:
                continue
            for finding in review.findings:
                if finding.finding_id == finding_id:
                    matches.append((review, finding))
        if len(matches) != 1:
            raise ValueError(
                f"Finding ID must resolve to exactly one active conversion review: {finding_id}"
            )
        return matches[0]


def _safe_repository_file(path: str | Path, *, label: str) -> Path:
    requested = Path(path)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    try:
        resolved = requested.resolve(strict=True)
    except (OSError, FileNotFoundError) as exc:
        raise ValueError(f"{label} does not exist: {requested}") from exc
    root = PROJECT_ROOT.resolve()
    if not resolved.is_file() or resolved.is_symlink() or not resolved.is_relative_to(root):
        raise ValueError(f"{label} must be a repository-contained regular file.")
    return resolved


def _source_measures(run: Any) -> dict[str, Mapping[str, Any]]:
    return {
        str(item["object_id"]): item
        for item in run.inventory.get("measures", [])
        if isinstance(item, Mapping) and item.get("object_id")
    }


def _source_columns(run: Any) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {
        (str(item.get("table", "")).casefold(), str(item.get("name", "")).casefold()): item
        for item in run.inventory.get("columns", [])
        if isinstance(item, Mapping)
    }


def _normalized_expression(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _autopilot_objects(data: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "facts": [],
        "dimensions": [],
        "metrics": [],
        "relationships": [],
    }
    for table in data.get("tables", []) or []:
        if not isinstance(table, Mapping):
            continue
        table_name = str(table.get("name") or "")
        for role in ("facts", "dimensions", "time_dimensions", "metrics"):
            target_role = "dimensions" if role == "time_dimensions" else role
            for item in table.get(role, []) or []:
                if isinstance(item, Mapping):
                    result[target_role].append({"table": table_name, **dict(item)})
    for item in data.get("metrics", []) or []:
        if isinstance(item, Mapping):
            result["metrics"].append({"table": None, **dict(item)})
    for item in data.get("relationships", []) or []:
        if isinstance(item, Mapping):
            result["relationships"].append(dict(item))
    for values in result.values():
        values.sort(
            key=lambda item: (
                str(item.get("table") or "").casefold(),
                str(item.get("name") or "").casefold(),
            )
        )
    return result


def _finding_for_candidate(
    candidate: Mapping[str, Any],
    measure: Mapping[str, Any],
) -> ConversionFinding:
    support = str(candidate.get("classification") or "MANUAL_REVIEW_REQUIRED")
    comparison = candidate.get("comparison") or {}
    snowflake = candidate.get("regenerated_snowflake") or {}
    mapping = candidate.get("mapping") or {}
    source_object = f"{candidate.get('source_table')}[{candidate.get('source_measure')}]"
    generated_object = str(snowflake.get("name")) if snowflake.get("name") else None
    generated_expression = str(snowflake.get("expr")) if snowflake.get("expr") else None
    canonical_metric = mapping.get("canonical_metric")
    diagnostics = candidate.get("diagnostics") or []
    codes = {str(item.get("code")) for item in diagnostics if isinstance(item, Mapping)}
    if support in SUPPORTED_IMPORT_PREFIXES and comparison.get("semantic_equivalent"):
        return ConversionFinding.create(
            rule_id="COMPARE_SUPPORTED_SEMANTICS",
            category=FailureCategory.EXACT_MATCH,
            severity=FindingSeverity.INFO,
            source_object_id=str(candidate["source_object_id"]),
            source_object=source_object,
            source_expression=str(measure.get("expression") or ""),
            source_support=support,
            generated_object=generated_object,
            generated_expression=generated_expression,
            evidence=(
                "The supported Power BI AST equals the deterministic canonical regeneration.",
                f"Canonical metric: {canonical_metric}",
            ),
            recommended_correction="Explicitly accept the exact canonical mapping.",
            canonical_metric=str(canonical_metric) if canonical_metric else None,
        )
    if support == "UNSUPPORTED" or any(code.startswith("DAX_") for code in codes):
        category = FailureCategory.UNSUPPORTED_SOURCE_CONSTRUCT
        rule = "COMPARE_UNSUPPORTED_DAX"
        evidence = tuple(sorted(codes)) or ("The source DAX is outside the supported grammar.",)
    elif comparison and not comparison.get("semantic_equivalent"):
        category = FailureCategory.FORMULA_MISMATCH
        rule = "COMPARE_FORMULA_AST"
        evidence = tuple(str(item) for item in comparison.get("differences", [])) or (
            "Source and regenerated ASTs are not equivalent.",
        )
    else:
        category = FailureCategory.MANUAL_REVIEW_REQUIRED
        rule = "COMPARE_MAPPING_UNRESOLVED"
        evidence = tuple(sorted(codes)) or (
            "No exact canonical mapping and cross-target equivalence proof exists.",
        )
    return ConversionFinding.create(
        rule_id=rule,
        category=category,
        severity=FindingSeverity.BLOCKING,
        source_object_id=str(candidate["source_object_id"]),
        source_object=source_object,
        source_expression=str(measure.get("expression") or ""),
        source_support=support,
        generated_object=generated_object,
        generated_expression=generated_expression,
        evidence=evidence,
        recommended_correction=(
            "Map to an exact supported canonical metric, create a typed canonical proposal, or explicitly exclude the unsupported object."
        ),
        canonical_metric=str(canonical_metric) if canonical_metric else None,
    )


def _lint_candidate(
    candidate: Mapping[str, Any],
    measure: Mapping[str, Any],
    columns: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[ConversionFinding]:
    findings: list[ConversionFinding] = []
    source_object = f"{candidate.get('source_table')}[{candidate.get('source_measure')}]"
    source_id = str(candidate["source_object_id"])
    support = str(candidate.get("classification") or "MANUAL_REVIEW_REQUIRED")
    expression = str(measure.get("expression") or "")
    analysis = measure.get("analysis") or {}
    ast = analysis.get("ast") or {}
    pattern = str(analysis.get("pattern") or "")
    generated = candidate.get("regenerated_snowflake") or {}
    generated_name = str(generated.get("name")) if generated.get("name") else None
    generated_expression = str(generated.get("expr")) if generated.get("expr") else None
    canonical_metric = (candidate.get("mapping") or {}).get("canonical_metric")

    name_text = " ".join(
        str(item or "")
        for item in (
            candidate.get("source_measure"),
            measure.get("description"),
        )
    ).casefold()
    seconds_source = bool(re.search(r"\bseconds?\b|_seconds\b", expression, re.IGNORECASE))
    hours_target = bool(re.search(r"\bhours?\b|_hours\b", name_text, re.IGNORECASE))
    divisor_match = re.search(r"/\s*(\d+(?:\.\d+)?)|DIVIDE\s*\([^,]+,\s*(\d+(?:\.\d+)?)", expression, re.IGNORECASE)
    if seconds_source and hours_target:
        divisor = (divisor_match.group(1) or divisor_match.group(2)) if divisor_match else None
        if divisor is None or not math.isclose(float(divisor), 3600.0):
            findings.append(
                ConversionFinding.create(
                    rule_id="LINT_TIME_UNIT_CONVERSION",
                    category=FailureCategory.UNIT_CONVERSION_MISMATCH,
                    severity=FindingSeverity.BLOCKING,
                    source_object_id=source_id,
                    source_object=source_object,
                    source_expression=expression,
                    source_support=support,
                    generated_object=generated_name,
                    generated_expression=generated_expression,
                    evidence=(
                        "The source field/expression is seconds while the measure is labelled as hours.",
                        f"Observed divisor: {divisor or '<missing>'}; required seconds-to-hours divisor: 3600.",
                    ),
                    recommended_correction="Create or map to a canonical scaled-sum metric with divisor 3600.",
                    canonical_metric=str(canonical_metric) if canonical_metric else None,
                )
            )

    column_name = str(ast.get("column") or "")
    column = columns.get((str(ast.get("table") or "").casefold(), column_name.casefold()))
    data_type = str((column or {}).get("data_type") or "").casefold()
    if pattern in {"SUM", "SCALED_SUM"} and re.search(r"(?:^|_)(?:id|key)$", column_name, re.IGNORECASE):
        findings.append(
            ConversionFinding.create(
                rule_id="LINT_IDENTIFIER_AS_FACT",
                category=FailureCategory.SEMANTIC_ROLE_MISMATCH,
                severity=FindingSeverity.BLOCKING,
                source_object_id=source_id,
                source_object=source_object,
                source_expression=expression,
                source_support=support,
                generated_object=generated_name,
                generated_expression=generated_expression,
                evidence=(f"Column {column_name!r} has identifier/key naming but is summed.",),
                recommended_correction="Map the identifier as a dimension/key or use an explicit count metric.",
                canonical_metric=str(canonical_metric) if canonical_metric else None,
            )
        )
    if pattern in {"SUM", "SCALED_SUM"} and data_type in {"string", "text"}:
        findings.append(
            ConversionFinding.create(
                rule_id="LINT_TEXT_AS_ADDITIVE_FACT",
                category=FailureCategory.TYPE_MISMATCH,
                severity=FindingSeverity.BLOCKING,
                source_object_id=source_id,
                source_object=source_object,
                source_expression=expression,
                source_support=support,
                generated_object=generated_name,
                generated_expression=generated_expression,
                evidence=(f"Column {column_name!r} is typed {data_type!r} but is used by SUM.",),
                recommended_correction="Correct the source type or map the field as a non-additive dimension.",
                canonical_metric=str(canonical_metric) if canonical_metric else None,
            )
        )

    format_string = str(measure.get("format_string") or "")
    percentage_name = bool(re.search(r"\b(?:rate|ratio|percent|percentage|pct)\b", name_text))
    if (pattern == "RATIO" or percentage_name) and "%" not in format_string:
        findings.append(
            ConversionFinding.create(
                rule_id="LINT_PERCENTAGE_FORMAT",
                category=FailureCategory.FORMAT_MISMATCH,
                severity=FindingSeverity.BLOCKING,
                source_object_id=source_id,
                source_object=source_object,
                source_expression=expression,
                source_support=support,
                generated_object=generated_name,
                generated_expression=generated_expression,
                evidence=(
                    "The measure is a ratio/percentage by AST or name.",
                    f"Observed Power BI format string: {format_string or '<missing>'}.",
                ),
                recommended_correction="Map to a canonical percentage format and regenerate both targets.",
                canonical_metric=str(canonical_metric) if canonical_metric else None,
            )
        )
    if "%" in format_string and pattern not in {"RATIO", "SUM"}:
        findings.append(
            ConversionFinding.create(
                rule_id="LINT_PERCENTAGE_FORMAT_CONFLICT",
                category=FailureCategory.FORMAT_MISMATCH,
                severity=FindingSeverity.BLOCKING,
                source_object_id=source_id,
                source_object=source_object,
                source_expression=expression,
                source_support=support,
                generated_object=generated_name,
                generated_expression=generated_expression,
                evidence=(
                    f"A {pattern or 'non-ratio'} measure uses percentage format {format_string!r}.",
                ),
                recommended_correction="Use a count/decimal format or map the source to an explicit canonical ratio.",
                canonical_metric=str(canonical_metric) if canonical_metric else None,
            )
        )
    if pattern == "SUM" and (percentage_name or "%" in format_string):
        findings.append(
            ConversionFinding.create(
                rule_id="LINT_NON_ADDITIVE_RATIO",
                category=FailureCategory.AGGREGATION_MISMATCH,
                severity=FindingSeverity.BLOCKING,
                source_object_id=source_id,
                source_object=source_object,
                source_expression=expression,
                source_support=support,
                generated_object=generated_name,
                generated_expression=generated_expression,
                evidence=("A percentage/rate is exposed through additive SUM semantics.",),
                recommended_correction="Represent the value as a canonical ratio or explicit non-additive metric.",
                canonical_metric=str(canonical_metric) if canonical_metric else None,
            )
        )
    if generated_name and re.search(r"(?:^|_)sum_of(?:_|$)", generated_name, re.IGNORECASE):
        findings.append(
            ConversionFinding.create(
                rule_id="LINT_SUSPICIOUS_SUM_OF_NAME",
                category=FailureCategory.AGGREGATION_MISMATCH,
                severity=FindingSeverity.WARNING,
                source_object_id=source_id,
                source_object=source_object,
                source_expression=expression,
                source_support=support,
                generated_object=generated_name,
                generated_expression=generated_expression,
                evidence=(f"Generated name {generated_name!r} uses the suspicious SUM_OF convention.",),
                recommended_correction="Confirm the field is additive and replace generated naming with the canonical metric name.",
                canonical_metric=str(canonical_metric) if canonical_metric else None,
            )
        )

    dependency_names = set(str(item).casefold() for item in analysis.get("measure_dependencies", []))
    all_names = set()
    # Filled by the caller through the resolved dependency object IDs when available.
    for dependency in measure.get("dependency_object_ids", []) or []:
        all_names.add(str(dependency).casefold())
    if dependency_names and len(all_names) < len(dependency_names):
        findings.append(
            ConversionFinding.create(
                rule_id="LINT_MISSING_MEASURE_DEPENDENCY",
                category=FailureCategory.OMITTED_OBJECT,
                severity=FindingSeverity.BLOCKING,
                source_object_id=source_id,
                source_object=source_object,
                source_expression=expression,
                source_support=support,
                generated_object=generated_name,
                generated_expression=generated_expression,
                evidence=("One or more DAX measure dependencies did not resolve to inventory object IDs.",),
                recommended_correction="Resolve every dependency exactly before generating or accepting the metric.",
                canonical_metric=str(canonical_metric) if canonical_metric else None,
            )
        )
    return findings


def _autopilot_findings(
    run: Any,
    autopilot: Mapping[str, Any],
) -> list[ConversionFinding]:
    findings: list[ConversionFinding] = []
    normalized = _autopilot_objects(autopilot)
    metric_index = {
        str(item.get("name") or "").casefold(): item for item in normalized["metrics"]
    }
    source_columns = _source_columns(run)
    source_measures_by_name = {
        str(item.get("name") or "").casefold(): item
        for item in run.inventory.get("measures", [])
        if isinstance(item, Mapping)
    }
    for candidate in run.classifications:
        mapping = candidate.get("mapping") or {}
        expected_name = mapping.get("snowflake_metric") or mapping.get("canonical_metric")
        expected = candidate.get("regenerated_snowflake") or {}
        source_object = f"{candidate.get('source_table')}[{candidate.get('source_measure')}]"
        if str(candidate.get("classification")) in SUPPORTED_IMPORT_PREFIXES and expected_name:
            actual = metric_index.get(str(expected_name).casefold())
            if actual is None:
                findings.append(
                    ConversionFinding.create(
                        rule_id="LINT_AUTOPILOT_OMITTED_SUPPORTED_MEASURE",
                        category=FailureCategory.OMITTED_OBJECT,
                        severity=FindingSeverity.BLOCKING,
                        source_object_id=str(candidate["source_object_id"]),
                        source_object=source_object,
                        source_expression=None,
                        source_support=str(candidate.get("classification")),
                        generated_object=str(expected_name),
                        generated_expression=None,
                        evidence=("A supported source measure is absent from the supplied Autopilot YAML.",),
                        recommended_correction="Use the governed repository mapping or correct the exported Autopilot definition.",
                        canonical_metric=str(mapping.get("canonical_metric")) if mapping.get("canonical_metric") else None,
                    )
                )
            elif expected.get("expr") and _normalized_expression(actual.get("expr")) != _normalized_expression(expected.get("expr")):
                findings.append(
                    ConversionFinding.create(
                        rule_id="COMPARE_AUTOPILOT_FORMULA",
                        category=FailureCategory.FORMULA_MISMATCH,
                        severity=FindingSeverity.BLOCKING,
                        source_object_id=str(candidate["source_object_id"]),
                        source_object=source_object,
                        source_expression=None,
                        source_support=str(candidate.get("classification")),
                        generated_object=str(actual.get("name") or expected_name),
                        generated_expression=str(actual.get("expr") or ""),
                        evidence=(
                            f"Repository expression: {expected.get('expr')}",
                            f"Autopilot expression: {actual.get('expr')}",
                        ),
                        recommended_correction="Reject the generated mapping or map to the exact canonical metric.",
                        canonical_metric=str(mapping.get("canonical_metric")) if mapping.get("canonical_metric") else None,
                    )
                )

    for role in ("facts", "dimensions", "metrics"):
        for item in normalized[role]:
            name = str(item.get("name") or "")
            expression = str(item.get("expr") or "")
            description = str(item.get("description") or "")
            source_id = f"autopilot:{role}:{str(item.get('table') or '')}:{name}"
            source_object = f"Autopilot {role[:-1] if role.endswith('s') else role} {name}"
            description_words = set(re.findall(r"[A-Za-z]+", description.casefold()))
            uncertain = sorted(description_words & UNCERTAIN_DESCRIPTION_WORDS)
            if uncertain:
                findings.append(
                    ConversionFinding.create(
                        rule_id="LINT_GENERATED_DESCRIPTION_UNCERTAIN",
                        category=FailureCategory.GENERATED_DESCRIPTION_UNVERIFIED,
                        severity=FindingSeverity.WARNING,
                        source_object_id=source_id,
                        source_object=source_object,
                        source_expression=None,
                        source_support="AUTOPILOT_EVIDENCE",
                        generated_object=name,
                        generated_expression=expression or None,
                        evidence=("Uncertain generated-description terms: " + ", ".join(uncertain),),
                        recommended_correction="Use the reviewed canonical description; do not promote uncertain generated prose.",
                    )
                )
            if re.search(r"(?:^|_)sum_of(?:_|$)", name, re.IGNORECASE):
                findings.append(
                    ConversionFinding.create(
                        rule_id="LINT_AUTOPILOT_SUM_OF_NAME",
                        category=FailureCategory.AGGREGATION_MISMATCH,
                        severity=FindingSeverity.WARNING,
                        source_object_id=source_id,
                        source_object=source_object,
                        source_expression=None,
                        source_support="AUTOPILOT_EVIDENCE",
                        generated_object=name,
                        generated_expression=expression or None,
                        evidence=(f"Generated object name {name!r} uses SUM_OF naming.",),
                        recommended_correction="Confirm additivity and use the exact canonical target name.",
                    )
                )
            if role == "dimensions" and name.casefold() in source_measures_by_name:
                findings.append(
                    ConversionFinding.create(
                        rule_id="LINT_NUMERIC_MEASURE_AS_DIMENSION",
                        category=FailureCategory.SEMANTIC_ROLE_MISMATCH,
                        severity=FindingSeverity.BLOCKING,
                        source_object_id=source_id,
                        source_object=source_object,
                        source_expression=str(source_measures_by_name[name.casefold()].get("expression") or ""),
                        source_support="AUTOPILOT_EVIDENCE",
                        generated_object=name,
                        generated_expression=expression or None,
                        evidence=("A Power BI measure was emitted as a Snowflake dimension.",),
                        recommended_correction="Map the object to a deterministic canonical metric.",
                    )
                )
            referenced_column = next(
                (
                    column
                    for (table_name, column_name), column in source_columns.items()
                    if column_name == name.casefold()
                    or re.search(rf"\b{re.escape(column_name)}\b", expression, re.IGNORECASE)
                ),
                None,
            )
            if role == "facts" and referenced_column is not None:
                column_name = str(referenced_column.get("name") or "")
                data_type = str(referenced_column.get("data_type") or "").casefold()
                category: FailureCategory | None = None
                evidence: tuple[str, ...] = ()
                if data_type in {"string", "text"}:
                    category = FailureCategory.TYPE_MISMATCH
                    evidence = (f"Text column {column_name!r} was emitted as a fact.",)
                elif re.search(r"(?:^|_)(?:id|key)$", column_name, re.IGNORECASE):
                    category = FailureCategory.SEMANTIC_ROLE_MISMATCH
                    evidence = (f"Identifier column {column_name!r} was emitted as an additive fact.",)
                if category is not None:
                    findings.append(
                        ConversionFinding.create(
                            rule_id="LINT_AUTOPILOT_FACT_ROLE",
                            category=category,
                            severity=FindingSeverity.BLOCKING,
                            source_object_id=source_id,
                            source_object=source_object,
                            source_expression=None,
                            source_support="AUTOPILOT_EVIDENCE",
                            generated_object=name,
                            generated_expression=expression or None,
                            evidence=evidence,
                            recommended_correction="Use the canonical key/dimension role and exclude the generated fact mapping.",
                        )
                    )
    return findings


def build_conversion_findings(
    run: Any,
    *,
    autopilot: Mapping[str, Any] | None = None,
) -> tuple[ConversionFinding, ...]:
    measures = _source_measures(run)
    columns = _source_columns(run)
    findings: list[ConversionFinding] = []
    for candidate in run.classifications:
        measure = measures.get(str(candidate["source_object_id"]), {})
        findings.append(_finding_for_candidate(candidate, measure))
        findings.extend(_lint_candidate(candidate, measure, columns))
    for item in run.relationship_findings:
        if item.get("finding_type") == "EXACT_MATCH" or item.get("informational"):
            continue
        relationship_id = str(item.get("relationship_id") or item.get("code") or "relationship")
        findings.append(
            ConversionFinding.create(
                rule_id="LINT_RELATIONSHIP_DRIFT",
                category=FailureCategory.RELATIONSHIP_MISMATCH,
                severity=FindingSeverity.BLOCKING,
                source_object_id=relationship_id,
                source_object=str(item.get("canonical_relationship") or relationship_id),
                source_expression=None,
                source_support=str(item.get("finding_type") or "MANUAL_REVIEW_REQUIRED"),
                generated_object=str(item.get("relationship_id")) if item.get("relationship_id") else None,
                generated_expression=None,
                evidence=(str(item.get("code")), str(item.get("message"))),
                recommended_correction="Resolve the relationship against an exact canonical relationship or explicitly exclude the dependent object.",
            )
        )
    if autopilot is not None:
        findings.extend(_autopilot_findings(run, autopilot))
    unique = {item.finding_id: item for item in findings}
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                list(FindingSeverity).index(item.severity),
                item.source_object.casefold(),
                item.rule_id,
                item.finding_id,
            ),
        )
    )


def _evidence_value(value: Any, label: str) -> tuple[str, Decimal | None]:
    if not isinstance(value, Mapping) or set(value) not in ({"status"}, {"status", "value"}):
        raise ValueError(f"{label} result must contain strict status/value fields.")
    status = value.get("status")
    if status == "NOT_AVAILABLE" and set(value) == {"status"}:
        return "NOT_AVAILABLE", None
    if status != "AVAILABLE" or set(value) != {"status", "value"}:
        raise ValueError(f"{label} result status/value combination is invalid.")
    if not isinstance(value.get("value"), str):
        raise ValueError(f"{label} result value must be a decimal string.")
    try:
        numeric = Decimal(value["value"])
    except InvalidOperation as exc:
        raise ValueError(f"{label} result value must be a decimal string.") from exc
    if not numeric.is_finite():
        raise ValueError(f"{label} result value must be finite.")
    return "AVAILABLE", numeric


def _metric_signature_sha256(metric: Any) -> str:
    return _stable_hash(
        {
            "canonical_metric": metric.canonical_name,
            "pattern": metric.pattern.value if metric.pattern else None,
            "aggregation": metric.aggregation.value if metric.aggregation else None,
            "source_semantic_model": metric.source_semantic_model,
            "source_entity": metric.source_entity,
            "source_logical_table": metric.source_logical_table,
            "source_physical_table": metric.source_physical_table,
            "source_field": metric.source_field,
            "scale_divisor": metric.scale_divisor,
            "filters": [
                {"field": item.field, "operator": item.operator.value, "value": item.value}
                for item in metric.filters
            ],
            "numerator": metric.numerator,
            "denominator": metric.denominator,
        }
    )


def _load_result_evidence(path: str | Path | None, run: Any) -> tuple[str, str | None]:
    if path is None:
        return "NOT_AVAILABLE", None
    resolved = _safe_repository_file(path, label="Result evidence")
    payload = resolved.read_bytes()
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Result evidence must be UTF-8 JSON.") from exc
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "source_snapshot_sha256",
        "canonical_sha256",
        "status",
        "results",
    }:
        raise ValueError("Result evidence must match the strict schema-v2 shape.")
    if value["schema_version"] != 2 or value["status"] not in {"PASSED", "FAILED", "NOT_AVAILABLE"}:
        raise ValueError("Result evidence schema_version/status is invalid.")
    if value["source_snapshot_sha256"] != run.source_snapshot_hash:
        raise ValueError("Result evidence is not bound to the imported Power BI snapshot.")
    if value["canonical_sha256"] != _sha256(DBT_SEMANTIC_YAML.read_bytes()):
        raise ValueError("Result evidence is not bound to the current canonical contract.")
    if not isinstance(value["results"], list):
        raise ValueError("Result evidence results must be an array.")
    canonical_index = build_canonical_metric_ir_index(
        load_yaml(DBT_SEMANTIC_YAML),
        canonical_source="models/semantic/triathlon_semantic.yml",
    )
    actual_status = "PASSED"
    seen: set[str] = set()
    for index, result in enumerate(value["results"]):
        expected = {
            "canonical_metric",
            "metric_signature_sha256",
            "comparison",
            "power_bi",
            "repository_snowflake",
            "autopilot",
        }
        if not isinstance(result, Mapping) or set(result) != expected:
            raise ValueError(f"Result evidence item {index} has unexpected fields.")
        metric = str(result.get("canonical_metric") or "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", metric) or metric in seen:
            raise ValueError("Result evidence canonical metrics must be unique exact identifiers.")
        seen.add(metric)
        if not SHA256_PATTERN.fullmatch(str(result.get("metric_signature_sha256") or "")):
            raise ValueError("Result evidence metric signature hash is invalid.")
        metric_ir = canonical_index.get(metric)
        if (
            metric_ir is None
            or metric_ir.support is not SupportClassification.SUPPORTED_PATTERN
            or result["metric_signature_sha256"] != _metric_signature_sha256(metric_ir)
        ):
            raise ValueError("Result evidence metric signature is stale or unsupported.")
        comparison = result.get("comparison")
        if comparison not in {"INTEGER_EXACT", "DECIMAL_1E-9"}:
            raise ValueError("Result evidence comparison must be INTEGER_EXACT or DECIMAL_1E-9.")
        power_status, power_value = _evidence_value(result["power_bi"], "Power BI")
        snowflake_status, snowflake_value = _evidence_value(
            result["repository_snowflake"], "Repository Snowflake"
        )
        autopilot_status, autopilot_value = _evidence_value(result["autopilot"], "Autopilot")
        if power_status == "NOT_AVAILABLE" or snowflake_status == "NOT_AVAILABLE":
            if actual_status != "FAILED":
                actual_status = "NOT_AVAILABLE"
            continue
        assert power_value is not None and snowflake_value is not None
        if comparison == "INTEGER_EXACT":
            matches = (
                power_value == power_value.to_integral_value()
                and snowflake_value == snowflake_value.to_integral_value()
                and power_value == snowflake_value
            )
        else:
            difference = abs(power_value - snowflake_value)
            matches = difference <= max(
                Decimal("1e-9"),
                Decimal("1e-9") * max(abs(power_value), abs(snowflake_value)),
            )
        if autopilot_status == "AVAILABLE":
            assert autopilot_value is not None
            if comparison == "INTEGER_EXACT":
                matches = matches and autopilot_value == snowflake_value
            else:
                difference = abs(autopilot_value - snowflake_value)
                matches = matches and difference <= max(
                    Decimal("1e-9"),
                    Decimal("1e-9") * max(abs(autopilot_value), abs(snowflake_value)),
                )
        if not matches:
            actual_status = "FAILED"
    if not value["results"]:
        actual_status = "NOT_AVAILABLE"
    if value["status"] != actual_status:
        raise ValueError(
            f"Result evidence declared {value['status']} but deterministic comparison is {actual_status}."
        )
    return actual_status, _sha256(payload)


def _load_autopilot(path: str | Path | None) -> tuple[Mapping[str, Any] | None, str, str | None]:
    if path is None:
        return None, "NOT_AVAILABLE", None
    resolved = _safe_repository_file(path, label="Autopilot YAML")
    payload = resolved.read_bytes()
    try:
        value = yaml.safe_load(payload.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError("Autopilot evidence must be UTF-8 YAML.") from exc
    if not isinstance(value, Mapping):
        raise ValueError("Autopilot YAML must contain a mapping.")
    metadata = value.get("benchmark_metadata") or {}
    status = (
        "SYNTHETIC_TEST_FIXTURE"
        if isinstance(metadata, Mapping) and metadata.get("evidence_kind") == "SYNTHETIC_TEST_FIXTURE"
        else "EXPORTED_YAML"
    )
    return value, status, _sha256(payload)


def _render_benchmark_json(review: ConversionReview, run: Any) -> str:
    value = {
        "schema_version": 1,
        "import_id": review.import_id,
        "source_snapshot_sha256": review.source_snapshot_sha256,
        "canonical_sha256": review.canonical_sha256,
        "autopilot_status": review.autopilot_status,
        "result_evidence_status": review.result_evidence_status,
        "inventory": run.inventory,
        "recognized_semantic_patterns": [
            {
                "source_object_id": item.get("source_object_id"),
                "source_object": f"{item.get('source_table')}[{item.get('source_measure')}]",
                "pattern": item.get("recognized_pattern"),
                "classification": item.get("classification"),
            }
            for item in run.classifications
        ],
        "repository_candidate_ir": [
            item.get("candidate_ir") for item in run.classifications if item.get("candidate_ir")
        ],
        "repository_snowflake_definitions": [
            item.get("regenerated_snowflake")
            for item in run.classifications
            if item.get("regenerated_snowflake")
        ],
        "findings": [item.to_dict() for item in review.findings],
    }
    return canonical_json_text(value, pretty=True)


def _render_benchmark_md(review: ConversionReview, run: Any) -> str:
    counts = {value.value: 0 for value in FailureCategory}
    for finding in review.findings:
        counts[finding.category.value] += 1
    lines = [
        f"# Conversion benchmark: {run.source_model_id}",
        "",
        f"- Import ID: `{review.import_id}`",
        f"- Autopilot evidence: `{review.autopilot_status}`",
        f"- Golden result evidence: `{review.result_evidence_status}`",
        f"- Blocking findings: {sum(item.severity is FindingSeverity.BLOCKING for item in review.findings)}",
        "",
        "## Failure taxonomy",
        "",
        "| Category | Count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| `{name}` | {counts[name]} |" for name in sorted(counts))
    lines.extend(
        [
            "",
            "## Object comparison",
            "",
            "| Finding | Severity | Category | Source | Generated |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in review.findings:
        source_object = item.source_object.replace("|", "\\|")
        generated_object = (item.generated_object or "—").replace("|", "\\|")
        lines.append(
            f"| `{item.finding_id}` | `{item.severity.value}` | `{item.category.value}` | "
            f"{source_object} | {generated_object} |"
        )
    return "\n".join(lines) + "\n"


def _lint_findings(review: ConversionReview) -> tuple[ConversionFinding, ...]:
    return tuple(item for item in review.findings if item.rule_id.startswith("LINT_"))


def _render_lint_json(review: ConversionReview) -> str:
    return canonical_json_text(
        {
            "schema_version": 1,
            "import_id": review.import_id,
            "findings": [item.to_dict() for item in _lint_findings(review)],
        },
        pretty=True,
    )


def _render_lint_md(review: ConversionReview) -> str:
    findings = _lint_findings(review)
    lines = [
        "# Semantic lint report",
        "",
        f"Findings: {len(findings)}",
        "",
        "| Rule | Severity | Category | Source | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in findings:
        evidence = "; ".join(item.evidence).replace("|", "\\|")
        source_object = item.source_object.replace("|", "\\|")
        lines.append(
            f"| `{item.rule_id}` | `{item.severity.value}` | `{item.category.value}` | "
            f"{source_object} | {evidence} |"
        )
    return "\n".join(lines) + "\n"


def _render_autopilot_md(review: ConversionReview) -> str:
    findings = [
        item
        for item in review.findings
        if "AUTOPILOT" in item.rule_id or item.source_support == "AUTOPILOT_EVIDENCE"
    ]
    lines = [
        "# Snowflake Autopilot comparison",
        "",
        f"Evidence status: `{review.autopilot_status}`",
        "",
    ]
    if review.autopilot_status == "NOT_AVAILABLE":
        lines.append("No exported Autopilot YAML was supplied; no equivalence claim is made.")
    else:
        lines.extend(
            [
                "| Finding | Severity | Category | Source |",
                "| --- | --- | --- | --- |",
            ]
        )
        lines.extend(
            f"| `{item.finding_id}` | `{item.severity.value}` | `{item.category.value}` | {item.source_object} |"
            for item in findings
        )
    return "\n".join(lines) + "\n"


def _render_decisions(review: ConversionReview) -> str:
    return canonical_json_text(
        {
            "schema_version": 1,
            "import_id": review.import_id,
            "state": review.state.value,
            "decisions": [item.to_dict() for item in review.decisions],
            "unresolved_blocking_findings": [
                item.finding_id for item in review.unresolved_blockers
            ],
            "unaccepted_exact_mappings": [
                item.finding_id for item in review.unaccepted_exact_matches
            ],
        },
        pretty=True,
    )


def _review_artifacts(review: ConversionReview, run: Any) -> dict[str, str]:
    return {
        "conversion-benchmark.json": _render_benchmark_json(review, run),
        "conversion-benchmark.md": _render_benchmark_md(review, run),
        "semantic-lint-report.json": _render_lint_json(review),
        "semantic-lint-report.md": _render_lint_md(review),
        "snowflake-autopilot-comparison.md": _render_autopilot_md(review),
        "human-review-decisions.json": _render_decisions(review),
        "maintenance-equivalence-report.md": (
            "# Maintenance equivalence report\n\n"
            "Status: `NOT_RUN`\n\n"
            "The synchronized maintenance and rollback proof is produced by the fixture benchmark runner.\n"
        ),
    }


def create_conversion_review(
    import_id: str,
    *,
    autopilot_yaml: str | Path | None = None,
    result_evidence: str | Path | None = None,
    import_store: ImportStore | None = None,
    conversion_store: ConversionStore | None = None,
    now: datetime | None = None,
) -> ConversionReview:
    imports = import_store or ImportStore(DEFAULT_IMPORT_DIR)
    store = conversion_store or ConversionStore(imports.root)
    run = imports.load_run(import_id)
    if imports.try_load_discard(import_id) is not None:
        raise ValueError("Discarded imports cannot enter conversion review.")
    if not verify_import_source(run):
        raise ValueError("Power BI source snapshot changed; create a new import run.")
    autopilot, autopilot_status, autopilot_hash = _load_autopilot(autopilot_yaml)
    result_status, result_hash = _load_result_evidence(result_evidence, run)
    findings = build_conversion_findings(run, autopilot=autopilot)
    review = ConversionReview(
        schema_version=CONVERSION_SCHEMA_VERSION,
        import_id=import_id,
        created_at=utc_timestamp(now),
        state=ConversionState.REVIEW_PENDING,
        source_snapshot_sha256=run.source_snapshot_hash,
        import_semantic_sha256=run.semantic_content_hash,
        canonical_sha256=_sha256(DBT_SEMANTIC_YAML.read_bytes()),
        autopilot_status=autopilot_status,
        autopilot_sha256=autopilot_hash,
        result_evidence_status=result_status,
        result_evidence_sha256=result_hash,
        findings=findings,
        decisions=(),
    )
    store.create(review, _review_artifacts(review, run))
    return review


def _record_decision(
    review: ConversionReview,
    finding: ConversionFinding,
    *,
    action: ReviewAction,
    actor: str,
    rationale: str,
    source: Mapping[str, Any],
    canonical_metric: str | None,
    change_id: str | None,
    now: datetime | None,
) -> tuple[ConversionReview, ReviewDecision]:
    if review.state is ConversionState.FINALIZED:
        raise ValueError("Finalized conversions cannot receive decisions.")
    if finding.finding_id in review.decisions_by_finding:
        raise ValueError(f"Finding already has a decision: {finding.finding_id}")
    before = review.semantic_content_sha256
    provisional = {
        "finding_id": finding.finding_id,
        "action": action.value,
        "actor": actor,
        "rationale": rationale,
        "source_object": finding.source_object,
        "source": dict(source),
        "mapping_file_sha256": source.get("sha256"),
        "canonical_metric": canonical_metric,
        "change_id": change_id,
        "before_sha256": before,
        "lifecycle_state": "DECISION_RECORDED",
    }
    after = _stable_hash(provisional)
    decision = ReviewDecision(
        finding_id=finding.finding_id,
        action=action,
        actor=actor,
        timestamp=utc_timestamp(now),
        rationale=rationale,
        source_object=finding.source_object,
        source=dict(source),
        mapping_file_sha256=(str(source["sha256"]) if source.get("sha256") is not None else None),
        canonical_metric=canonical_metric,
        change_id=change_id,
        before_sha256=before,
        after_sha256=after,
        lifecycle_state="DECISION_RECORDED",
    )
    updated = replace(review, decisions=review.decisions + (decision,))
    if not updated.unresolved_blockers and not updated.unaccepted_exact_matches:
        updated = replace(updated, state=ConversionState.READY_TO_FINALIZE)
    return updated, decision


def _require_current_review(store: ConversionStore, review: ConversionReview) -> Any:
    run = ImportStore(store.root).load_run(review.import_id)
    if not verify_import_source(run):
        raise ValueError("Power BI source snapshot changed; create a new import/review.")
    if (
        review.source_snapshot_sha256 != run.source_snapshot_hash
        or review.import_semantic_sha256 != run.semantic_content_hash
    ):
        raise ValueError("Conversion review no longer matches the immutable import record.")
    if review.canonical_sha256 != _sha256(DBT_SEMANTIC_YAML.read_bytes()):
        raise ValueError("Canonical contract changed after review; create a new import/review.")
    return run


def accept_mapping(
    finding_id: str,
    *,
    actor: str = "local-user",
    conversion_store: ConversionStore | None = None,
    now: datetime | None = None,
) -> ReviewDecision:
    store = conversion_store or ConversionStore()
    review, finding = store.find_finding(finding_id)
    if (
        finding.category is not FailureCategory.EXACT_MATCH
        or finding.severity is not FindingSeverity.INFO
        or not finding.canonical_metric
    ):
        raise ValueError("Only an exact, cross-target-valid canonical mapping can be accepted.")
    _require_current_review(store, review)
    index = build_canonical_metric_ir_index(
        load_yaml(DBT_SEMANTIC_YAML),
        canonical_source="models/semantic/triathlon_semantic.yml",
    )
    metric = index.get(finding.canonical_metric)
    if metric is None or metric.support is not SupportClassification.SUPPORTED_PATTERN:
        raise ValueError("Exact finding no longer resolves to a current supported canonical metric.")
    dax = generate_dax_definition(metric, index)
    snowflake = generate_snowflake_definition(metric, index)
    cross_target = validate_cross_target(metric, dax, snowflake)
    if (
        not cross_target.valid
        or snowflake.definition is None
        or snowflake.definition.get("name") != finding.generated_object
        or snowflake.definition.get("expr") != finding.generated_expression
    ):
        raise ValueError("Exact finding no longer matches the current cross-target canonical generation.")
    updated, decision = _record_decision(
        review,
        finding,
        action=ReviewAction.ACCEPT,
        actor=actor,
        rationale="EXACT_MAPPING_CONFIRMED",
        source={"kind": "REPOSITORY_CANONICAL_MAPPING", "finding_id": finding_id},
        canonical_metric=finding.canonical_metric,
        change_id=None,
        now=now,
    )
    store.update(updated, {"human-review-decisions.json": _render_decisions(updated)})
    return decision


def _load_correction(path: str | Path, review: ConversionReview, finding: ConversionFinding) -> tuple[dict[str, Any], str, Path]:
    resolved = _safe_repository_file(path, label="Correction mapping")
    payload = resolved.read_bytes()
    try:
        value = yaml.safe_load(payload.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError("Correction mapping must be UTF-8 JSON or YAML.") from exc
    expected = {
        "schema_version",
        "finding_id",
        "source_snapshot_sha256",
        "rationale",
        "resolution",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("Correction mapping must match the strict schema-v1 shape.")
    if value["schema_version"] != 1 or value["finding_id"] != finding.finding_id:
        raise ValueError("Correction mapping schema version or finding ID is invalid.")
    if value["source_snapshot_sha256"] != review.source_snapshot_sha256:
        raise ValueError("Correction mapping is not bound to the current Power BI snapshot.")
    _required_text(value["rationale"], "rationale")
    resolution = value["resolution"]
    shapes = (
        {"kind", "canonical_metric"},
        {"kind", "canonical_metric", "change_id"},
        {"kind", "change_request"},
    )
    if not isinstance(resolution, Mapping) or set(resolution) not in shapes:
        raise ValueError(
            "Correction resolution must contain an exact canonical metric or a typed canonical change request."
        )
    if resolution.get("kind") not in {
        "EXISTING_CANONICAL_METRIC",
        "VALIDATED_CHANGE",
        "TYPED_CANONICAL_CHANGE",
    }:
        raise ValueError("Correction resolution kind is unsupported.")
    if resolution.get("kind") == "TYPED_CANONICAL_CHANGE":
        if set(resolution) != {"kind", "change_request"}:
            raise ValueError("TYPED_CANONICAL_CHANGE accepts only a strict change_request object.")
        try:
            MetricChangeRequest.from_dict(resolution["change_request"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid typed canonical change request: {exc}") from exc
    elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(resolution.get("canonical_metric") or "")):
        raise ValueError("Correction canonical_metric must be an exact safe identifier.")
    if resolution.get("kind") == "VALIDATED_CHANGE" and not resolution.get("change_id"):
        raise ValueError("VALIDATED_CHANGE requires change_id.")
    return dict(value), _sha256(payload), resolved


def correct_mapping(
    finding_id: str,
    *,
    mapping_file: str | Path,
    actor: str = "local-user",
    conversion_store: ConversionStore | None = None,
    change_store: ChangeStore | None = None,
    now: datetime | None = None,
) -> ReviewDecision:
    store = conversion_store or ConversionStore()
    review, finding = store.find_finding(finding_id)
    _require_current_review(store, review)
    mapping, mapping_hash, resolved = _load_correction(mapping_file, review, finding)
    resolution = mapping["resolution"]
    changes = change_store or ChangeStore(DEFAULT_CHANGE_DIR)
    if resolution["kind"] == "TYPED_CANONICAL_CHANGE":
        request = MetricChangeRequest.from_dict(resolution["change_request"])
        try:
            proposal = propose_change(request)
        except ProposalInputError as exc:
            raise ValueError(f"Typed correction could not create a proposal: {exc}") from exc
        if (
            proposal.status is not ProposalStatus.PROPOSED
            or not proposal.cross_target_valid
            or proposal.canonical_metric is None
        ):
            raise ValueError("Typed correction did not produce an applicable cross-target-valid proposal.")
        changes.save_proposal(proposal)
        canonical_metric = proposal.canonical_metric
        change_id = proposal.change_id
    else:
        canonical_metric = str(resolution["canonical_metric"])
        canonical = load_yaml(DBT_SEMANTIC_YAML)
        ir_index = build_canonical_metric_ir_index(
            canonical,
            canonical_source="models/semantic/triathlon_semantic.yml",
        )
        metric_ir = ir_index.get(canonical_metric)
        if metric_ir is None:
            raise ValueError(f"Canonical metric does not exist exactly: {canonical_metric}")
        generated = generate_snowflake_definition(metric_ir, ir_index)
        if generated.support is not SupportClassification.SUPPORTED_PATTERN or generated.definition is None:
            raise ValueError("Corrected canonical metric is not deterministically supported.")
        change_id = resolution.get("change_id")
        if change_id is not None:
            proposal = changes.load_proposal(str(change_id))
            if proposal.status is not ProposalStatus.VALIDATED or proposal.canonical_metric != canonical_metric:
                raise ValueError("Correction change_id must identify a validated proposal for the same canonical metric.")
    updated, decision = _record_decision(
        review,
        finding,
        action=ReviewAction.CORRECT,
        actor=actor,
        rationale=str(mapping["rationale"]),
        source={
            "kind": "STRUCTURED_CORRECTION_MAPPING",
            "path": resolved.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": mapping_hash,
        },
        canonical_metric=canonical_metric,
        change_id=str(change_id) if change_id is not None else None,
        now=now,
    )
    store.update(updated, {"human-review-decisions.json": _render_decisions(updated)})
    return decision


def reject_mapping(
    finding_id: str,
    *,
    rationale: str,
    actor: str = "local-user",
    conversion_store: ConversionStore | None = None,
    now: datetime | None = None,
) -> ReviewDecision:
    store = conversion_store or ConversionStore()
    review, finding = store.find_finding(finding_id)
    _require_current_review(store, review)
    _required_text(rationale, "rationale")
    findings = review.findings
    if finding.source_support in SUPPORTED_IMPORT_PREFIXES:
        omission = ConversionFinding.create(
            rule_id="LINT_REJECTED_SUPPORTED_SOURCE",
            category=FailureCategory.OMITTED_OBJECT,
            severity=FindingSeverity.BLOCKING,
            source_object_id=finding.source_object_id,
            source_object=finding.source_object,
            source_expression=finding.source_expression,
            source_support=finding.source_support,
            generated_object=None,
            generated_expression=None,
            evidence=("A supported source object was rejected from the accepted conversion.",),
            recommended_correction="Correct the supported object to an exact canonical mapping before finalization.",
            canonical_metric=finding.canonical_metric,
        )
        if omission.finding_id not in {item.finding_id for item in findings}:
            findings = findings + (omission,)
            review = replace(review, findings=findings)
    updated, decision = _record_decision(
        review,
        finding,
        action=ReviewAction.REJECT,
        actor=actor,
        rationale=rationale,
        source={"kind": "EXPLICIT_EXCLUSION", "finding_id": finding_id},
        canonical_metric=None,
        change_id=None,
        now=now,
    )
    store.update(
        updated,
        {
            "human-review-decisions.json": _render_decisions(updated),
        },
    )
    return decision


def finalize_conversion(
    import_id: str,
    *,
    import_store: ImportStore | None = None,
    conversion_store: ConversionStore | None = None,
    change_store: ChangeStore | None = None,
) -> ConversionReview:
    imports = import_store or ImportStore(DEFAULT_IMPORT_DIR)
    store = conversion_store or ConversionStore(imports.root)
    run = imports.load_run(import_id)
    review = store.load(import_id)
    if review.state is ConversionState.FINALIZED:
        raise ValueError("Conversion is already finalized.")
    if not verify_import_source(run):
        raise ValueError("Power BI source snapshot changed; finalization is refused.")
    if review.source_snapshot_sha256 != run.source_snapshot_hash or review.import_semantic_sha256 != run.semantic_content_hash:
        raise ValueError("Conversion review no longer matches the immutable import record.")
    current_canonical_hash = _sha256(DBT_SEMANTIC_YAML.read_bytes())
    if review.unresolved_blockers:
        raise ValueError(
            "Blocking conversion findings remain unresolved: "
            + ", ".join(item.finding_id for item in review.unresolved_blockers)
        )
    if review.unaccepted_exact_matches:
        raise ValueError(
            "Exact mappings still require explicit acceptance: "
            + ", ".join(item.finding_id for item in review.unaccepted_exact_matches)
        )
    if review.result_evidence_status != "PASSED":
        raise ValueError("Passing hash-bound golden result evidence is required before finalization.")
    accepted_canonical_hashes = {review.canonical_sha256}
    current_ir = build_canonical_metric_ir_index(
        load_yaml(DBT_SEMANTIC_YAML),
        canonical_source="models/semantic/triathlon_semantic.yml",
    )
    changes = change_store or ChangeStore(DEFAULT_CHANGE_DIR)
    for decision in review.decisions:
        if decision.change_id:
            proposal = changes.load_proposal(decision.change_id)
            if proposal.status is not ProposalStatus.VALIDATED:
                raise ValueError(f"Correction proposal is not validated: {decision.change_id}")
            applied_hash = proposal.applied_hashes.get("models/semantic/triathlon_semantic.yml")
            if isinstance(applied_hash, str):
                accepted_canonical_hashes.add(applied_hash)
        if decision.action in {ReviewAction.ACCEPT, ReviewAction.CORRECT} and decision.canonical_metric:
            accepted_metric = current_ir.get(decision.canonical_metric)
            if accepted_metric is None or accepted_metric.support is not SupportClassification.SUPPORTED_PATTERN:
                raise ValueError(
                    f"Accepted canonical metric is absent or no longer supported: {decision.canonical_metric}"
                )
    if current_canonical_hash not in accepted_canonical_hashes:
        raise ValueError("Canonical contract changed outside the reviewed and validated correction proposals.")
    if not SNOWFLAKE_OUTPUT.is_file():
        raise ValueError("Deterministic Snowflake output is missing; run the governed generation workflow.")
    accepted = SNOWFLAKE_OUTPUT.read_bytes()
    try:
        accepted_yaml = yaml.safe_load(accepted) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Deterministic Snowflake output is invalid: {exc}") from exc
    accepted_metrics: dict[str, Mapping[str, Any]] = {}
    for table in accepted_yaml.get("tables", []) or []:
        if isinstance(table, Mapping):
            for metric in table.get("metrics", []) or []:
                if isinstance(metric, Mapping) and isinstance(metric.get("name"), str):
                    accepted_metrics[str(metric["name"])] = metric
    for metric in accepted_yaml.get("metrics", []) or []:
        if isinstance(metric, Mapping) and isinstance(metric.get("name"), str):
            accepted_metrics[str(metric["name"])] = metric
    included_canonical_metrics = {
        decision.canonical_metric
        for decision in review.decisions
        if decision.action in {ReviewAction.ACCEPT, ReviewAction.CORRECT}
        and decision.canonical_metric is not None
    }
    for canonical_metric in sorted(included_canonical_metrics):
        metric_ir = current_ir[canonical_metric]
        generated = generate_snowflake_definition(metric_ir, current_ir)
        if generated.support is not SupportClassification.SUPPORTED_PATTERN or generated.definition is None:
            continue
        name = str(generated.definition["name"])
        actual = accepted_metrics.get(name)
        if actual is None or any(actual.get(key) != value for key, value in generated.definition.items()):
            raise ValueError(
                f"Deterministic Snowflake output is stale for canonical metric: {metric_ir.canonical_name}"
            )
    accepted_hash = _sha256(accepted)
    finalized = replace(
        review,
        state=ConversionState.FINALIZED,
        accepted_output_sha256=accepted_hash,
    )
    store.update(
        finalized,
        {
            "accepted-snowflake-semantic-view.yml": accepted,
            "human-review-decisions.json": _render_decisions(finalized),
        },
    )
    return finalized


__all__ = [
    "CONVERSION_SCHEMA_VERSION",
    "ConversionFinding",
    "ConversionReview",
    "ConversionState",
    "ConversionStore",
    "ConversionStoreError",
    "FailureCategory",
    "FindingSeverity",
    "ReviewAction",
    "ReviewDecision",
    "accept_mapping",
    "build_conversion_findings",
    "correct_mapping",
    "create_conversion_review",
    "finalize_conversion",
    "reject_mapping",
]
