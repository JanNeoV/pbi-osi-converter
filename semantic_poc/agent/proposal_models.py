from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from .schemas import (
    ApprovalState,
    CANONICAL_FILE,
    DeploymentState,
    MetricChangeRequest,
    TargetSupport,
    ValidationState,
    validate_change_id,
    validate_timestamp,
)


PROPOSAL_SCHEMA_VERSION = 3


class ProposalSource(str, Enum):
    STRUCTURED_REQUEST = "STRUCTURED_REQUEST"
    POWERBI_IMPORT = "POWERBI_IMPORT"


IMPORT_REQUEST_SCHEMA_VERSION = 1
IMPORT_ID_PATTERN = re.compile(r"^imp_\d{8}T\d{6}Z_[0-9a-f]{8}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IMPORT_AUTHORITY_STATES = frozenset(
    {"POWER_BI_DISCOVERED", "CANONICALIZATION_PROPOSED", "CANONICAL_CONTRACT_ACCEPTED"}
)
IMPORT_PROVENANCE_FIELDS = {
    "import_run_id",
    "source_model_path",
    "source_model_id",
    "source_object_path",
    "source_object_id",
    "source_snapshot_sha256",
    "authority_state",
    "extraction_version",
    "semantic_content_sha256",
}
IMPORT_REQUEST_FIELDS = {
    "schema_version",
    "change_id",
    "created_at",
    "requested_action",
    "import_run_id",
    "source_model_path",
    "source_model_id",
    "source_object_path",
    "source_object_id",
}


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    NO_OP = "NO_OP"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    APPLIED_LOCAL = "APPLIED_LOCAL"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    DISCARDED = "DISCARDED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class LocalApplicationState(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    APPLIED = "APPLIED"
    PROTECTED = "PROTECTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True)
class ProposalDiagnostic:
    code: str
    message: str
    severity: str = "ERROR"
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "message": self.message, "severity": self.severity}
        if self.target is not None:
            result["target"] = self.target
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalDiagnostic":
        expected = {"code", "message", "severity"} | ({"target"} if "target" in data else set())
        _strict_fields(data, expected, "proposal diagnostic")
        if not all(isinstance(data[key], str) and data[key] for key in ("code", "message", "severity")):
            raise ValueError("Proposal diagnostic fields must be non-empty strings.")
        target = data.get("target")
        if target is not None and not isinstance(target, str):
            raise ValueError("Proposal diagnostic target must be a string or null.")
        return cls(data["code"], data["message"], data["severity"], target)


def _strict_fields(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a JSON object.")
    missing = expected - set(data)
    extra = set(data) - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected: " + ", ".join(sorted(extra)))
        raise ValueError(f"Invalid {label} fields (" + "; ".join(details) + ").")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object.")
    return MappingProxyType(dict(value))


def _thaw_json(value: Any) -> Any:
    """Recursively convert immutable/nested proposal values to JSON containers."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def proposal_semantic_content_sha256(proposal: "ProposalRecord") -> str:
    """Hash semantic proposal content while excluding identity and lifecycle state."""

    payload = {
        "proposal_source": ProposalSource(proposal.proposal_source).value,
        "intent": proposal.intent,
        "mode": proposal.mode,
        "operation": _thaw_json(proposal.operation),
        "resolution": _thaw_json(proposal.resolution),
        "canonical_metric": proposal.canonical_metric,
        "canonical_file": proposal.canonical_file,
        "source_snapshot": _thaw_json(proposal.source_snapshot),
        "current_ir": _thaw_json(proposal.current_ir),
        "proposed_ir": _thaw_json(proposal.proposed_ir),
        "canonical_patch": _thaw_json(proposal.canonical_patch),
        "canonical_diff": proposal.canonical_diff,
        "current_dax": proposal.current_dax,
        "proposed_dax": proposal.proposed_dax,
        "dax_diff": proposal.dax_diff,
        "current_snowflake": _thaw_json(proposal.current_snowflake),
        "proposed_snowflake": _thaw_json(proposal.proposed_snowflake),
        "snowflake_diff": proposal.snowflake_diff,
        "target_support": _thaw_json(proposal.target_support),
        "cross_target_valid": proposal.cross_target_valid,
        "assumptions": list(proposal.assumptions),
        "diagnostics": [item.to_dict() for item in proposal.diagnostics],
        "risk_level": proposal.risk_level.value,
        "required_validation": list(proposal.required_validation),
        "canonical_application_available": proposal.canonical_application_available,
        "source_model_id": proposal.source_model_id,
        "source_object_path": proposal.source_object_path,
        "source_object_id": proposal.source_object_id,
        "source_snapshot_sha256": proposal.source_snapshot_sha256,
        "extraction_version": proposal.extraction_version,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping_tuple(value: Any, label: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be an array of JSON objects.")
    return tuple(_mapping(item, label) for item in value)


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be an array of strings.")
    result = tuple(value)
    if any(not isinstance(item, str) for item in result):
        raise ValueError(f"{label} must be an array of strings.")
    return result


def _safe_import_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError(f"{label} must be a non-empty bounded string.")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{label} must not contain control characters.")
    return value


def _safe_import_path(value: Any, label: str) -> str:
    result = _safe_import_text(value, label)
    if "\\" in result or result.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", result):
        raise ValueError(f"{label} must be a repository-relative POSIX path.")
    if any(part in {"", ".", ".."} for part in result.split("/")):
        raise ValueError(f"{label} must not contain empty or traversal path components.")
    return result


def _validate_import_request(data: Mapping[str, Any]) -> Mapping[str, Any]:
    _strict_fields(data, IMPORT_REQUEST_FIELDS, "Power BI import proposal request")
    if data["schema_version"] != IMPORT_REQUEST_SCHEMA_VERSION:
        raise ValueError(f"Power BI import request schema_version must be {IMPORT_REQUEST_SCHEMA_VERSION}.")
    validate_change_id(data["change_id"])
    validate_timestamp(data["created_at"])
    if data["requested_action"] != "CANONICALIZE_METRIC":
        raise ValueError("Power BI import requested_action must be CANONICALIZE_METRIC.")
    if not isinstance(data["import_run_id"], str) or not IMPORT_ID_PATTERN.fullmatch(data["import_run_id"]):
        raise ValueError("Power BI import request has an invalid import_run_id.")
    _safe_import_path(data["source_model_path"], "source_model_path")
    _safe_import_text(data["source_model_id"], "source_model_id")
    _safe_import_path(data["source_object_path"], "source_object_path")
    _safe_import_text(data["source_object_id"], "source_object_id")
    return MappingProxyType(dict(data))


@dataclass(frozen=True)
class ProposalRecord:
    schema_version: int
    change_id: str
    created_at: str
    original_request: Mapping[str, Any]
    intent: str
    mode: str
    operation: Mapping[str, Any]
    resolution: Mapping[str, Any]
    canonical_metric: str | None
    canonical_file: str
    source_snapshot: Mapping[str, Any]
    source_snapshot_hash: str
    current_ir: Mapping[str, Any] | None
    proposed_ir: Mapping[str, Any] | None
    canonical_patch: tuple[Mapping[str, Any], ...]
    canonical_diff: str
    current_dax: str | None
    proposed_dax: str | None
    dax_diff: str
    current_snowflake: Mapping[str, Any] | None
    proposed_snowflake: Mapping[str, Any] | None
    snowflake_diff: str
    target_support: Mapping[str, str]
    cross_target_valid: bool
    assumptions: tuple[str, ...]
    diagnostics: tuple[ProposalDiagnostic, ...]
    risk_level: RiskLevel
    required_validation: tuple[str, ...]
    status: ProposalStatus
    approval_state: ApprovalState
    validation_state: ValidationState
    local_application_state: LocalApplicationState
    deployment_state: DeploymentState
    canonical_application_available: bool = False
    audit_events: tuple[Mapping[str, Any], ...] = ()
    planned_hashes: Mapping[str, Any] = field(default_factory=dict)
    applied_hashes: Mapping[str, Any] = field(default_factory=dict)
    backup: Mapping[str, Any] = field(default_factory=dict)
    powerbi_copy: Mapping[str, Any] = field(default_factory=dict)
    validation_results: Mapping[str, Any] = field(default_factory=dict)
    proposal_source: ProposalSource = ProposalSource.STRUCTURED_REQUEST
    import_run_id: str | None = None
    source_model_path: str | None = None
    source_model_id: str | None = None
    source_object_path: str | None = None
    source_object_id: str | None = None
    source_snapshot_sha256: str | None = None
    authority_state: str | None = None
    extraction_version: str | None = None
    semantic_content_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2, PROPOSAL_SCHEMA_VERSION}:
            raise ValueError(f"proposal schema_version must be 1, 2, or {PROPOSAL_SCHEMA_VERSION}.")
        validate_change_id(self.change_id)
        validate_timestamp(self.created_at)
        if self.canonical_file != CANONICAL_FILE:
            raise ValueError(f"canonical_file must be {CANONICAL_FILE!r}.")
        if self.mode != "PROPOSE":
            raise ValueError("Proposal mode must be PROPOSE.")
        if not isinstance(self.intent, str) or not self.intent:
            raise ValueError("Proposal intent must be a non-empty string.")
        if self.canonical_metric is not None and (not isinstance(self.canonical_metric, str) or not self.canonical_metric):
            raise ValueError("canonical_metric must be a non-empty string or null.")
        if not isinstance(self.source_snapshot_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", self.source_snapshot_hash):
            raise ValueError("source_snapshot_hash must be a lowercase SHA-256 value.")
        normalized_snapshot = _mapping(self.source_snapshot, "source_snapshot")
        if normalized_snapshot.get("aggregate_sha256") != self.source_snapshot_hash:
            raise ValueError("source_snapshot_hash must match the source snapshot aggregate.")
        for name in ("canonical_diff", "dax_diff", "snowflake_diff"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a string.")
        for name in ("current_dax", "proposed_dax"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or null.")
        if type(self.cross_target_valid) is not bool:
            raise ValueError("cross_target_valid must be boolean.")
        if type(self.canonical_application_available) is not bool:
            raise ValueError("canonical_application_available must be boolean.")
        original_request = _mapping(self.original_request, "original_request")
        try:
            proposal_source = ProposalSource(self.proposal_source)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported proposal_source: {self.proposal_source!r}.") from exc
        if proposal_source is ProposalSource.STRUCTURED_REQUEST:
            request = MetricChangeRequest.from_dict(original_request)
            import_values = (
                self.import_run_id,
                self.source_model_path,
                self.source_model_id,
                self.source_object_path,
                self.source_object_id,
                self.source_snapshot_sha256,
                self.authority_state,
                self.extraction_version,
                self.semantic_content_sha256,
            )
            if any(value is not None for value in import_values):
                raise ValueError("Structured proposals must not contain Power BI import provenance.")
        else:
            if self.schema_version != PROPOSAL_SCHEMA_VERSION:
                raise ValueError("Power BI import proposals require the current proposal schema version.")
            request = _validate_import_request(original_request)
            if not isinstance(self.import_run_id, str) or not IMPORT_ID_PATTERN.fullmatch(self.import_run_id):
                raise ValueError("Power BI import proposal has an invalid import_run_id.")
            _safe_import_path(self.source_model_path, "source_model_path")
            _safe_import_text(self.source_model_id, "source_model_id")
            _safe_import_path(self.source_object_path, "source_object_path")
            _safe_import_text(self.source_object_id, "source_object_id")
            if not isinstance(self.source_snapshot_sha256, str) or not SHA256_PATTERN.fullmatch(
                self.source_snapshot_sha256
            ):
                raise ValueError("source_snapshot_sha256 must be a lowercase SHA-256 value.")
            if self.semantic_content_sha256 is not None and (
                not isinstance(self.semantic_content_sha256, str)
                or not SHA256_PATTERN.fullmatch(self.semantic_content_sha256)
            ):
                raise ValueError("semantic_content_sha256 must be a lowercase SHA-256 value.")
            if self.authority_state not in IMPORT_AUTHORITY_STATES:
                raise ValueError("Power BI import authority_state is invalid.")
            _safe_import_text(self.extraction_version, "extraction_version")
            for name in (
                "import_run_id",
                "source_model_path",
                "source_model_id",
                "source_object_path",
                "source_object_id",
            ):
                if request[name] != getattr(self, name):
                    raise ValueError(f"Power BI import request and provenance disagree for {name}.")
        request_change_id = request["change_id"] if isinstance(request, Mapping) else request.change_id
        request_created_at = request["created_at"] if isinstance(request, Mapping) else request.created_at
        if request_change_id != self.change_id or request_created_at != self.created_at:
            raise ValueError("Proposal identity must match the original request.")
        object.__setattr__(self, "proposal_source", proposal_source)
        object.__setattr__(self, "original_request", original_request)
        object.__setattr__(self, "operation", _mapping(self.operation, "operation"))
        object.__setattr__(self, "resolution", _mapping(self.resolution, "resolution"))
        object.__setattr__(self, "source_snapshot", normalized_snapshot)
        object.__setattr__(self, "canonical_patch", _mapping_tuple(self.canonical_patch, "canonical_patch"))
        object.__setattr__(self, "current_ir", None if self.current_ir is None else _mapping(self.current_ir, "current_ir"))
        object.__setattr__(self, "proposed_ir", None if self.proposed_ir is None else _mapping(self.proposed_ir, "proposed_ir"))
        object.__setattr__(
            self,
            "current_snowflake",
            None if self.current_snowflake is None else _mapping(self.current_snowflake, "current_snowflake"),
        )
        object.__setattr__(
            self,
            "proposed_snowflake",
            None if self.proposed_snowflake is None else _mapping(self.proposed_snowflake, "proposed_snowflake"),
        )
        object.__setattr__(self, "target_support", _mapping(self.target_support, "target_support"))
        for value in self.target_support.values():
            TargetSupport(value)
        object.__setattr__(self, "assumptions", _string_tuple(self.assumptions, "assumptions"))
        object.__setattr__(self, "required_validation", _string_tuple(self.required_validation, "required_validation"))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "audit_events", _mapping_tuple(self.audit_events, "audit_events"))
        object.__setattr__(self, "planned_hashes", _mapping(self.planned_hashes, "planned_hashes"))
        object.__setattr__(self, "applied_hashes", _mapping(self.applied_hashes, "applied_hashes"))
        object.__setattr__(self, "backup", _mapping(self.backup, "backup"))
        object.__setattr__(self, "powerbi_copy", _mapping(self.powerbi_copy, "powerbi_copy"))
        object.__setattr__(self, "validation_results", _mapping(self.validation_results, "validation_results"))
        if proposal_source is ProposalSource.POWERBI_IMPORT:
            expected_semantic_hash = proposal_semantic_content_sha256(self)
            if self.semantic_content_sha256 is None:
                object.__setattr__(self, "semantic_content_sha256", expected_semantic_hash)
            elif self.semantic_content_sha256 != expected_semantic_hash:
                raise ValueError(
                    "semantic_content_sha256 does not match Power BI import proposal content."
                )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "change_id": self.change_id,
            "created_at": self.created_at,
            "original_request": _thaw_json(self.original_request),
            "intent": self.intent,
            "mode": self.mode,
            "operation": _thaw_json(self.operation),
            "resolution": _thaw_json(self.resolution),
            "canonical_metric": self.canonical_metric,
            "canonical_file": self.canonical_file,
            "source_snapshot": _thaw_json(self.source_snapshot),
            "source_snapshot_hash": self.source_snapshot_hash,
            "current_ir": None if self.current_ir is None else _thaw_json(self.current_ir),
            "proposed_ir": None if self.proposed_ir is None else _thaw_json(self.proposed_ir),
            "canonical_patch": _thaw_json(self.canonical_patch),
            "canonical_diff": self.canonical_diff,
            "current_dax": self.current_dax,
            "proposed_dax": self.proposed_dax,
            "dax_diff": self.dax_diff,
            "current_snowflake": None if self.current_snowflake is None else _thaw_json(self.current_snowflake),
            "proposed_snowflake": None if self.proposed_snowflake is None else _thaw_json(self.proposed_snowflake),
            "snowflake_diff": self.snowflake_diff,
            "target_support": _thaw_json(self.target_support),
            "cross_target_valid": self.cross_target_valid,
            "assumptions": list(self.assumptions),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "risk_level": self.risk_level.value,
            "required_validation": list(self.required_validation),
            "status": self.status.value,
            "approval_state": self.approval_state.value,
            "validation_state": self.validation_state.value,
            "local_application_state": self.local_application_state.value,
            "deployment_state": self.deployment_state.value,
            "canonical_application_available": self.canonical_application_available,
        }
        if self.schema_version >= 2:
            result.update(
                {
                    "audit_events": _thaw_json(self.audit_events),
                    "planned_hashes": _thaw_json(self.planned_hashes),
                    "applied_hashes": _thaw_json(self.applied_hashes),
                    "backup": _thaw_json(self.backup),
                    "powerbi_copy": _thaw_json(self.powerbi_copy),
                    "validation_results": _thaw_json(self.validation_results),
                }
            )
        if self.schema_version >= 3:
            result.update(
                {
                    "proposal_source": self.proposal_source.value,
                    "import_run_id": self.import_run_id,
                    "source_model_path": self.source_model_path,
                    "source_model_id": self.source_model_id,
                    "source_object_path": self.source_object_path,
                    "source_object_id": self.source_object_id,
                    "source_snapshot_sha256": self.source_snapshot_sha256,
                    "authority_state": self.authority_state,
                    "extraction_version": self.extraction_version,
                    "semantic_content_sha256": self.semantic_content_sha256,
                }
            )
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalRecord":
        base_expected = {
            "schema_version", "change_id", "created_at", "original_request", "intent", "mode", "operation",
            "resolution", "canonical_metric", "canonical_file", "source_snapshot", "source_snapshot_hash",
            "current_ir", "proposed_ir", "canonical_patch", "canonical_diff", "current_dax", "proposed_dax",
            "dax_diff", "current_snowflake", "proposed_snowflake", "snowflake_diff", "target_support",
            "cross_target_valid", "assumptions", "diagnostics", "risk_level", "required_validation", "status",
            "approval_state", "validation_state", "local_application_state", "deployment_state",
            "canonical_application_available",
        }
        version = data.get("schema_version")
        m4_fields = {
            "audit_events", "planned_hashes", "applied_hashes", "backup", "powerbi_copy", "validation_results",
        }
        import_fields = {
            "proposal_source",
            "import_run_id",
            "source_model_path",
            "source_model_id",
            "source_object_path",
            "source_object_id",
            "source_snapshot_sha256",
            "authority_state",
            "extraction_version",
            "semantic_content_sha256",
        }
        expected = base_expected if version == 1 else base_expected | m4_fields
        if version == 3:
            expected |= import_fields
        elif set(data) & import_fields:
            expected |= import_fields
        _strict_fields(data, expected, "proposal record")
        if (
            data.get("proposal_source") == ProposalSource.POWERBI_IMPORT.value
            and not isinstance(data.get("semantic_content_sha256"), str)
        ):
            raise ValueError(
                "Invalid proposal record: semantic_content_sha256 must be present for Power BI import proposals."
            )
        try:
            return cls(
                schema_version=data["schema_version"],
                change_id=data["change_id"],
                created_at=data["created_at"],
                original_request=data["original_request"],
                intent=data["intent"],
                mode=data["mode"],
                operation=data["operation"],
                resolution=data["resolution"],
                canonical_metric=data["canonical_metric"],
                canonical_file=data["canonical_file"],
                source_snapshot=data["source_snapshot"],
                source_snapshot_hash=data["source_snapshot_hash"],
                current_ir=data["current_ir"],
                proposed_ir=data["proposed_ir"],
                canonical_patch=tuple(data["canonical_patch"]),
                canonical_diff=data["canonical_diff"],
                current_dax=data["current_dax"],
                proposed_dax=data["proposed_dax"],
                dax_diff=data["dax_diff"],
                current_snowflake=data["current_snowflake"],
                proposed_snowflake=data["proposed_snowflake"],
                snowflake_diff=data["snowflake_diff"],
                target_support=data["target_support"],
                cross_target_valid=data["cross_target_valid"],
                assumptions=_string_tuple(data["assumptions"], "assumptions"),
                diagnostics=tuple(ProposalDiagnostic.from_dict(item) for item in data["diagnostics"]),
                risk_level=RiskLevel(data["risk_level"]),
                required_validation=_string_tuple(data["required_validation"], "required_validation"),
                status=ProposalStatus(data["status"]),
                approval_state=ApprovalState(data["approval_state"]),
                validation_state=ValidationState(data["validation_state"]),
                local_application_state=LocalApplicationState(data["local_application_state"]),
                deployment_state=DeploymentState(data["deployment_state"]),
                canonical_application_available=data["canonical_application_available"],
                audit_events=tuple(data.get("audit_events", ())),
                planned_hashes=data.get("planned_hashes", {}),
                applied_hashes=data.get("applied_hashes", {}),
                backup=data.get("backup", {}),
                powerbi_copy=data.get("powerbi_copy", {}),
                validation_results=data.get("validation_results", {}),
                proposal_source=ProposalSource(data.get("proposal_source", ProposalSource.STRUCTURED_REQUEST.value)),
                import_run_id=data.get("import_run_id"),
                source_model_path=data.get("source_model_path"),
                source_model_id=data.get("source_model_id"),
                source_object_path=data.get("source_object_path"),
                source_object_id=data.get("source_object_id"),
                source_snapshot_sha256=data.get("source_snapshot_sha256"),
                authority_state=data.get("authority_state"),
                extraction_version=data.get("extraction_version"),
                semantic_content_sha256=data.get("semantic_content_sha256"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid proposal record: {exc}") from exc


def support_value(value: TargetSupport | str) -> str:
    return value.value if isinstance(value, TargetSupport) else value
