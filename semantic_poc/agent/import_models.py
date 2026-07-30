from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .proposal_models import ProposalRecord, ProposalSource


IMPORT_SCHEMA_VERSION = 2
IMPORT_PROPOSAL_BATCH_SCHEMA_VERSION = 3
SUPPORTED_IMPORT_SCHEMA_VERSIONS = frozenset({1, IMPORT_SCHEMA_VERSION})
SUPPORTED_IMPORT_PROPOSAL_BATCH_SCHEMA_VERSIONS = frozenset(
    {1, 2, IMPORT_PROPOSAL_BATCH_SCHEMA_VERSION}
)
IMPORT_ID_PATTERN = re.compile(r"^imp_(\d{8}T\d{6}Z)_([0-9a-f]{8})$")
UTC_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXTRACTION_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SAFE_ACTOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ImportAuthorityState(str, Enum):
    POWER_BI_DISCOVERED = "POWER_BI_DISCOVERED"
    CANONICALIZATION_PROPOSED = "CANONICALIZATION_PROPOSED"
    CANONICAL_CONTRACT_ACCEPTED = "CANONICAL_CONTRACT_ACCEPTED"


class ImportDiscardState(str, Enum):
    ACTIVE = "ACTIVE"
    DISCARDED = "DISCARDED"


# Short aliases are useful to callers rendering the authority-transition model.
AuthorityState = ImportAuthorityState
DiscardState = ImportDiscardState


def utc_timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp source must be timezone-aware.")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_import_id(now: datetime | None = None, entropy: str | None = None) -> str:
    timestamp = utc_timestamp(now).replace("-", "").replace(":", "")
    suffix = entropy or uuid.uuid4().hex[:8]
    if not isinstance(suffix, str) or not re.fullmatch(r"[0-9a-f]{8}", suffix):
        raise ValueError("Import ID entropy must contain exactly eight lowercase hexadecimal characters.")
    return f"imp_{timestamp}_{suffix}"


def validate_import_id(import_id: str) -> None:
    if not isinstance(import_id, str) or not IMPORT_ID_PATTERN.fullmatch(import_id):
        raise ValueError(f"Invalid import ID: {import_id!r}")


def validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid UTC timestamp: {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(f"Invalid UTC timestamp: {value!r}") from exc


def _validate_sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 value.")
    return value


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain control characters.")
    return value


def _validate_relative_path(value: Any, field_name: str) -> str:
    path = _required_text(value, field_name)
    if "\\" in path or ":" in path or path.startswith("/") or path.endswith("/"):
        raise ValueError(f"{field_name} must be a normalized repository-relative POSIX path.")
    raw_parts = path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"{field_name} must not contain empty or traversal path components.")
    normalized = PurePosixPath(path).as_posix()
    if normalized != path or PurePosixPath(path).is_absolute():
        raise ValueError(f"{field_name} must be a normalized repository-relative POSIX path.")
    return path


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


def _deep_freeze(value: Any, field_name: str = "value") -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must not contain non-finite JSON numbers.")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} JSON object keys must be strings.")
            normalized[key] = _deep_freeze(item, f"{field_name}.{key}")
        return MappingProxyType(normalized)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_deep_freeze(item, f"{field_name}[]") for item in value)
    raise ValueError(f"{field_name} must contain only JSON-compatible values.")


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


def _mapping_payload(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) and hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object or expose to_dict().")
    return _deep_freeze(value, field_name)


def canonical_json_text(value: Any, *, pretty: bool = False) -> str:
    normalized = _deep_thaw(_deep_freeze(value))
    if pretty:
        return json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def semantic_content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_text(value).encode("utf-8")).hexdigest()


def _sorted_mapping_tuple(value: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be an array of JSON objects.")
    items = tuple(_mapping_payload(item, field_name) for item in value)
    return tuple(
        sorted(
            items,
            key=lambda item: (
                canonical_json_text(semantic_projection(item)),
                canonical_json_text(item),
            ),
        )
    )


_VOLATILE_SEMANTIC_KEYS = frozenset(
    {
        "import_id",
        "import_run_id",
        "change_id",
        "created_at",
        "updated_at",
        "discarded_at",
        "timestamp",
        "audit_events",
        "authority_state",
        "discard_state",
        "approval_state",
        "validation_state",
        "deployment_state",
        "local_application_state",
        "status",
        "semantic_content_hash",
        "source_model_path",
    }
)


def semantic_projection(value: Any) -> Any:
    """Remove identity, time, path, and lifecycle fields from semantic hash input."""

    if isinstance(value, Mapping):
        return {
            key: semantic_projection(item)
            for key, item in sorted(value.items())
            if key not in _VOLATILE_SEMANTIC_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [semantic_projection(item) for item in value]
    return value


def _validate_id_timestamp(import_id: str, created_at: str) -> None:
    match = IMPORT_ID_PATTERN.fullmatch(import_id)
    if match is None:
        return
    compact_created_at = created_at.replace("-", "").replace(":", "")
    if match.group(1) != compact_created_at:
        raise ValueError("import_id timestamp must match created_at.")


@dataclass(frozen=True)
class ImportRun:
    schema_version: int
    import_id: str
    created_at: str
    authority_state: ImportAuthorityState
    discard_state: ImportDiscardState
    extraction_version: str
    source_model_id: str
    source_model_path: str
    source_snapshot_hash: str
    inventory: Mapping[str, Any]
    mapping_decisions: tuple[Mapping[str, Any], ...]
    relationship_findings: tuple[Mapping[str, Any], ...]
    classifications: tuple[Mapping[str, Any], ...]
    semantic_content_hash: str

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_IMPORT_SCHEMA_VERSIONS:
            raise ValueError(
                f"ImportRun schema_version must be one of {sorted(SUPPORTED_IMPORT_SCHEMA_VERSIONS)}."
            )
        validate_import_id(self.import_id)
        validate_timestamp(self.created_at)
        _validate_id_timestamp(self.import_id, self.created_at)
        try:
            authority_state = ImportAuthorityState(self.authority_state)
            discard_state = ImportDiscardState(self.discard_state)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid import-run lifecycle state: {exc}") from exc
        if authority_state is not ImportAuthorityState.POWER_BI_DISCOVERED:
            raise ValueError("ImportRun authority_state must be POWER_BI_DISCOVERED.")
        if discard_state is not ImportDiscardState.ACTIVE:
            raise ValueError("Immutable ImportRun records must have discard_state ACTIVE.")
        if not isinstance(self.extraction_version, str) or not EXTRACTION_VERSION_PATTERN.fullmatch(
            self.extraction_version
        ):
            raise ValueError("extraction_version must be a stable identifier of at most 64 characters.")
        _required_text(self.source_model_id, "source_model_id")
        _validate_relative_path(self.source_model_path, "source_model_path")
        _validate_sha256(self.source_snapshot_hash, "source_snapshot_hash")
        inventory = _mapping_payload(self.inventory, "inventory")
        if self.schema_version >= 2:
            support_records = inventory.get("object_support_records")
            if inventory.get("schema_version") != 2 or not isinstance(
                support_records, tuple
            ):
                raise ValueError(
                    "Schema-v2 import runs require a schema-v2 inventory with object_support_records."
                )
        mapping_decisions = _sorted_mapping_tuple(self.mapping_decisions, "mapping_decisions")
        relationship_findings = _sorted_mapping_tuple(
            self.relationship_findings, "relationship_findings"
        )
        classifications = _sorted_mapping_tuple(self.classifications, "classifications")
        object.__setattr__(self, "authority_state", authority_state)
        object.__setattr__(self, "discard_state", discard_state)
        object.__setattr__(self, "inventory", inventory)
        object.__setattr__(self, "mapping_decisions", mapping_decisions)
        object.__setattr__(self, "relationship_findings", relationship_findings)
        object.__setattr__(self, "classifications", classifications)
        _validate_sha256(self.semantic_content_hash, "semantic_content_hash")
        if self.semantic_content_hash != semantic_content_hash(self.semantic_payload()):
            raise ValueError("semantic_content_hash does not match ImportRun semantic content.")

    @classmethod
    def create(
        cls,
        *,
        extraction_version: str,
        source_model_id: str,
        source_model_path: str,
        source_snapshot_hash: str,
        inventory: Any,
        mapping_decisions: Sequence[Any] = (),
        relationship_findings: Sequence[Any] = (),
        classifications: Sequence[Any] = (),
        now: datetime | None = None,
        entropy: str | None = None,
    ) -> "ImportRun":
        timestamp_source = now or datetime.now(timezone.utc)
        created_at = utc_timestamp(timestamp_source)
        import_id = new_import_id(timestamp_source, entropy)
        provisional = {
            "schema_version": IMPORT_SCHEMA_VERSION,
            "extraction_version": extraction_version,
            "source_model_id": source_model_id,
            "source_snapshot_hash": source_snapshot_hash,
            "inventory": _deep_thaw(_mapping_payload(inventory, "inventory")),
            "mapping_decisions": [
                _deep_thaw(item)
                for item in _sorted_mapping_tuple(mapping_decisions, "mapping_decisions")
            ],
            "relationship_findings": [
                _deep_thaw(item)
                for item in _sorted_mapping_tuple(relationship_findings, "relationship_findings")
            ],
            "classifications": [
                _deep_thaw(item)
                for item in _sorted_mapping_tuple(classifications, "classifications")
            ],
        }
        return cls(
            schema_version=IMPORT_SCHEMA_VERSION,
            import_id=import_id,
            created_at=created_at,
            authority_state=ImportAuthorityState.POWER_BI_DISCOVERED,
            discard_state=ImportDiscardState.ACTIVE,
            extraction_version=extraction_version,
            source_model_id=source_model_id,
            source_model_path=source_model_path,
            source_snapshot_hash=source_snapshot_hash,
            inventory=inventory,
            mapping_decisions=tuple(mapping_decisions),
            relationship_findings=tuple(relationship_findings),
            classifications=tuple(classifications),
            semantic_content_hash=semantic_content_hash(semantic_projection(provisional)),
        )

    def semantic_payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "extraction_version": self.extraction_version,
            "source_model_id": self.source_model_id,
            "source_snapshot_hash": self.source_snapshot_hash,
            "inventory": _deep_thaw(self.inventory),
            "mapping_decisions": [_deep_thaw(item) for item in self.mapping_decisions],
            "relationship_findings": [_deep_thaw(item) for item in self.relationship_findings],
            "classifications": [_deep_thaw(item) for item in self.classifications],
        }
        return semantic_projection(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "import_id": self.import_id,
            "created_at": self.created_at,
            "authority_state": self.authority_state.value,
            "discard_state": self.discard_state.value,
            "extraction_version": self.extraction_version,
            "source_model_id": self.source_model_id,
            "source_model_path": self.source_model_path,
            "source_snapshot_hash": self.source_snapshot_hash,
            "inventory": _deep_thaw(self.inventory),
            "mapping_decisions": [_deep_thaw(item) for item in self.mapping_decisions],
            "relationship_findings": [_deep_thaw(item) for item in self.relationship_findings],
            "classifications": [_deep_thaw(item) for item in self.classifications],
            "semantic_content_hash": self.semantic_content_hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ImportRun":
        expected = {
            "schema_version",
            "import_id",
            "created_at",
            "authority_state",
            "discard_state",
            "extraction_version",
            "source_model_id",
            "source_model_path",
            "source_snapshot_hash",
            "inventory",
            "mapping_decisions",
            "relationship_findings",
            "classifications",
            "semantic_content_hash",
        }
        _strict_fields(data, expected, "ImportRun")
        return cls(
            schema_version=data["schema_version"],
            import_id=data["import_id"],
            created_at=data["created_at"],
            authority_state=ImportAuthorityState(data["authority_state"]),
            discard_state=ImportDiscardState(data["discard_state"]),
            extraction_version=data["extraction_version"],
            source_model_id=data["source_model_id"],
            source_model_path=data["source_model_path"],
            source_snapshot_hash=data["source_snapshot_hash"],
            inventory=data["inventory"],
            mapping_decisions=_sequence(data["mapping_decisions"], "mapping_decisions"),
            relationship_findings=_sequence(
                data["relationship_findings"], "relationship_findings"
            ),
            classifications=_sequence(data["classifications"], "classifications"),
            semantic_content_hash=data["semantic_content_hash"],
        )


def _sequence(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be an array.")
    return tuple(value)


@dataclass(frozen=True)
class ImportProposalBatch:
    schema_version: int
    import_id: str
    created_at: str
    authority_state: ImportAuthorityState
    discard_state: ImportDiscardState
    source_snapshot_hash: str
    import_semantic_content_hash: str
    extraction_version: str
    source_model_id: str
    source_model_path: str
    proposals: tuple[Mapping[str, Any], ...]
    manual_review_items: tuple[Mapping[str, Any], ...]
    unsupported_items: tuple[Mapping[str, Any], ...]
    blocked_child_ids: tuple[str, ...]
    semantic_content_hash: str

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_IMPORT_PROPOSAL_BATCH_SCHEMA_VERSIONS:
            raise ValueError(
                "ImportProposalBatch schema_version must be one of "
                f"{sorted(SUPPORTED_IMPORT_PROPOSAL_BATCH_SCHEMA_VERSIONS)}."
            )
        validate_import_id(self.import_id)
        validate_timestamp(self.created_at)
        try:
            authority_state = ImportAuthorityState(self.authority_state)
            discard_state = ImportDiscardState(self.discard_state)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid proposal-batch lifecycle state: {exc}") from exc
        if authority_state is not ImportAuthorityState.CANONICALIZATION_PROPOSED:
            raise ValueError(
                "ImportProposalBatch authority_state must be CANONICALIZATION_PROPOSED."
            )
        if discard_state is not ImportDiscardState.ACTIVE:
            raise ValueError("Immutable ImportProposalBatch records must have discard_state ACTIVE.")
        _validate_sha256(self.source_snapshot_hash, "source_snapshot_hash")
        _validate_sha256(
            self.import_semantic_content_hash, "import_semantic_content_hash"
        )
        if not isinstance(self.extraction_version, str) or not EXTRACTION_VERSION_PATTERN.fullmatch(
            self.extraction_version
        ):
            raise ValueError("extraction_version must be a stable identifier of at most 64 characters.")
        _required_text(self.source_model_id, "source_model_id")
        _validate_relative_path(self.source_model_path, "source_model_path")
        proposals = _sorted_mapping_tuple(self.proposals, "proposals")
        change_ids: list[str] = []
        validated_proposals: list[Mapping[str, Any]] = []
        for raw_proposal in proposals:
            try:
                proposal = ProposalRecord.from_dict(_deep_thaw(raw_proposal))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid import proposal child: {exc}") from exc
            change_ids.append(_validate_change_id_value(proposal.change_id))
            if proposal.proposal_source is not ProposalSource.POWERBI_IMPORT:
                raise ValueError("ImportProposalBatch children must have proposal_source POWERBI_IMPORT.")
            if proposal.import_run_id != self.import_id:
                raise ValueError("Import proposal provenance does not match the batch import_id.")
            if proposal.source_snapshot_sha256 != self.source_snapshot_hash:
                raise ValueError("Import proposal source snapshot does not match its batch.")
            if proposal.extraction_version != self.extraction_version:
                raise ValueError("Import proposal extraction version does not match its batch.")
            if proposal.source_model_id != self.source_model_id:
                raise ValueError("Import proposal source model ID does not match its batch.")
            if proposal.source_model_path != self.source_model_path:
                raise ValueError("Import proposal source model path does not match its batch.")
            validated_proposals.append(_mapping_payload(proposal.to_dict(), "proposal"))
        if len(set(change_ids)) != len(change_ids):
            raise ValueError("ImportProposalBatch proposals must have unique change_id values.")
        manual_review_items = _sorted_mapping_tuple(
            self.manual_review_items, "manual_review_items"
        )
        unsupported_items = _sorted_mapping_tuple(self.unsupported_items, "unsupported_items")
        blocked_child_ids = tuple(
            sorted(_validate_change_id_value(item) for item in self.blocked_child_ids)
        )
        if len(set(blocked_child_ids)) != len(blocked_child_ids):
            raise ValueError("ImportProposalBatch blocked_child_ids must be unique.")
        if set(change_ids) & set(blocked_child_ids):
            raise ValueError(
                "ImportProposalBatch executable and blocked child IDs must not overlap."
            )
        if self.schema_version < 3 and blocked_child_ids:
            raise ValueError(
                "ImportProposalBatch blocked_child_ids require schema_version 3."
            )
        proposals = tuple(
            sorted(
                validated_proposals,
                key=lambda item: (
                    canonical_json_text(semantic_projection(item)),
                    str(item["change_id"]),
                ),
            )
        )
        object.__setattr__(self, "authority_state", authority_state)
        object.__setattr__(self, "discard_state", discard_state)
        object.__setattr__(self, "proposals", proposals)
        object.__setattr__(self, "manual_review_items", manual_review_items)
        object.__setattr__(self, "unsupported_items", unsupported_items)
        object.__setattr__(self, "blocked_child_ids", blocked_child_ids)
        _validate_sha256(self.semantic_content_hash, "semantic_content_hash")
        if self.semantic_content_hash != semantic_content_hash(self.semantic_payload()):
            raise ValueError(
                "semantic_content_hash does not match ImportProposalBatch semantic content."
            )

    @classmethod
    def create(
        cls,
        *,
        import_id: str,
        source_snapshot_hash: str,
        import_semantic_content_hash: str,
        extraction_version: str,
        source_model_id: str,
        source_model_path: str,
        proposals: Sequence[Any],
        manual_review_items: Sequence[Any] = (),
        unsupported_items: Sequence[Any] = (),
        blocked_child_ids: Sequence[str] = (),
        now: datetime | None = None,
    ) -> "ImportProposalBatch":
        normalized_proposals = _sorted_mapping_tuple(proposals, "proposals")
        normalized_proposals = tuple(
            sorted(
                normalized_proposals,
                key=lambda item: (
                    canonical_json_text(semantic_projection(item)),
                    str(item.get("change_id", "")),
                ),
            )
        )
        normalized_manual = _sorted_mapping_tuple(manual_review_items, "manual_review_items")
        normalized_unsupported = _sorted_mapping_tuple(unsupported_items, "unsupported_items")
        normalized_blocked = tuple(
            sorted(_validate_change_id_value(item) for item in blocked_child_ids)
        )
        payload = {
            "schema_version": IMPORT_PROPOSAL_BATCH_SCHEMA_VERSION,
            "source_snapshot_hash": source_snapshot_hash,
            "import_semantic_content_hash": import_semantic_content_hash,
            "extraction_version": extraction_version,
            "source_model_id": source_model_id,
            "source_model_path": source_model_path,
            "proposals": [semantic_projection(item) for item in normalized_proposals],
            "manual_review_items": [_deep_thaw(item) for item in normalized_manual],
            "unsupported_items": [_deep_thaw(item) for item in normalized_unsupported],
            "blocked_child_ids": list(normalized_blocked),
        }
        return cls(
            schema_version=IMPORT_PROPOSAL_BATCH_SCHEMA_VERSION,
            import_id=import_id,
            created_at=utc_timestamp(now),
            authority_state=ImportAuthorityState.CANONICALIZATION_PROPOSED,
            discard_state=ImportDiscardState.ACTIVE,
            source_snapshot_hash=source_snapshot_hash,
            import_semantic_content_hash=import_semantic_content_hash,
            extraction_version=extraction_version,
            source_model_id=source_model_id,
            source_model_path=source_model_path,
            proposals=normalized_proposals,
            manual_review_items=normalized_manual,
            unsupported_items=normalized_unsupported,
            blocked_child_ids=normalized_blocked,
            semantic_content_hash=semantic_content_hash(semantic_projection(payload)),
        )

    @classmethod
    def for_run(
        cls,
        run: ImportRun,
        *,
        proposals: Sequence[Any],
        manual_review_items: Sequence[Any] = (),
        unsupported_items: Sequence[Any] = (),
        blocked_child_ids: Sequence[str] = (),
        now: datetime | None = None,
    ) -> "ImportProposalBatch":
        return cls.create(
            import_id=run.import_id,
            source_snapshot_hash=run.source_snapshot_hash,
            import_semantic_content_hash=run.semantic_content_hash,
            extraction_version=run.extraction_version,
            source_model_id=run.source_model_id,
            source_model_path=run.source_model_path,
            proposals=proposals,
            manual_review_items=manual_review_items,
            unsupported_items=unsupported_items,
            blocked_child_ids=blocked_child_ids,
            now=now,
        )

    @property
    def proposal_change_ids(self) -> tuple[str, ...]:
        return tuple(str(proposal["change_id"]) for proposal in self.proposals)

    @property
    def child_change_ids(self) -> tuple[str, ...]:
        return tuple(sorted((*self.proposal_change_ids, *self.blocked_child_ids)))

    def semantic_payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "source_snapshot_hash": self.source_snapshot_hash,
            "import_semantic_content_hash": self.import_semantic_content_hash,
            "extraction_version": self.extraction_version,
            "source_model_id": self.source_model_id,
            "source_model_path": self.source_model_path,
            "proposals": [semantic_projection(item) for item in self.proposals],
            "manual_review_items": [_deep_thaw(item) for item in self.manual_review_items],
            "unsupported_items": [_deep_thaw(item) for item in self.unsupported_items],
        }
        if self.schema_version >= 3:
            payload["blocked_child_ids"] = list(self.blocked_child_ids)
        return semantic_projection(payload)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "import_id": self.import_id,
            "created_at": self.created_at,
            "authority_state": self.authority_state.value,
            "discard_state": self.discard_state.value,
            "source_snapshot_hash": self.source_snapshot_hash,
            "import_semantic_content_hash": self.import_semantic_content_hash,
            "extraction_version": self.extraction_version,
            "source_model_id": self.source_model_id,
            "source_model_path": self.source_model_path,
            "proposals": [_deep_thaw(item) for item in self.proposals],
            "manual_review_items": [_deep_thaw(item) for item in self.manual_review_items],
            "unsupported_items": [_deep_thaw(item) for item in self.unsupported_items],
            "semantic_content_hash": self.semantic_content_hash,
        }
        if self.schema_version >= 3:
            result["blocked_child_ids"] = list(self.blocked_child_ids)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ImportProposalBatch":
        normalized_data = dict(data)
        legacy_forward_empty_blockers = (
            data.get("schema_version") in {1, 2}
            and data.get("blocked_child_ids") == []
        )
        if legacy_forward_empty_blockers:
            if data.get("semantic_content_hash") != semantic_content_hash(
                semantic_projection(data)
            ):
                raise ValueError(
                    "ImportProposalBatch semantic_content_hash does not match semantic content."
                )
            normalized_data.pop("blocked_child_ids")
            normalized_data["semantic_content_hash"] = semantic_content_hash(
                semantic_projection(normalized_data)
            )
            data = normalized_data
        expected = {
            "schema_version",
            "import_id",
            "created_at",
            "authority_state",
            "discard_state",
            "source_snapshot_hash",
            "import_semantic_content_hash",
            "extraction_version",
            "source_model_id",
            "source_model_path",
            "proposals",
            "manual_review_items",
            "unsupported_items",
            "semantic_content_hash",
        }
        if data.get("schema_version") == 3:
            expected.add("blocked_child_ids")
        _strict_fields(data, expected, "ImportProposalBatch")
        return cls(
            schema_version=data["schema_version"],
            import_id=data["import_id"],
            created_at=data["created_at"],
            authority_state=ImportAuthorityState(data["authority_state"]),
            discard_state=ImportDiscardState(data["discard_state"]),
            source_snapshot_hash=data["source_snapshot_hash"],
            import_semantic_content_hash=data["import_semantic_content_hash"],
            extraction_version=data["extraction_version"],
            source_model_id=data["source_model_id"],
            source_model_path=data["source_model_path"],
            proposals=_sequence(data["proposals"], "proposals"),
            manual_review_items=_sequence(data["manual_review_items"], "manual_review_items"),
            unsupported_items=_sequence(data["unsupported_items"], "unsupported_items"),
            blocked_child_ids=_sequence(data.get("blocked_child_ids", ()), "blocked_child_ids"),
            semantic_content_hash=data["semantic_content_hash"],
        )


def _validate_change_id_value(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"chg_\d{8}T\d{6}Z_[0-9a-f]{8}", value
    ):
        raise ValueError(f"Invalid proposal change_id: {value!r}")
    return value


@dataclass(frozen=True)
class ImportDiscardTombstone:
    schema_version: int
    import_id: str
    discarded_at: str
    authority_state: ImportAuthorityState
    discard_state: ImportDiscardState
    actor: str
    reason: str

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_IMPORT_SCHEMA_VERSIONS:
            raise ValueError(
                "ImportDiscardTombstone schema_version must be one of "
                f"{sorted(SUPPORTED_IMPORT_SCHEMA_VERSIONS)}."
            )
        validate_import_id(self.import_id)
        validate_timestamp(self.discarded_at)
        try:
            authority_state = ImportAuthorityState(self.authority_state)
            discard_state = ImportDiscardState(self.discard_state)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid discard-tombstone lifecycle state: {exc}") from exc
        if authority_state is ImportAuthorityState.CANONICAL_CONTRACT_ACCEPTED:
            raise ValueError("Accepted canonical imports cannot be discarded.")
        if discard_state is not ImportDiscardState.DISCARDED:
            raise ValueError("ImportDiscardTombstone discard_state must be DISCARDED.")
        _required_text(self.actor, "actor")
        if not SAFE_ACTOR_PATTERN.fullmatch(self.actor):
            raise ValueError(
                "actor must contain 1-64 letters, digits, periods, underscores, or hyphens."
            )
        _required_text(self.reason, "reason")
        object.__setattr__(self, "authority_state", authority_state)
        object.__setattr__(self, "discard_state", discard_state)

    @classmethod
    def create(
        cls,
        *,
        import_id: str,
        authority_state: ImportAuthorityState,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> "ImportDiscardTombstone":
        return cls(
            schema_version=IMPORT_SCHEMA_VERSION,
            import_id=import_id,
            discarded_at=utc_timestamp(now),
            authority_state=authority_state,
            discard_state=ImportDiscardState.DISCARDED,
            actor=actor,
            reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "import_id": self.import_id,
            "discarded_at": self.discarded_at,
            "authority_state": self.authority_state.value,
            "discard_state": self.discard_state.value,
            "actor": self.actor,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ImportDiscardTombstone":
        expected = {
            "schema_version",
            "import_id",
            "discarded_at",
            "authority_state",
            "discard_state",
            "actor",
            "reason",
        }
        _strict_fields(data, expected, "ImportDiscardTombstone")
        return cls(
            schema_version=data["schema_version"],
            import_id=data["import_id"],
            discarded_at=data["discarded_at"],
            authority_state=ImportAuthorityState(data["authority_state"]),
            discard_state=ImportDiscardState(data["discard_state"]),
            actor=data["actor"],
            reason=data["reason"],
        )
