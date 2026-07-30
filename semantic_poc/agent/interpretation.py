"""Strict, non-authoritative natural-language interpretation types.

These models describe what an interpreter may request.  They deliberately do
not contain filesystem paths, target expressions, lifecycle transitions, or
deployment controls.  Repository code validates and resolves every identity
before invoking the deterministic Milestone 3-5 workflows.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal, Mapping, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from .schemas import ChangeIntent, TargetName


INTERPRETATION_SCHEMA_VERSION = 1
IMPORT_ID_PATTERN = re.compile(r"^imp_\d{8}T\d{6}Z_[0-9a-f]{8}$")
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
FINDING_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
SAFE_CANDIDATE_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .\-\[\]]{0,127}$")
SAFE_FIELD_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InterpretationIntent(str, Enum):
    METRIC_CHANGE_REQUEST = "METRIC_CHANGE_REQUEST"
    POWERBI_IMPORT_REQUEST = "POWERBI_IMPORT_REQUEST"
    IMPORT_REVIEW_REQUEST = "IMPORT_REVIEW_REQUEST"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class ImportReviewAction(str, Enum):
    SUMMARIZE_IMPORT = "SUMMARIZE_IMPORT"
    LIST_SUPPORTED_EXACT = "LIST_SUPPORTED_EXACT"
    LIST_MANUAL_REVIEW = "LIST_MANUAL_REVIEW"
    CREATE_EXACT_PROPOSALS = "CREATE_EXACT_PROPOSALS"
    EXPLAIN_RELATIONSHIP = "EXPLAIN_RELATIONSHIP"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FilterInputModel(StrictModel):
    field: str = Field(min_length=1, max_length=128)
    operator: Literal["EQ"]
    value: Union[StrictBool, StrictInt, StrictFloat, StrictStr]

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        if not SAFE_FIELD_PATTERN.fullmatch(value):
            raise ValueError("filter field must be a safe identifier")
        return value


class SetTextOperation(StrictModel):
    kind: Literal["SET_LABEL", "SET_DESCRIPTION", "SET_FORMAT"]
    value: str = Field(min_length=1, max_length=500)


class PredicateOperation(StrictModel):
    kind: Literal["ADD_FILTER", "REMOVE_FILTER"]
    predicate: FilterInputModel


class ReplaceFilterOperation(StrictModel):
    kind: Literal["REPLACE_FILTER"]
    current: FilterInputModel
    proposed: FilterInputModel


class SetMetricReferenceOperation(StrictModel):
    kind: Literal["SET_NUMERATOR", "SET_DENOMINATOR"]
    metric: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)


class EnsureExcludedValuesOperation(StrictModel):
    kind: Literal["ENSURE_EXCLUDED_VALUES"]
    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)
    values: list[str] = Field(min_length=1, max_length=32)

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 128 or CONTROL_PATTERN.search(value) for value in values):
            raise ValueError("excluded values must be short printable strings")
        if len(set(values)) != len(values):
            raise ValueError("excluded values must be unique")
        return values


class CreateMetricOperation(StrictModel):
    kind: Literal["CREATE_METRIC"]
    proposed_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)


class RenameMetricOperation(StrictModel):
    kind: Literal["RENAME_METRIC"]
    new_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)


class DeprecateMetricOperation(StrictModel):
    kind: Literal["DEPRECATE_METRIC"]
    reason: str = Field(min_length=1, max_length=500)


SemanticOperation = Annotated[
    Union[
        SetTextOperation,
        PredicateOperation,
        ReplaceFilterOperation,
        SetMetricReferenceOperation,
        EnsureExcludedValuesOperation,
        CreateMetricOperation,
        RenameMetricOperation,
        DeprecateMetricOperation,
    ],
    Field(discriminator="kind"),
]


class InterpretationEnvelope(StrictModel):
    schema_version: Literal[INTERPRETATION_SCHEMA_VERSION]
    intent: InterpretationIntent
    change_intent: ChangeIntent | None = None
    canonical_metric_candidate: str | None = Field(default=None, max_length=128)
    operation: SemanticOperation | None = None
    source_model_candidate: str | None = Field(default=None, max_length=64)
    import_run_candidate: str | None = Field(default=None, max_length=64)
    review_action: ImportReviewAction | None = None
    finding_candidate: str | None = Field(default=None, max_length=128)
    requested_targets: list[TargetName] = Field(default_factory=list, max_length=3)
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    ambiguity: str | None = Field(default=None, max_length=500)
    clarification_question: str | None = Field(default=None, max_length=500)
    deployment_requested: Literal[False]

    @field_validator("canonical_metric_candidate")
    @classmethod
    def validate_candidate(cls, value: str | None) -> str | None:
        if value is not None and not SAFE_CANDIDATE_PATTERN.fullmatch(value):
            raise ValueError("canonical metric candidate must be an exact safe name")
        return value

    @field_validator("source_model_candidate")
    @classmethod
    def validate_model_symbol(cls, value: str | None) -> str | None:
        if value is not None and not SYMBOL_PATTERN.fullmatch(value):
            raise ValueError("source model candidate must be an allowed symbolic identifier")
        return value

    @field_validator("import_run_candidate")
    @classmethod
    def validate_import_candidate(cls, value: str | None) -> str | None:
        if value is not None and not IMPORT_ID_PATTERN.fullmatch(value):
            raise ValueError("import run candidate must be a valid import ID")
        return value

    @field_validator("finding_candidate")
    @classmethod
    def validate_finding_candidate(cls, value: str | None) -> str | None:
        if value is not None and not FINDING_PATTERN.fullmatch(value):
            raise ValueError("finding candidate must be a safe finding code")
        return value

    @field_validator("assumptions")
    @classmethod
    def validate_assumptions(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 256 or CONTROL_PATTERN.search(value) for value in values):
            raise ValueError("assumptions must be short printable strings")
        return values

    @model_validator(mode="after")
    def validate_intent_shape(self) -> "InterpretationEnvelope":
        if len(set(self.requested_targets)) != len(self.requested_targets):
            raise ValueError("requested_targets must not contain duplicates")
        execution_fields = (
            self.change_intent,
            self.canonical_metric_candidate,
            self.operation,
            self.source_model_candidate,
            self.import_run_candidate,
            self.review_action,
            self.finding_candidate,
        )
        if self.intent is InterpretationIntent.METRIC_CHANGE_REQUEST:
            if self.change_intent is None or self.canonical_metric_candidate is None or self.operation is None:
                raise ValueError("metric change requests require change intent, exact metric candidate, and operation")
            if TargetName.CANONICAL_DBT not in self.requested_targets:
                raise ValueError("metric change requests must include CANONICAL_DBT")
            if any((self.source_model_candidate, self.import_run_candidate, self.review_action, self.finding_candidate)):
                raise ValueError("metric change requests must not contain import fields")
        elif self.intent is InterpretationIntent.POWERBI_IMPORT_REQUEST:
            if self.source_model_candidate is None:
                raise ValueError("Power BI import requests require a symbolic source model")
            if any((self.change_intent, self.canonical_metric_candidate, self.operation, self.import_run_candidate, self.review_action, self.finding_candidate)):
                raise ValueError("Power BI import requests must contain only import selection fields")
            if self.requested_targets:
                raise ValueError("Power BI import requests must not request semantic targets")
        elif self.intent is InterpretationIntent.IMPORT_REVIEW_REQUEST:
            if self.import_run_candidate is None or self.review_action is None:
                raise ValueError("import review requests require an import ID and review action")
            if any((self.change_intent, self.canonical_metric_candidate, self.operation, self.source_model_candidate)):
                raise ValueError("import review requests must not contain metric execution fields")
            if self.review_action is not ImportReviewAction.EXPLAIN_RELATIONSHIP and self.finding_candidate is not None:
                raise ValueError("finding_candidate is valid only for relationship explanation")
            if self.requested_targets:
                raise ValueError("import review requests must not request semantic targets")
        elif self.intent is InterpretationIntent.CLARIFICATION_REQUIRED:
            if not self.clarification_question:
                raise ValueError("clarification requests require a question")
            if any(execution_fields) or self.requested_targets:
                raise ValueError("clarification requests must not contain execution fields")
        elif self.intent is InterpretationIntent.MANUAL_REVIEW_REQUIRED:
            if not self.ambiguity:
                raise ValueError("manual review requests require an ambiguity explanation")
            if any(execution_fields) or self.requested_targets:
                raise ValueError("manual review requests must not contain execution fields")
        return self


class MalformedStructuredOutput(ValueError):
    """Raised without retaining or echoing untrusted model output."""


def validate_interpretation(value: Any) -> InterpretationEnvelope:
    """Validate SDK-parsed, mapping, or JSON text output through one gate."""

    try:
        if isinstance(value, InterpretationEnvelope):
            # Validate a fresh copy so callers cannot bypass post-parse checks.
            return InterpretationEnvelope.model_validate(value.model_dump(mode="json"))
        if isinstance(value, str):
            return InterpretationEnvelope.model_validate_json(value)
        if isinstance(value, Mapping):
            return InterpretationEnvelope.model_validate(dict(value))
    except (ValidationError, ValueError, TypeError) as exc:
        raise MalformedStructuredOutput("Structured interpretation did not satisfy the strict schema.") from exc
    raise MalformedStructuredOutput("Structured interpretation was not a JSON object.")
