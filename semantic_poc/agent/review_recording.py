"""Deterministic, offline Task 04 review suggestion and recording workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Mapping, Sequence

import jsonschema
from jsonschema import Draft202012Validator, FormatChecker
import yaml

from semantic_poc.review_memory import (
    DEFAULT_REVIEW_MEMORY_ROOT,
    ReviewMemoryError,
    ReviewRule,
    ReviewRuleV2,
    load_review_registry,
    sha256_value,
    suggest_review_for_finding,
)
from semantic_poc.src.models import PROJECT_ROOT

from .preview_sync import (
    BLOCKED_FILES,
    FULL_FILES,
    QUEUE_SCHEMA,
    RESULT_EVIDENCE_SCHEMA,
    _file_sha256,
    _has_link_component,
    _json_bytes,
    _tree_sha256,
    _yaml_bytes,
)


DECISION_SCHEMA = Path(__file__).with_name("review_decision.schema.json")
RULE_SCHEMA = DEFAULT_REVIEW_MEMORY_ROOT / "schema-v2.json"
REGISTRY_SCHEMA = DEFAULT_REVIEW_MEMORY_ROOT / "registry.schema.json"
REGISTRY_PATH = DEFAULT_REVIEW_MEMORY_ROOT / "registry.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
RULE_ELIGIBLE_ANSWERS = frozenset(
    {
        "CONFIRM_TYPED_SEMANTICS",
        "SELECT_EXACT_MAPPING",
        "CONFIRM_METADATA",
    }
)
UNRESOLVED_ANSWERS = frozenset(
    {"DEFER_MANUAL_REVIEW", "SUPPLY_HASH_BOUND_EVIDENCE"}
)


class ReviewRecordingError(RuntimeError):
    exit_code = 1
    code = "REVIEW_RECORDING_FAILED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class ReviewInputError(ReviewRecordingError):
    exit_code = 2
    code = "INVALID_REVIEW_INPUT"


class ReviewManualRequired(ReviewRecordingError):
    exit_code = 3
    code = "MANUAL_REVIEW_REQUIRED"


class ReviewStateError(ReviewRecordingError):
    exit_code = 4
    code = "REVIEW_STATE_CONFLICT"


@dataclass(frozen=True)
class PreviewBundle:
    root: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    queue: Mapping[str, Any]


@dataclass(frozen=True)
class SuggestReviewResult:
    preview_id: str
    evaluations: tuple[Mapping[str, Any], ...]

    @property
    def exit_code(self) -> int:
        return (
            0
            if self.evaluations
            and all(item["match_status"] == "EXACT" for item in self.evaluations)
            else 3
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_state": "NOT_REQUESTED",
            "application_state": "NOT_REQUESTED",
            "evaluated_count": len(self.evaluations),
            "human_confirmation": "REQUIRED",
            "preview_id": self.preview_id,
            "status": (
                "REVIEW_RULE_SUGGESTED"
                if self.exit_code == 0
                else "MANUAL_REVIEW_REQUIRED"
            ),
            "suggestions": [dict(item) for item in self.evaluations],
        }


@dataclass(frozen=True)
class RecordReviewResult:
    decision_id: str
    finding_id: str
    output_dir: str
    status: str
    proposed_rule: bool

    @property
    def exit_code(self) -> int:
        return 3 if self.status == "MANUAL_REVIEW_REQUIRED" else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_state": "NOT_REQUESTED",
            "approval_state": "NOT_REQUESTED",
            "decision_id": self.decision_id,
            "deployment_authorized": False,
            "finding_id": self.finding_id,
            "human_confirmation": "RECORDED",
            "output_dir": self.output_dir,
            "proposed_rule": self.proposed_rule,
            "status": self.status,
        }


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ReviewInputError(f"Decision YAML contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _load_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewInputError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ReviewInputError(f"{label} must contain a JSON object.")
    return value


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except ReviewRecordingError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ReviewInputError(f"Decision file is not valid UTF-8 YAML: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ReviewInputError("Decision file must contain exactly one YAML object.")
    return value


def _schema(path: Path) -> Mapping[str, Any]:
    value = _load_json(path, f"Schema {path.name}")
    try:
        Draft202012Validator.check_schema(value)
    except jsonschema.SchemaError as exc:
        raise ReviewStateError(
            f"Committed schema is invalid: {path.name}: {exc.message}",
            code="REVIEW_SCHEMA_INVALID",
        ) from exc
    return value


def _validate_schema(
    value: Any,
    schema: Mapping[str, Any],
    *,
    label: str,
) -> None:
    errors = sorted(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ReviewInputError(
            f"{label} fails schema at {location}: {first.message}",
            code="REVIEW_SCHEMA_VALIDATION_FAILED",
        )


def _safe_repo_input(
    value: str | Path,
    *,
    label: str,
    kind: str,
) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    root = PROJECT_ROOT.resolve(strict=True)
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise ReviewInputError(f"{label} does not exist: {value}") from exc
    if (
        not resolved.is_relative_to(root)
        or requested.is_symlink()
        or _has_link_component(requested, stop=root)
    ):
        raise ReviewInputError(f"{label} must be repository-local and non-symlinked.")
    if kind == "file" and not resolved.is_file():
        raise ReviewInputError(f"{label} must be a regular file.")
    if kind == "dir" and not resolved.is_dir():
        raise ReviewInputError(f"{label} must be a directory.")
    return resolved


def _repository_relative_path(
    value: str, *, label: str, kind: str = "file"
) -> Path:
    if "\\" in value:
        raise ReviewInputError(f"{label} must use repository-relative POSIX syntax.")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ReviewInputError(f"{label} is not a contained repository-relative path.")
    sensitive = {part.casefold() for part in pure.parts}
    if (
        ".env" in sensitive
        or "profiles.yml" in sensitive
        or "credentials" in sensitive
        or "secrets" in sensitive
    ):
        raise ReviewInputError(f"{label} may not reference credential-bearing paths.")
    return _safe_repo_input(
        PROJECT_ROOT.joinpath(*pure.parts), label=label, kind=kind
    )


def _relative(path: Path) -> str:
    return path.resolve(strict=True).relative_to(
        PROJECT_ROOT.resolve(strict=True)
    ).as_posix()


def _current_manifest_hashes(manifest: Mapping[str, Any]) -> dict[str, str]:
    result = {
        "canonical_baseline_sha256": manifest.get("canonical_baseline_sha256"),
        "canonical_candidate_sha256": manifest.get("canonical_candidate_sha256"),
        "proposal_sha256": manifest.get("proposal_sha256"),
        "powerbi_source_tree_sha256": manifest.get("source_powerbi_tree_sha256"),
        "snowflake_environment_sha256": manifest.get(
            "snowflake_environment_sha256"
        ),
    }
    if manifest.get("existing_snowflake_sha256") is not None:
        result["existing_snowflake_sha256"] = manifest["existing_snowflake_sha256"]
    if manifest.get("result_evidence_sha256") is not None:
        result["result_evidence_sha256"] = manifest["result_evidence_sha256"]
    if any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in result.values()):
        raise ReviewInputError("Preview manifest contains malformed bound hashes.")
    return dict(sorted(result.items()))


def _verify_preview_current(manifest: Mapping[str, Any]) -> None:
    for flag in (
        "application_performed",
        "approval_performed",
        "deployment_performed",
        "network_contacted",
        "source_edit_performed",
    ):
        if manifest.get(flag) is not False:
            raise ReviewStateError(
                f"Preview lifecycle conflicts at {flag}.",
                code="PREVIEW_LIFECYCLE_CONFLICT",
            )
    if manifest.get("proposal_status") not in {
        "PROPOSED",
        "NO_OP",
        "MANUAL_REVIEW_REQUIRED",
    }:
        raise ReviewStateError(
            "Preview proposal lifecycle is no longer reviewable.",
            code="PREVIEW_LIFECYCLE_CONFLICT",
        )
    protected = manifest.get("protected_inputs")
    if not isinstance(protected, Sequence) or isinstance(protected, (str, bytes)):
        raise ReviewInputError("Preview manifest protected inputs are malformed.")
    for item in protected:
        if not isinstance(item, Mapping) or set(item) != {
            "after_sha256",
            "before_sha256",
            "kind",
            "label",
            "path",
        }:
            raise ReviewInputError("Preview protected input record is malformed.")
        if item["kind"] not in {"FILE", "TREE"}:
            raise ReviewInputError("Preview protected input kind is invalid.")
        path = _repository_relative_path(
            item["path"],
            label="Preview protected input",
            kind="file" if item["kind"] == "FILE" else "dir",
        )
        actual = _file_sha256(path) if item["kind"] == "FILE" else _tree_sha256(path)
        if (
            item["before_sha256"] != item["after_sha256"]
            or actual != item["after_sha256"]
        ):
            raise ReviewStateError(
                f"Preview protected input is stale: {item['path']}",
                code="PREVIEW_INPUT_STALE",
            )
    compiler_files = manifest.get("compiler_files")
    if not isinstance(compiler_files, list):
        raise ReviewInputError("Preview compiler fingerprint is malformed.")
    for item in compiler_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise ReviewInputError("Preview compiler record is malformed.")
        path = _repository_relative_path(item["path"], label="Preview compiler")
        if _file_sha256(path) != item["sha256"]:
            raise ReviewStateError(
                f"Preview compiler is stale: {item['path']}",
                code="PREVIEW_COMPILER_STALE",
            )
    for path, field in (
        (QUEUE_SCHEMA, "validation_queue_schema_sha256"),
        (RESULT_EVIDENCE_SCHEMA, "result_evidence_schema_sha256"),
    ):
        if manifest.get(field) != _file_sha256(path):
            raise ReviewStateError(
                f"Preview schema is stale: {path.name}",
                code="PREVIEW_SCHEMA_STALE",
            )


def validate_preview_bundle(preview_dir: str | Path) -> PreviewBundle:
    root = _safe_repo_input(preview_dir, label="Preview directory", kind="dir")
    entries = list(root.iterdir())
    if any(item.is_symlink() or not item.is_file() for item in entries):
        raise ReviewInputError("Preview bundle contains unsafe or nested entries.")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ReviewInputError("Preview bundle is missing manifest.json.")
    manifest = _load_json(manifest_path, "Preview manifest")
    bundle_kind = manifest.get("bundle_kind")
    expected = set(BLOCKED_FILES if bundle_kind == "BLOCKED" else FULL_FILES if bundle_kind == "FULL" else ())
    actual = {item.name for item in entries}
    if not expected or actual != expected:
        raise ReviewInputError("Preview bundle file set is incomplete or unexpected.")
    artifact_hashes = manifest.get("artifact_hashes")
    if (
        not isinstance(artifact_hashes, Mapping)
        or set(artifact_hashes) != expected - {"manifest.json"}
    ):
        raise ReviewInputError("Preview manifest artifact hash set is invalid.")
    for name, expected_hash in artifact_hashes.items():
        if (
            not isinstance(expected_hash, str)
            or not SHA256_RE.fullmatch(expected_hash)
            or _file_sha256(root / name) != expected_hash
        ):
            raise ReviewStateError(
                f"Preview artifact is stale: {name}",
                code="PREVIEW_ARTIFACT_STALE",
            )
    queue = _load_json(root / "validation-queue.json", "Preview validation queue")
    _validate_schema(queue, _schema(QUEUE_SCHEMA), label="Preview validation queue")
    finding_ids = [item["finding_id"] for item in queue["findings"]]
    if (
        queue.get("preview_id") != manifest.get("preview_id")
        or finding_ids != sorted(finding_ids)
        or len(finding_ids) != len(set(finding_ids))
    ):
        raise ReviewInputError("Preview queue identity or stable ordering is invalid.")
    bound = _current_manifest_hashes(manifest)
    for finding in queue["findings"]:
        identity = finding["finding_identity_payload"]
        expected_id = "fnd_" + sha256_value(identity)[:24]
        if (
            finding["preview_id"] != queue["preview_id"]
            or identity["preview_id"] != queue["preview_id"]
            or finding["finding_id"] != expected_id
            or finding["bound_input_hashes"] != bound
            or finding["semantic_signature_sha256"]
            != sha256_value(finding["semantic_signature_payload"])
            or finding["typed_pattern_signature_sha256"]
            != sha256_value(finding["typed_pattern_signature_payload"])
        ):
            raise ReviewStateError(
                f"Finding is stale: {finding['finding_id']}",
                code="PREVIEW_FINDING_STALE",
            )
        try:
            Draft202012Validator.check_schema(finding["allowed_answer_schema"])
        except jsonschema.SchemaError as exc:
            raise ReviewInputError(
                f"Finding answer contract is invalid: {finding['finding_id']}: "
                f"{exc.message}"
            ) from exc
    _verify_preview_current(manifest)
    return PreviewBundle(
        root=root,
        manifest=manifest,
        manifest_sha256=_file_sha256(manifest_path),
        queue=queue,
    )


def _registry() -> tuple[ReviewRule | ReviewRuleV2, ...]:
    try:
        return load_review_registry()
    except ReviewMemoryError as exc:
        message = str(exc)
        if "stale" in message.casefold() or "lifecycle" in message.casefold():
            raise ReviewStateError(message, code="REVIEW_REGISTRY_STALE") from exc
        raise ReviewInputError(message, code="REVIEW_REGISTRY_INVALID") from exc


def suggest_review(
    preview_dir: str | Path,
    *,
    finding_id: str | None = None,
) -> SuggestReviewResult:
    bundle = validate_preview_bundle(preview_dir)
    findings = list(bundle.queue["findings"])
    if finding_id is not None:
        findings = [item for item in findings if item["finding_id"] == finding_id]
        if len(findings) != 1:
            raise ReviewInputError(
                f"Unknown preview finding ID: {finding_id}",
                code="REVIEW_FINDING_NOT_FOUND",
            )
    rules = _registry()
    evaluations = tuple(
        suggest_review_for_finding(item, rules)
        for item in sorted(findings, key=lambda value: value["finding_id"])
    )
    return SuggestReviewResult(
        preview_id=bundle.queue["preview_id"], evaluations=evaluations
    )


def _find_finding(bundle: PreviewBundle, finding_id: str) -> Mapping[str, Any]:
    findings = [
        item for item in bundle.queue["findings"] if item["finding_id"] == finding_id
    ]
    if len(findings) != 1:
        raise ReviewInputError(
            f"Unknown preview finding ID: {finding_id}",
            code="REVIEW_FINDING_NOT_FOUND",
        )
    return findings[0]


def _validate_evidence(
    decision: Mapping[str, Any],
    *,
    bundle: PreviewBundle,
    finding: Mapping[str, Any],
    rules: Sequence[ReviewRule | ReviewRuleV2],
) -> list[Mapping[str, Any]]:
    references = list(decision["evidence_references"])
    ids = [item["evidence_id"] for item in references]
    paths = [item["path"] for item in references]
    if ids != sorted(ids) or len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
        raise ReviewInputError(
            "Evidence references must use stable ID order and unique IDs/paths."
        )
    queue_path = (bundle.root / "validation-queue.json").resolve(strict=True)
    queue_bound = False
    for item in references:
        if item["finding_id"] != finding["finding_id"]:
            raise ReviewInputError("Evidence reference finding ID does not match.")
        path = _repository_relative_path(item["path"], label="Evidence reference")
        if _file_sha256(path) != item["sha256"]:
            raise ReviewStateError(
                f"Evidence reference is stale: {item['path']}",
                code="REVIEW_EVIDENCE_STALE",
            )
        if path == queue_path and item["kind"] == "PREVIEW_ARTIFACT":
            queue_bound = True
    if not queue_bound:
        raise ReviewInputError(
            "At least one PREVIEW_ARTIFACT evidence reference must bind validation-queue.json."
        )
    answer = decision["selected_answer"]
    parameters = answer["parameters"]
    if answer["answer_id"] == "SUPPLY_HASH_BOUND_EVIDENCE":
        matches = [
            item
            for item in references
            if item["kind"] == parameters["evidence_kind"]
            and item["path"] == parameters["evidence_path"]
            and item["sha256"] == parameters["sha256"]
        ]
        if len(matches) != 1:
            raise ReviewInputError(
                "SUPPLY_HASH_BOUND_EVIDENCE requires exactly one matching evidence reference."
            )
    if answer["answer_id"] == "CONFIRM_REGISTERED_REVIEW_RULE":
        matches = [
            item
            for item in references
            if item["kind"] == "REVIEW_RULE"
            and item["sha256"] == parameters["sha256"]
        ]
        if len(matches) != 1:
            raise ReviewInputError(
                "Registered-rule confirmation requires exactly one matching REVIEW_RULE reference."
            )
        rule = next(
            (
                item
                for item in rules
                if item.rule_id == parameters["rule_id"]
                and item.registered_sha256 == parameters["sha256"]
            ),
            None,
        )
        if rule is None or matches[0]["path"] != rule.registered_path:
            raise ReviewStateError(
                "Registered-rule evidence does not bind the current registry bytes.",
                code="REVIEW_REGISTRY_STALE",
            )
    return references


def _validate_selected_answer(
    decision: Mapping[str, Any],
    *,
    finding: Mapping[str, Any],
    rules: Sequence[ReviewRule | ReviewRuleV2],
) -> Mapping[str, Any] | None:
    answer = decision["selected_answer"]
    if answer["answer_id"] != "CONFIRM_REGISTERED_REVIEW_RULE":
        _validate_schema(
            answer,
            finding["allowed_answer_schema"],
            label="Selected answer",
        )
        if decision.get("suggested_rule") is not None:
            raise ReviewInputError(
                "suggested_rule is permitted only with CONFIRM_REGISTERED_REVIEW_RULE."
            )
        return None
    suggested = decision.get("suggested_rule")
    if not isinstance(suggested, Mapping):
        raise ReviewInputError(
            "Registered-rule confirmation requires suggested_rule."
        )
    result = suggest_review_for_finding(finding, rules)
    if result.get("match_status") != "EXACT":
        raise ReviewStateError(
            "The prior review suggestion is no longer exact.",
            code="REVIEW_SUGGESTION_STALE",
        )
    if (
        suggested
        != {
            "rule_id": result["registered_rule_id"],
            "sha256": result["registered_rule_sha256"],
        }
        or answer != result["permitted_structured_answer"]
    ):
        raise ReviewStateError(
            "The decision does not bind the current exact suggestion.",
            code="REVIEW_SUGGESTION_STALE",
        )
    return result


def _fixture_evidence(
    decision: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    value = decision.get("rule_fixture_evidence")
    if value is None:
        return None
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for group in ("positive", "near_misses"):
        ids = [item["fixture_id"] for item in value[group]]
        if ids != sorted(ids):
            raise ReviewInputError("Rule fixture evidence must use stable fixture-ID order.")
        for item in value[group]:
            if item["fixture_id"] in seen_ids or item["path"] in seen_paths:
                raise ReviewInputError("Rule fixture evidence IDs/paths must be unique.")
            seen_ids.add(item["fixture_id"])
            seen_paths.add(item["path"])
            path = _repository_relative_path(item["path"], label="Rule fixture evidence")
            if _file_sha256(path) != item["sha256"]:
                raise ReviewStateError(
                    f"Rule fixture evidence is stale: {item['path']}",
                    code="REVIEW_FIXTURE_STALE",
                )
    return value


def _has_missing_dependencies(
    finding: Mapping[str, Any], queue: Mapping[str, Any]
) -> bool:
    dependencies = set(finding["dependency_ids"])
    if not dependencies:
        return False
    related = {
        item.get("canonical_metric"): item
        for item in queue["findings"]
        if item.get("canonical_metric") in dependencies
    }
    return any(
        dependency in related
        and (
            related[dependency]["blocking"]
            or related[dependency]["status"] not in {"PASSED", "OPEN"}
        )
        for dependency in dependencies
    )


def _missing_required_evidence(
    finding: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
) -> bool:
    kinds = {item["kind"] for item in evidence}
    for requirement in finding["evidence_required"]:
        if requirement == "HASH_BOUND_DATA_RESULTS" and "DATA_RESULTS" in kinds:
            continue
        if requirement == "HASH_BOUND_MODEL_METADATA" and "MODEL_METADATA" in kinds:
            continue
        return True
    return False


def _signature_has_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value or value.upper() in {"UNKNOWN", "AMBIGUOUS"}
    if isinstance(value, Mapping):
        return any(_signature_has_null(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_signature_has_null(item) for item in value)
    return False


def _rule_proposal(
    decision: Mapping[str, Any],
    *,
    decision_sha256: str,
    decision_path: str,
    finding: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    fixture_evidence: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    scope = decision["requested_rule_scope"]
    bindings = dict(finding["concrete_role_bindings"])
    constraints = (
        {
            token: dict(role)
            for token, role in finding["typed_pattern_signature_payload"][
                "typed_parameter_roles"
            ].items()
        }
        if scope == "EXACT_TYPED_PATTERN"
        else {}
    )
    rule_id = "review_rule_" + hashlib.sha256(
        f"{decision['decision_id']}:{finding['finding_id']}:{scope}".encode("utf-8")
    ).hexdigest()[:24]
    rule = {
        "applicability": {
            "canonical_object_id": finding["finding_identity_payload"][
                "canonical_object_id"
            ],
            "parameter_role_constraints": constraints,
            "semantic_signature_payload": finding["semantic_signature_payload"],
            "semantic_signature_sha256": finding["semantic_signature_sha256"],
            "source_identifier": finding["source_identifier"],
            "typed_pattern_signature_payload": finding[
                "typed_pattern_signature_payload"
            ],
            "typed_pattern_signature_sha256": finding[
                "typed_pattern_signature_sha256"
            ],
        },
        "applicability_scope": scope,
        "confirmation_required": True,
        "finding_reason_codes": list(finding["reason_codes"]),
        "lifecycle": "CURRENT",
        "provenance": {
            "actor": decision["actor"],
            "canonical_source_file": finding["canonical_source_file"],
            "concrete_role_bindings": bindings,
            "decision_id": decision["decision_id"],
            "decision_path": decision_path,
            "decision_sha256": decision_sha256,
            "evidence_references": [dict(item) for item in evidence],
            "fixture_evidence": fixture_evidence,
            "rationale": decision["rationale"],
            "recorded_at": decision["recorded_at"],
            "source_snapshot_sha256": decision["bound_input_hashes"][
                "powerbi_source_tree_sha256"
            ],
        },
        "review_class": finding["review_class"],
        "rule_id": rule_id,
        "schema_version": 2,
        "status": "PROPOSED",
        "structured_answer": decision["selected_answer"],
        "superseded_by": None,
        "version": 1,
    }
    _validate_schema(rule, _schema(RULE_SCHEMA), label="Proposed review rule")
    ReviewRuleV2.from_dict(rule)
    return rule


def _render_session(
    decision: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    status: str,
) -> bytes:
    answer = decision["selected_answer"]
    lines = [
        "# Governed review session",
        "",
        f"- Decision ID: `{decision['decision_id']}`",
        f"- Preview ID: `{decision['preview_id']}`",
        f"- Finding ID: `{decision['finding_id']}`",
        f"- Review class: `{finding['review_class']}`",
        f"- Result status: `{status}`",
        f"- Canonical metric: `{finding['canonical_metric'] or 'UNRESOLVED'}`",
        f"- Source object: `{finding['source_identifier']}`",
        f"- Affected targets: `{', '.join(finding['affected_targets'])}`",
        f"- Approval state: `{decision['approval_state']}`",
        f"- Application state: `{decision['application_state']}`",
        "- Deployment authorized: `false`",
        "",
        "## Question",
        "",
        finding.get("human_question", "Review the structured validation finding."),
        "",
        "## Selected answer",
        "",
        f"`{answer['answer_id']}` with `{json.dumps(answer['parameters'], sort_keys=True)}`",
        "",
        "## Rationale",
        "",
        decision["rationale"],
        "",
        "## Evidence",
        "",
    ]
    lines.extend(
        f"- `{item['evidence_id']}` — `{item['kind']}` — `{item['path']}`"
        for item in decision["evidence_references"]
    )
    lines.extend(
        [
            "",
            "## Unresolved validation",
            "",
            (
                "- Manual review remains required."
                if status == "MANUAL_REVIEW_REQUIRED"
                else "- The selected finding was recorded as resolved; no operation was applied."
            ),
            "",
        ]
    )
    return ("\n".join(lines).replace("\r\n", "\n") + "\n").encode("utf-8")


def _safe_output(
    output_dir: str | Path,
    *,
    bundle: PreviewBundle,
    decision_file: Path,
) -> Path:
    requested = Path(output_dir)
    if not requested.is_absolute():
        requested = PROJECT_ROOT / requested
    root = PROJECT_ROOT.resolve(strict=True)
    resolved = requested.resolve(strict=False)
    if (
        not resolved.is_relative_to(root)
        or resolved == root
        or _has_link_component(requested, stop=root)
    ):
        raise ReviewInputError("Review output must be a safe repository-local directory.")
    if resolved.exists() or requested.is_symlink():
        raise ReviewStateError(
            "Review output directory must be fresh.",
            code="REVIEW_OUTPUT_EXISTS",
        )
    protected = [
        bundle.root,
        decision_file,
        DEFAULT_REVIEW_MEMORY_ROOT,
        PROJECT_ROOT / "models" / "semantic" / "triathlon_semantic.yml",
        PROJECT_ROOT / "semantic" / "triathlon_metric_contract.yml",
        PROJECT_ROOT / "pbit" / "snowflake_semantic_view" / "pbi_trial.yaml",
    ]
    for item in protected:
        candidate = item.resolve(strict=False)
        if (
            resolved == candidate
            or resolved.is_relative_to(candidate)
            or candidate.is_relative_to(resolved)
        ):
            raise ReviewInputError("Review output overlaps an input or protected source.")
    cursor = resolved.parent
    while cursor != root and cursor != cursor.parent:
        if (cursor / "manifest.json").is_file():
            raise ReviewInputError("Review output overlaps another managed bundle.")
        cursor = cursor.parent
    return resolved


def _write_atomic(output: Path, contents: Mapping[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".review-stage-", dir=output.parent))
    try:
        for name in sorted(contents):
            with (stage / name).open("xb") as handle:
                handle.write(contents[name])
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(stage, output)
    except Exception:
        if stage.exists():
            for item in stage.iterdir():
                if item.is_file() and not item.is_symlink():
                    item.unlink()
            stage.rmdir()
        raise


def record_review(
    preview_dir: str | Path,
    *,
    decision_file: str | Path,
    output_dir: str | Path,
) -> RecordReviewResult:
    bundle = validate_preview_bundle(preview_dir)
    decision_path = _safe_repo_input(
        decision_file, label="Decision file", kind="file"
    )
    decision = dict(_load_yaml(decision_path))
    _validate_schema(decision, _schema(DECISION_SCHEMA), label="Review decision")
    finding = _find_finding(bundle, decision["finding_id"])
    if (
        decision["preview_id"] != bundle.queue["preview_id"]
        or decision["preview_manifest_sha256"] != bundle.manifest_sha256
        or decision["bound_input_hashes"] != finding["bound_input_hashes"]
    ):
        raise ReviewStateError(
            "Review decision is stale for the current preview.",
            code="REVIEW_DECISION_STALE",
        )
    try:
        parsed_time = datetime.fromisoformat(
            decision["recorded_at"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ReviewInputError("recorded_at must be an RFC 3339 timestamp.") from exc
    if parsed_time.tzinfo is None:
        raise ReviewInputError("recorded_at must include an RFC 3339 timezone.")
    rules = _registry()
    suggestion = _validate_selected_answer(decision, finding=finding, rules=rules)
    evidence = _validate_evidence(
        decision, bundle=bundle, finding=finding, rules=rules
    )
    fixture_evidence = _fixture_evidence(decision)
    answer_id = decision["selected_answer"]["answer_id"]
    if decision["propose_review_rule"]:
        ineligible_reasons: list[str] = []
        if answer_id not in RULE_ELIGIBLE_ANSWERS:
            ineligible_reasons.append("ANSWER_NOT_ELIGIBLE")
        if suggestion is not None:
            ineligible_reasons.append("REGISTERED_RULE_CONFIRMATION")
        if decision.get("requested_rule_scope") not in {
            "EXACT_OBJECT",
            "EXACT_TYPED_PATTERN",
        }:
            ineligible_reasons.append("RULE_SCOPE_INVALID")
        if _signature_has_null(finding["semantic_signature_payload"]):
            ineligible_reasons.append("SEMANTIC_SIGNATURE_INCOMPLETE")
        if (
            decision.get("requested_rule_scope") == "EXACT_TYPED_PATTERN"
            and _signature_has_null(finding["typed_pattern_signature_payload"])
        ):
            ineligible_reasons.append("TYPED_PATTERN_SIGNATURE_INCOMPLETE")
        if _has_missing_dependencies(finding, bundle.queue):
            ineligible_reasons.append("DEPENDENCY_UNRESOLVED")
        if _missing_required_evidence(finding, evidence):
            ineligible_reasons.append("REQUIRED_EVIDENCE_MISSING")
        if not any(
            item["kind"] in {"MODEL_METADATA", "DOCUMENTATION", "REVIEW_RULE"}
            for item in evidence
        ):
            ineligible_reasons.append("REVIEW_EVIDENCE_MISSING")
        if any("UNSUPPORTED" in code for code in finding["reason_codes"]):
            ineligible_reasons.append("UNSUPPORTED_PATTERN")
        if ineligible_reasons:
            raise ReviewInputError(
                "The selected finding/answer is not eligible for a reusable rule "
                f"proposal: {', '.join(ineligible_reasons)}."
            )
        if (
            decision["requested_rule_scope"] == "EXACT_TYPED_PATTERN"
            and fixture_evidence is None
        ) or (
            decision["requested_rule_scope"] == "EXACT_OBJECT"
            and fixture_evidence is not None
        ):
            raise ReviewInputError(
                "Typed-pattern rules require fixture evidence; exact-object rules forbid it."
            )
    elif any(
        field in decision
        for field in ("requested_rule_scope", "rule_fixture_evidence")
    ):
        raise ReviewInputError(
            "Rule scope/fixtures are forbidden when propose_review_rule is false."
        )
    output = _safe_output(output_dir, bundle=bundle, decision_file=decision_path)
    status = (
        "MANUAL_REVIEW_REQUIRED"
        if answer_id in UNRESOLVED_ANSWERS
        else "REVIEW_RECORDED"
    )
    normalized_decision = {
        **decision,
        "recorded_result": status,
        "resolved_structured_operation": (
            suggestion["resolved_structured_operation"] if suggestion else None
        ),
    }
    decision_bytes = _yaml_bytes(normalized_decision)
    contents: dict[str, bytes] = {
        "review-decision.yml": decision_bytes,
        "review-session.md": _render_session(decision, finding, status=status),
    }
    if decision["propose_review_rule"]:
        rule = _rule_proposal(
            decision,
            decision_sha256=_file_sha256(decision_path),
            decision_path=_relative(decision_path),
            finding=finding,
            evidence=evidence,
            fixture_evidence=fixture_evidence,
        )
        contents["review-rule.proposed.yml"] = _yaml_bytes(rule)
    manifest = {
        "application_performed": False,
        "approval_performed": False,
        "artifact_hashes": {
            name: hashlib.sha256(payload).hexdigest()
            for name, payload in sorted(contents.items())
        },
        "decision_input_path": _relative(decision_path),
        "decision_input_sha256": _file_sha256(decision_path),
        "decision_schema_path": _relative(DECISION_SCHEMA),
        "decision_schema_sha256": _file_sha256(DECISION_SCHEMA),
        "deployment_performed": False,
        "finding_id": finding["finding_id"],
        "implementation_files": [
            {
                "path": _relative(path),
                "sha256": _file_sha256(path),
            }
            for path in sorted(
                (
                    Path(__file__),
                    PROJECT_ROOT / "semantic_poc" / "review_memory.py",
                    PROJECT_ROOT / "semantic_poc" / "agent" / "preview_sync.py",
                ),
                key=lambda item: item.as_posix(),
            )
        ],
        "preview_id": bundle.queue["preview_id"],
        "preview_manifest_path": (
            bundle.root.relative_to(PROJECT_ROOT.resolve()).as_posix()
            + "/manifest.json"
        ),
        "preview_manifest_sha256": bundle.manifest_sha256,
        "registry_path": _relative(REGISTRY_PATH),
        "registry_schema_path": _relative(REGISTRY_SCHEMA),
        "registry_schema_sha256": _file_sha256(REGISTRY_SCHEMA),
        "registry_sha256": _file_sha256(REGISTRY_PATH),
        "result_status": status,
        "review_rule_schema_path": _relative(RULE_SCHEMA),
        "review_rule_schema_sha256": _file_sha256(RULE_SCHEMA),
        "schema_version": 1,
        "source_modified": False,
        "tool": "semantic-agent record-review",
        "validation_queue_schema_path": _relative(QUEUE_SCHEMA),
        "validation_queue_schema_sha256": _file_sha256(QUEUE_SCHEMA),
    }
    contents["manifest.json"] = _json_bytes(manifest)
    expected = {
        "manifest.json",
        "review-decision.yml",
        "review-session.md",
    }
    if decision["propose_review_rule"]:
        expected.add("review-rule.proposed.yml")
    if set(contents) != expected:
        raise AssertionError("Review bundle file contract is incomplete.")
    _write_atomic(output, contents)
    return RecordReviewResult(
        decision_id=decision["decision_id"],
        finding_id=finding["finding_id"],
        output_dir=output.relative_to(PROJECT_ROOT.resolve()).as_posix(),
        status=status,
        proposed_rule=bool(decision["propose_review_rule"]),
    )


__all__ = [
    "DECISION_SCHEMA",
    "PreviewBundle",
    "RecordReviewResult",
    "ReviewInputError",
    "ReviewManualRequired",
    "ReviewRecordingError",
    "ReviewStateError",
    "SuggestReviewResult",
    "record_review",
    "suggest_review",
    "validate_preview_bundle",
]
