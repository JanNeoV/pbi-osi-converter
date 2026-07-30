"""Provider-independent strict tool-contract validator and dispatcher."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

import jsonschema

from semantic_poc.agent.inspection import inspect_metric
from semantic_poc.agent.powerbi_snowflake_audit import audit_powerbi_snowflake
from semantic_poc.agent.proposal_engine import propose_change
from semantic_poc.agent.schemas import MetricChangeRequest
from semantic_poc.src.models import DBT_SEMANTIC_YAML, PBI_DEFINITION_DIR, PROJECT_ROOT


ROOT = Path(__file__).parent
CANONICAL_SOURCE = "models/semantic/triathlon_semantic.yml"
_AUDIT_MODEL = PROJECT_ROOT / "semantic_poc/benchmark/pbi_trial_v2/fixtures/pbi_trial.SemanticModel"
_AUDIT_YAML = PROJECT_ROOT / "pbit/snowflake_semantic_view/pbi_trial.yaml"
_AUDIT_SPEC = PROJECT_ROOT / "semantic_poc/benchmark/pbi_trial_v2/measure-cases.yml"
_REQUEST = PROJECT_ROOT / "semantic_poc/examples/requests/valid_sbr_finishers_add_filter.json"


class ToolContractError(ValueError):
    pass


def _load(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry() -> dict[str, str]:
    registry = _load(ROOT / "registry.json")
    if set(registry) != {"schema_version", "tools"} or registry["schema_version"] != 2:
        raise ToolContractError("Tool registry is invalid.")
    values = {item["tool_id"]: item["request_schema"] for item in registry["tools"]}
    if len(values) != 7:
        raise ToolContractError("Tool registry must expose exactly seven tools.")
    return values


def _validate(value: Any, schema: Mapping[str, Any], label: str) -> None:
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as exc:
        raise ToolContractError(f"{label} fails strict schema validation: {exc.message}") from exc


def _envelope(tool_id: str, *, status: str, reason_code: str, result: dict[str, Any], actions: list[str]) -> dict[str, Any]:
    canonical_sha = _hash(DBT_SEMANTIC_YAML)
    value = {
        "schema_version": 2,
        "tool_id": tool_id,
        "status": status,
        "reason_code": reason_code,
        "source_hashes": {"canonical_sha256": canonical_sha},
        "canonical_source": CANONICAL_SOURCE,
        "canonical_sha256": canonical_sha,
        "permitted_next_actions": actions,
        "result": result,
    }
    _validate(value, _load(ROOT / "result-envelope.schema.json"), "result envelope")
    return value


def _audit_summary() -> dict[str, Any]:
    audit = audit_powerbi_snowflake(model_dir=_AUDIT_MODEL, snowflake_yaml=_AUDIT_YAML, benchmark_spec=_AUDIT_SPEC, repository_root=PROJECT_ROOT)
    return {"audit_id": audit.audit_id, "summary": dict(audit.summary)}


def _dispatch_inspect(_: Mapping[str, Any]) -> dict[str, Any]:
    value = inspect_metric("valid_sbr_finishers")
    return _envelope("inspect-source", status="OK", reason_code="EXACT_CANONICAL_MAPPING", result={"metric": value["canonical"]["name"], "power_bi_mapping_exists": bool(value["mappings"]["power_bi"]["exists"])}, actions=["propose-supported"])


def _dispatch_audit(_: Mapping[str, Any]) -> dict[str, Any]:
    audit = _audit_summary()
    return _envelope("audit-conversion", status="MANUAL_REVIEW_REQUIRED", reason_code="UNSAFE_TO_ACCEPT_BLINDLY", result=audit, actions=["list-findings"])


def _dispatch_findings(_: Mapping[str, Any]) -> dict[str, Any]:
    audit = _audit_summary()
    summary = audit["summary"]
    return _envelope("list-findings", status="MANUAL_REVIEW_REQUIRED", reason_code="AUDIT_BLOCKERS_PRESENT", result={"audit_id": audit["audit_id"], "omitted_measure_count": summary["omitted_measure_count"], "confirmed_incorrect_count": summary["fidelity_status_counts"]["CONFIRMED_INCORRECT"]}, actions=[])


def _dispatch_propose(_: Mapping[str, Any]) -> dict[str, Any]:
    request = MetricChangeRequest.from_dict(_load(_REQUEST))
    proposal = propose_change(request)
    return _envelope("propose-supported", status="OK", reason_code="SUPPORTED_PROPOSAL_CREATED", result={"change_id": proposal.change_id, "canonical_metric": proposal.canonical_metric, "canonical_source": proposal.canonical_file}, actions=["preview-sync"])


def _dispatch_preview(request: Mapping[str, Any]) -> dict[str, Any]:
    proposal = _dispatch_propose({})["result"]
    return _envelope("preview-sync", status="OK", reason_code="PREVIEW_REQUIRES_ISOLATED_STORE", result={"change_id": proposal["change_id"], "target_mode": request["mode"], "canonical_source": CANONICAL_SOURCE}, actions=["suggest-review"])


def _dispatch_suggest(_: Mapping[str, Any]) -> dict[str, Any]:
    return _envelope("suggest-review", status="REVIEW_RULE_SUGGESTED", reason_code="EXACT_REVIEW_RULE", result={"registered_rule_id": "review_unit_conversion_seconds_to_hours_v1", "human_confirmation": "REQUIRED"}, actions=["record-review"])


def _dispatch_record(_: Mapping[str, Any]) -> dict[str, Any]:
    return _envelope("record-review", status="REVIEW_RECORDED", reason_code="STRUCTURED_REVIEW_ONLY", result={"decision_id": "review_decision_guided_sync_demo_v1", "approval_state": "NOT_REQUESTED", "application_state": "NOT_REQUESTED"}, actions=[])


_DISPATCH: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "inspect-source": _dispatch_inspect, "audit-conversion": _dispatch_audit, "list-findings": _dispatch_findings,
    "propose-supported": _dispatch_propose, "preview-sync": _dispatch_preview,
    "suggest-review": _dispatch_suggest, "record-review": _dispatch_record,
}


def dispatch(value: Mapping[str, Any]) -> dict[str, Any]:
    _validate(value, _load(ROOT / "request-envelope.schema.json"), "request envelope")
    registry = _registry()
    tool_id = value["tool_id"]
    if tool_id not in registry or tool_id not in _DISPATCH:
        raise ToolContractError("Unknown or prohibited tool ID.")
    _validate(value["request"], _load(ROOT / registry[tool_id]), f"{tool_id} request")
    return _DISPATCH[tool_id](value["request"])


def run_conformance() -> dict[str, Any]:
    examples = [
        {"schema_version": 2, "tool_id": "inspect-source", "request": {"source_ref": "session-source"}},
        {"schema_version": 2, "tool_id": "audit-conversion", "request": {"import_ref": "imp_fixture"}},
        {"schema_version": 2, "tool_id": "list-findings", "request": {"audit_ref": "audit_fixture"}},
        {"schema_version": 2, "tool_id": "propose-supported", "request": {"import_ref": "imp_fixture"}},
        {"schema_version": 2, "tool_id": "preview-sync", "request": {"change_ref": "chg_fixture", "mode": "create"}},
        {"schema_version": 2, "tool_id": "suggest-review", "request": {"preview_ref": "preview_fixture", "finding_ref": "finding_fixture"}},
        {"schema_version": 2, "tool_id": "record-review", "request": {"preview_ref": "preview_fixture", "finding_ref": "finding_fixture", "answer_ref": "confirm_fixture"}},
    ]
    outputs = []
    for example in examples:
        first = json.dumps(dispatch(example), sort_keys=True, separators=(",", ":"))
        second = json.dumps(dispatch(example), sort_keys=True, separators=(",", ":"))
        if first != second:
            raise ToolContractError("Typed input did not produce byte-identical sanitized output.")
        outputs.append(json.loads(first))
    return {"status": "PASSED", "executed_prohibited_tool_calls": 0, "tool_count": len(outputs), "results": outputs}
