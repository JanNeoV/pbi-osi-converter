from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from semantic_poc.agent import cli
from semantic_poc.agent.proposal_engine import propose_change
from semantic_poc.agent.proposal_models import ProposalStatus
from semantic_poc.agent.schemas import MetricChangeRequest
from semantic_poc.review_memory import (
    DEFAULT_REVIEW_MEMORY_ROOT,
    ReviewMemoryError,
    load_review_registry,
    load_review_rule,
    review_match_signature,
    suggest_review_rule,
)
from semantic_poc.src.models import PROJECT_ROOT


REQUEST = (
    PROJECT_ROOT
    / "semantic_poc"
    / "examples"
    / "requests"
    / "valid_sbr_finishers_add_filter.json"
)
DECISION = PROJECT_ROOT / "semantic_poc" / "end_to_end" / "review-decision.accepted.yml"
RULE = DEFAULT_REVIEW_MEMORY_ROOT / "accepted" / "unit_conversion_seconds_to_hours.yml"
SCHEMA = DEFAULT_REVIEW_MEMORY_ROOT / "schema.json"
M8_FINDINGS = PROJECT_ROOT / "semantic_poc" / "demo" / "expected_findings.json"


def _context(rule: object) -> dict[str, object]:
    return {
        "finding_category": rule.finding_category,
        "source_pattern": dict(rule.source_pattern),
        "applicability": dict(rule.applicability),
        "fixture_source_signature": dict(rule.fixture_source_signature),
        "match_signature_sha256": rule.match_signature_sha256,
    }


def _resign(context: dict[str, object]) -> None:
    context["match_signature_sha256"] = review_match_signature(
        finding_category=str(context["finding_category"]),
        source_pattern=context["source_pattern"],
        applicability=context["applicability"],
        fixture_source_signature=context["fixture_source_signature"],
    )


def test_committed_request_is_schema_v2_and_proposes_only() -> None:
    assert REQUEST.is_file()
    request = MetricChangeRequest.from_dict(json.loads(REQUEST.read_text(encoding="utf-8")))
    proposal = propose_change(request)

    assert request.change_id == "chg_20260718T190000Z_90909090"
    assert request.mode.value == "PROPOSE"
    assert request.status.value == "DRAFT"
    assert request.approval_state.value == "NOT_REQUESTED"
    assert request.deployment_requested is False
    assert proposal.change_id == request.change_id
    assert proposal.status is ProposalStatus.PROPOSED
    assert proposal.cross_target_valid is True
    assert proposal.approval_state.value == "PENDING"
    assert proposal.deployment_state.value == "NOT_REQUESTED"


def test_committed_request_command_is_activation_free_without_dbt_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("SEMANTIC_AGENT_CHANGE_DIR", raising=False)
    monkeypatch.setattr(cli, "DBT_SEMANTIC_MANIFEST", tmp_path / "missing-manifest.json")
    monkeypatch.setattr(cli, "DEFAULT_CHANGE_DIR", tmp_path / "changes")

    assert cli.main(["propose", "--request", str(REQUEST), "--json"]) == 0
    proposal = json.loads(capsys.readouterr().out)

    assert proposal["change_id"] == "chg_20260718T190000Z_90909090"
    assert proposal["status"] == "PROPOSED"
    assert proposal["approval_state"] == "PENDING"
    assert proposal["deployment_state"] == "NOT_REQUESTED"


def test_unit_specific_finding_keeps_the_general_formula_mismatch() -> None:
    findings = json.loads(M8_FINDINGS.read_text(encoding="utf-8"))["findings"]
    hours = [item for item in findings if item["source_object"] == "Measures[Hours]"]

    assert len(
        [item for item in hours if item["category"] == "UNIT_CONVERSION_MISMATCH"]
    ) == 1
    assert len([item for item in hours if item["category"] == "FORMULA_MISMATCH"]) == 1


def test_registry_entry_validates_against_committed_schema_and_provenance() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    entry = yaml.safe_load(RULE.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(entry)

    rules = load_review_registry()
    assert len(rules) == 1
    rule = rules[0]
    assert rule.rule_id == "review_unit_conversion_seconds_to_hours_v1"
    assert rule.status == "ACCEPTED"
    assert rule.version == 1
    assert rule.supersession_status == "CURRENT"
    assert rule.approval_provenance["decision_sha256"] == hashlib.sha256(
        DECISION.read_bytes()
    ).hexdigest()
    assert rule.evidence_references


def test_runtime_rule_load_rejects_schema_invalid_yaml(tmp_path: Path) -> None:
    entry = yaml.safe_load(RULE.read_text(encoding="utf-8"))
    entry["status"] = "DRAFT"
    invalid = tmp_path / "invalid.yml"
    invalid.write_text(yaml.safe_dump(entry, sort_keys=False), encoding="utf-8", newline="\n")

    with pytest.raises(ReviewMemoryError, match="fails committed schema"):
        load_review_rule(invalid, schema_path=SCHEMA)


def test_exact_review_reuse_is_suggestion_only_and_requires_confirmation() -> None:
    rule = load_review_registry()[0]
    result = suggest_review_rule(_context(rule), (rule,))

    assert result == {
        "status": "REVIEW_RULE_SUGGESTED",
        "matched_rule_id": "review_unit_conversion_seconds_to_hours_v1",
        "prior_rationale": rule.human_rationale,
        "prior_evidence": list(rule.evidence_references),
        "proposed_structured_correction": {
            "kind": "USE_CANONICAL_SCALED_SUM",
            "canonical_metric": "duration_hours",
            "source_field": "duration_seconds",
            "scale_divisor": "3600",
        },
        "human_confirmation": "REQUIRED",
        "approval_state": "NOT_REQUESTED",
        "application_state": "NOT_REQUESTED",
    }


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("source_unit", None, "REVIEW_CONTEXT_UNITS_MISSING"),
        ("target_unit", "AMBIGUOUS", "REVIEW_CONTEXT_UNITS_AMBIGUOUS"),
    ],
)
def test_missing_or_ambiguous_units_require_manual_review(
    field: str, value: object, reason: str
) -> None:
    rule = load_review_registry()[0]
    context = _context(rule)
    context["applicability"][field] = value

    result = suggest_review_rule(context, (rule,))

    assert result["status"] == "MANUAL_REVIEW_REQUIRED"
    assert result["reason_code"] == reason
    assert result["human_confirmation"] == "REQUIRED"


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("source_pattern", "aggregation", "AVERAGE"),
        ("source_pattern", "observed_divisor", "30"),
        ("applicability", "relationship_context", "ALL_RELATIONSHIPS"),
        ("fixture_source_signature", "source_object_id", "pbi_measure_changed"),
        ("applicability", "target_unit", "minutes"),
    ],
)
def test_non_exact_ast_aggregation_relationship_signature_or_unit_has_no_match(
    section: str, field: str, value: str
) -> None:
    rule = load_review_registry()[0]
    context = _context(rule)
    context[section][field] = value
    _resign(context)

    result = suggest_review_rule(context, (rule,))

    assert result["status"] == "MANUAL_REVIEW_REQUIRED"
    assert result["reason_code"] == "NO_EXACT_REVIEW_RULE"


def test_changed_supplied_signature_and_duplicate_matches_require_manual_review() -> None:
    rule = load_review_registry()[0]
    changed = _context(rule)
    changed["match_signature_sha256"] = "0" * 64
    mismatch = suggest_review_rule(changed, (rule,))
    duplicate = suggest_review_rule(_context(rule), (rule, copy.deepcopy(rule)))

    assert mismatch["reason_code"] == "REVIEW_SIGNATURE_NOT_EXACT"
    assert duplicate["status"] == "MANUAL_REVIEW_REQUIRED"
    assert duplicate["reason_code"] == "AMBIGUOUS_REVIEW_RULE"
