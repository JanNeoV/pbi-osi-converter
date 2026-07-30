"""Natural-language orchestration over deterministic semantic workflows.

The interpreter selects a typed operation or read-only import action.  This
module never imports approval/application/deployment functions and never
accepts model-authored target expressions.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Mapping

from semantic_poc.src.models import DBT_SEMANTIC_YAML, PBI_PBIP, PROJECT_ROOT, load_yaml

from .import_workflow import create_import_proposal_batch, create_import_run
from .inspection import MetricAmbiguousError, MetricNotFoundError, resolve_metric
from .interpretation import (
    ImportReviewAction,
    InterpretationEnvelope,
    InterpretationIntent,
    MalformedStructuredOutput,
    validate_interpretation,
)
from .openai_provider import (
    AgentConfigurationError,
    AgentProviderError,
    InterpretationProvider,
    OpenAIResponsesProvider,
)
from .powerbi_import import ImportSupportClassification
from .proposal_engine import propose_change
from .proposal_models import ProposalRecord
from .schemas import MetricChangeRequest, MetricOperation, OperationKind, TargetName, TargetSupport


PROMPT_ROOT = Path(__file__).with_name("prompts")
MAX_USER_TEXT = 4000
MODEL_SYMBOLS = {
    "COMMITTED_TRIATHLON_MODEL": PBI_PBIP,
}
CODE_OR_COMMAND_PATTERNS = (
    re.compile(r"```", re.IGNORECASE),
    re.compile(r"\b(?:CALCULATE|COUNTROWS|DIVIDE|COUNT_IF)\s*\(", re.IGNORECASE),
    re.compile(r"\bSELECT\b.{0,120}\bFROM\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:INSERT\s+INTO|DELETE\s+FROM|CREATE\s+(?:TABLE|VIEW)|ALTER\s+(?:TABLE|VIEW))\b", re.IGNORECASE),
    re.compile(r"(?:\.\.[/\\]|[A-Za-z]:\\|/(?:etc|home|var|tmp)/)", re.IGNORECASE),
    re.compile(r"(?:\$\(|`[^`]+`|&&|\|\|)", re.IGNORECASE),
    re.compile(r"\b(?:run|execute)\b.{0,40}\b(?:shell|command|powershell|cmd\.exe|bash)\b", re.IGNORECASE),
    re.compile(r"\b(?:ignore|override|bypass)\b.{0,60}\b(?:instruction|rule|schema|AGENTS\.md|system prompt)\b", re.IGNORECASE),
    re.compile(r"\b(?:approve|deploy|rollback)\b", re.IGNORECASE),
    re.compile(r"\bapply\b.{0,60}\b(?:change|proposal|canonical|Power\s*BI|Snowflake)\b", re.IGNORECASE),
)
EXECUTION_OUTPUT_PATTERNS = CODE_OR_COMMAND_PATTERNS[:-3] + CODE_OR_COMMAND_PATTERNS[-2:]


class AgentManualReview(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _validate_user_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise AgentConfigurationError("Natural-language text must be a non-empty string.")
    value = text.strip()
    if len(value) > MAX_USER_TEXT:
        raise AgentConfigurationError(f"Natural-language text must be at most {MAX_USER_TEXT} characters.")
    if any(pattern.search(value) for pattern in CODE_OR_COMMAND_PATTERNS):
        raise AgentManualReview(
            "MANUAL_REVIEW_REQUIRED",
            "The request contains code, path, prompt-override, lifecycle, or deployment instructions that are outside the natural-language boundary.",
        )
    return value


def _prompt(filename: str) -> str:
    path = PROMPT_ROOT / filename
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentConfigurationError(f"Controlled prompt template is unavailable: {filename}.") from exc
    if not value.strip():
        raise AgentConfigurationError(f"Controlled prompt template is empty: {filename}.")
    return value


def _provider(value: InterpretationProvider | None) -> InterpretationProvider:
    return value or OpenAIResponsesProvider()


def _interpret(
    *,
    provider: InterpretationProvider,
    instructions: str,
    context: Mapping[str, Any],
) -> InterpretationEnvelope:
    max_retries = getattr(provider, "max_retries", 0)
    if max_retries not in {0, 1}:
        raise AgentConfigurationError("Interpreter providers must cap controlled retries at 0 or 1.")
    last_error: MalformedStructuredOutput | None = None
    for attempt in range(max_retries + 1):
        retry_context = dict(context)
        if attempt:
            retry_context["RETRY_REASON"] = "Previous output did not satisfy the strict structured schema."
        try:
            return validate_interpretation(
                provider.parse(instructions=instructions, context=retry_context)
            )
        except MalformedStructuredOutput as exc:
            last_error = exc
            continue
        except AgentProviderError as exc:
            raise AgentManualReview(exc.code, str(exc)) from None
    raise AgentManualReview(
        "MANUAL_REVIEW_REQUIRED",
        "Structured interpretation remained invalid after the configured controlled retry.",
    ) from last_error


def _handle_non_executable(envelope: InterpretationEnvelope) -> None:
    if envelope.intent is InterpretationIntent.CLARIFICATION_REQUIRED:
        raise AgentManualReview("CLARIFICATION_REQUIRED", envelope.clarification_question or "Clarification is required.")
    if envelope.intent is InterpretationIntent.MANUAL_REVIEW_REQUIRED:
        raise AgentManualReview("MANUAL_REVIEW_REQUIRED", envelope.ambiguity or "Manual review is required.")


def _metric_context() -> dict[str, Any]:
    canonical = load_yaml(DBT_SEMANTIC_YAML) or {}
    metrics: list[dict[str, Any]] = []
    for metric in canonical.get("metrics", []) or []:
        if not isinstance(metric, Mapping) or not metric.get("name"):
            continue
        meta = ((metric.get("config") or {}).get("meta") or {})
        power_bi = meta.get("power_bi") or {}
        snowflake = meta.get("snowflake") or {}
        metrics.append(
            {
                "canonical_name": str(metric["name"]),
                "power_bi_measure": power_bi.get("measure"),
                "snowflake_metric": snowflake.get("metric_name"),
                "metric_type": metric.get("type"),
            }
        )
    return {"CANONICAL_METRICS_AND_EXACT_ALIASES": sorted(metrics, key=lambda item: item["canonical_name"])}


def _reject_executable_content(envelope: InterpretationEnvelope) -> None:
    if envelope.intent is not InterpretationIntent.METRIC_CHANGE_REQUEST:
        return
    payload = envelope.model_dump(mode="json", exclude_none=True)
    execution_text = json.dumps(
        {
            "operation": payload.get("operation"),
            "assumptions": payload.get("assumptions"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    if any(pattern.search(execution_text) for pattern in EXECUTION_OUTPUT_PATTERNS):
        raise AgentManualReview(
            "MANUAL_REVIEW_REQUIRED",
            "The interpreter supplied prohibited code, path, lifecycle, or deployment content.",
        )


def _operation_summary(operation: MetricOperation) -> str:
    summaries = {
        OperationKind.SET_LABEL: "Set canonical label metadata.",
        OperationKind.SET_DESCRIPTION: "Set canonical description metadata.",
        OperationKind.SET_FORMAT: "Set the registered canonical display format.",
        OperationKind.ADD_FILTER: "Add one typed equality filter.",
        OperationKind.REMOVE_FILTER: "Remove one typed equality filter.",
        OperationKind.REPLACE_FILTER: "Replace one typed equality filter.",
        OperationKind.SET_NUMERATOR: "Set the canonical ratio numerator metric.",
        OperationKind.SET_DENOMINATOR: "Set the canonical ratio denominator metric.",
        OperationKind.ENSURE_EXCLUDED_VALUES: "Ensure the named source values are excluded.",
        OperationKind.CREATE_METRIC: "Create a canonical metric proposal.",
        OperationKind.RENAME_METRIC: "Rename a canonical metric proposal.",
        OperationKind.DEPRECATE_METRIC: "Deprecate a canonical metric proposal.",
    }
    return summaries[operation.kind]


def propose_from_text(
    text: str,
    *,
    provider: InterpretationProvider | None = None,
) -> ProposalRecord:
    user_text = _validate_user_text(text)
    context = {
        "PROMPT_VERSION": "maintenance_v1",
        "USER_TEXT": user_text,
        **_metric_context(),
        "SUPPORTED_OPERATION_KINDS": [item.value for item in OperationKind],
        "REQUIRED_CANONICAL_SOURCE": "models/semantic/triathlon_semantic.yml",
    }
    envelope = _interpret(
        provider=_provider(provider),
        instructions=_prompt("maintenance_v1.txt"),
        context=context,
    )
    _handle_non_executable(envelope)
    if envelope.intent is not InterpretationIntent.METRIC_CHANGE_REQUEST:
        raise AgentManualReview("MANUAL_REVIEW_REQUIRED", "The interpreter selected the wrong operation family.")
    _reject_executable_content(envelope)
    assert envelope.operation is not None
    assert envelope.change_intent is not None
    assert envelope.canonical_metric_candidate is not None
    try:
        operation = MetricOperation.from_dict(
            envelope.operation.model_dump(mode="json", exclude_none=True)
        )
        canonical = load_yaml(DBT_SEMANTIC_YAML) or {}
        resolved = resolve_metric(canonical, envelope.canonical_metric_candidate)
        targets = tuple(envelope.requested_targets)
        request = MetricChangeRequest.create(
            user_request=user_text,
            intent=envelope.change_intent,
            canonical_metric_name=str(resolved.metric["name"]),
            requested_semantic_change=_operation_summary(operation),
            operation=operation,
            affected_targets=targets,
            target_support={target: TargetSupport.SUPPORTED_PATTERN for target in targets},
            assumptions=tuple(envelope.assumptions),
            diagnostics=("Natural-language interpretation validated; deterministic proposal engine remains authoritative.",),
        )
        return propose_change(request)
    except (MetricNotFoundError, MetricAmbiguousError, TypeError, ValueError) as exc:
        raise AgentManualReview("MANUAL_REVIEW_REQUIRED", str(exc)) from None


def import_powerbi_from_text(
    text: str,
    *,
    store: Any,
    mapping_file: str | Path | None = None,
    provider: InterpretationProvider | None = None,
) -> Any:
    user_text = _validate_user_text(text)
    context = {
        "PROMPT_VERSION": "powerbi_import_v1",
        "USER_TEXT": user_text,
        "ALLOWED_MODELS": [
            {"symbol": symbol, "description": "Committed triathlon Power BI semantic model"}
            for symbol in sorted(MODEL_SYMBOLS)
        ],
    }
    envelope = _interpret(
        provider=_provider(provider),
        instructions=_prompt("powerbi_import_v1.txt"),
        context=context,
    )
    _handle_non_executable(envelope)
    if envelope.intent is not InterpretationIntent.POWERBI_IMPORT_REQUEST:
        raise AgentManualReview("MANUAL_REVIEW_REQUIRED", "The interpreter selected the wrong operation family.")
    assert envelope.source_model_candidate is not None
    model_path = MODEL_SYMBOLS.get(envelope.source_model_candidate)
    if model_path is None:
        raise AgentManualReview("MANUAL_REVIEW_REQUIRED", "The source model symbol is not allowlisted.")
    return create_import_run(model_path, store=store, mapping_file=mapping_file)


def _classification_counts(run: Any) -> dict[str, int]:
    counts = Counter(str(item["classification"]) for item in run.classifications)
    return dict(sorted(counts.items()))


def _review_context(run: Any) -> dict[str, Any]:
    candidates = [
        {
            "source_table": str(item["source_table"]),
            "source_measure": str(item["source_measure"]),
            "classification": str(item["classification"]),
            "canonical_metric": (item.get("mapping") or {}).get("canonical_metric"),
        }
        for item in run.classifications
    ]
    findings = [
        {
            "code": str(item["code"]),
            "finding_type": str(item["finding_type"]),
            "informational": bool(item.get("informational")),
            "ambiguous_filter_path": bool(item.get("ambiguous_filter_path")),
        }
        for item in run.relationship_findings
    ]
    return {
        "IMPORT_ID": run.import_id,
        "CLASSIFICATION_COUNTS": _classification_counts(run),
        "MEASURE_FINDINGS": candidates,
        "RELATIONSHIP_FINDINGS": findings,
    }


def review_import_from_text(
    import_id: str,
    text: str,
    *,
    store: Any,
    provider: InterpretationProvider | None = None,
) -> dict[str, Any]:
    user_text = _validate_user_text(text)
    run = store.load_run(import_id)
    deterministic = _review_context(run)
    envelope = _interpret(
        provider=_provider(provider),
        instructions=_prompt("import_review_v1.txt"),
        context={"PROMPT_VERSION": "import_review_v1", "USER_TEXT": user_text, **deterministic},
    )
    _handle_non_executable(envelope)
    if envelope.intent is not InterpretationIntent.IMPORT_REVIEW_REQUEST:
        raise AgentManualReview("MANUAL_REVIEW_REQUIRED", "The interpreter selected the wrong operation family.")
    if envelope.import_run_candidate != import_id:
        raise AgentManualReview("MANUAL_REVIEW_REQUIRED", "The interpreted import ID does not match the requested import.")
    assert envelope.review_action is not None
    base: dict[str, Any] = {
        "import_id": import_id,
        "review_action": envelope.review_action.value,
        "classification_counts": deterministic["CLASSIFICATION_COUNTS"],
        "authority_state": run.authority_state.value,
    }
    if envelope.review_action is ImportReviewAction.SUMMARIZE_IMPORT:
        return {
            **base,
            "measure_count": len(run.classifications),
            "relationship_finding_count": len(run.relationship_findings),
        }
    if envelope.review_action is ImportReviewAction.LIST_SUPPORTED_EXACT:
        return {
            **base,
            "measures": [
                item for item in deterministic["MEASURE_FINDINGS"]
                if item["classification"] == ImportSupportClassification.SUPPORTED_EXACT.value
            ],
        }
    if envelope.review_action is ImportReviewAction.LIST_MANUAL_REVIEW:
        return {
            **base,
            "measures": [
                item for item in deterministic["MEASURE_FINDINGS"]
                if item["classification"] == ImportSupportClassification.MANUAL_REVIEW_REQUIRED.value
            ],
        }
    if envelope.review_action is ImportReviewAction.CREATE_EXACT_PROPOSALS:
        batch = create_import_proposal_batch(
            import_id,
            store=store,
            included_classifications=frozenset({ImportSupportClassification.SUPPORTED_EXACT}),
        )
        return {
            **base,
            "authority_state": batch.authority_state.value,
            "included_classifications": [ImportSupportClassification.SUPPORTED_EXACT.value],
            "proposal_count": len(batch.proposals),
            "manual_review_count": len(batch.manual_review_items),
            "unsupported_count": len(batch.unsupported_items),
            "proposals": [dict(item) for item in batch.proposals],
        }
    if envelope.review_action is ImportReviewAction.EXPLAIN_RELATIONSHIP:
        strict_findings = [
            item for item in run.relationship_findings
            if not item.get("informational") and item.get("finding_type") != "EXACT_MATCH"
        ]
        if envelope.finding_candidate is not None:
            strict_findings = [item for item in strict_findings if item.get("code") == envelope.finding_candidate]
        if len(strict_findings) != 1:
            raise AgentManualReview(
                "CLARIFICATION_REQUIRED",
                "Select one exact non-informational relationship finding code.",
            )
        finding = strict_findings[0]
        return {
            **base,
            "relationship_finding": {
                "code": finding["code"],
                "finding_type": finding["finding_type"],
                "message": finding["message"],
                "canonical_relationship": finding.get("canonical_relationship"),
                "relationship_id": finding.get("relationship_id"),
                "ambiguous_filter_path": bool(finding.get("ambiguous_filter_path")),
            },
        }
    raise AssertionError(f"Unhandled import review action: {envelope.review_action}")
