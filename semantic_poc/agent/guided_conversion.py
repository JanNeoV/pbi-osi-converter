"""Bounded, proposal-only orchestration for ``guide-conversion``.

Providers may select an allowlisted typed operation or explain deterministic
evidence.  This module alone owns paths, persistence, and deterministic tool
execution; a provider never receives filesystem authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

import jsonschema
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from semantic_poc.src.models import DBT_SEMANTIC_YAML, PROJECT_ROOT
from .change_store import ChangeStore
from .import_store import ImportStore
from .import_workflow import create_import_proposal_batch, create_import_run
from .powerbi_import import PowerBIPathError, resolve_powerbi_model_dir
from .preview_sync import preview_sync
from .preview_sync import PORTABLE_POWERBI_DEFINITION
from .proposal_engine import propose_change
from .review_recording import record_review, suggest_review
from .schemas import MetricChangeRequest


SESSION_ROOT = PROJECT_ROOT / "semantic_poc" / "agent_sessions"
PROMPT = Path(__file__).with_name("prompts") / "guided_conversion_v1.txt"
REVIEW_REGISTRY = PROJECT_ROOT / "semantic_poc" / "review_memory" / "registry.json"
TOOL_ROOT = Path(__file__).with_name("tool_contract")
TOOL_REGISTRY = TOOL_ROOT / "registry.json"
RUNTIME_VERSION = "guided-conversion-v2"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_TURNS = 10
MAX_TOOL_FAILURES = 2
MAX_OUTPUT_TOKENS = 800
PROHIBITED_TEXT = re.compile(r"(?:```|\b(?:approve|apply|deploy|rollback|validate)\b|\b(?:select|insert|delete|alter|create)\b|[A-Za-z]:[\\/]|\.\.[\\/])", re.I)
PROHIBITED_ARGUMENT = re.compile(r"(?:```|\b(?:select|insert|delete|alter|drop)\b|[A-Za-z]:[\\/]|\.\.[\\/])", re.I)
TOOL_IDS = ("inspect-source", "audit-conversion", "list-findings", "propose-supported", "preview-sync", "suggest-review", "record-review")


class GuidedConversionError(RuntimeError):
    def __init__(self, code: str, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class InspectSourceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_ref: Literal["session-source"]


class ImportReferenceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    import_ref: str = Field(pattern=r"^imp_[A-Za-z0-9_:-]+$")


class AuditReferenceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    audit_ref: str = Field(pattern=r"^audit_[A-Za-z0-9_:-]+$")


class PreviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    change_ref: str = Field(pattern=r"^chg_[A-Za-z0-9_:-]+$")
    mode: Literal["create", "update"]


class ReviewSuggestionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    preview_ref: str = Field(
        pattern=r"^(?:prv_[0-9a-f]{24}|preview_[A-Za-z0-9_:-]+)$"
    )
    finding_ref: str = Field(min_length=1, max_length=160)


class ReviewRecordArguments(ReviewSuggestionArguments):
    answer_ref: str = Field(min_length=1, max_length=160)


ToolArguments = (
    InspectSourceArguments
    | ImportReferenceArguments
    | AuditReferenceArguments
    | PreviewArguments
    | ReviewSuggestionArguments
    | ReviewRecordArguments
)


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool_id: Literal["inspect-source", "audit-conversion", "list-findings", "propose-supported", "preview-sync", "suggest-review", "record-review"]
    arguments: ToolArguments

    @model_validator(mode="after")
    def _tool_and_arguments_match(self) -> "ToolRequest":
        required_types: dict[str, tuple[type[BaseModel], ...]] = {
            "inspect-source": (InspectSourceArguments,),
            "audit-conversion": (ImportReferenceArguments,),
            "list-findings": (AuditReferenceArguments,),
            "propose-supported": (ImportReferenceArguments,),
            "preview-sync": (PreviewArguments,),
            "suggest-review": (ReviewSuggestionArguments,),
            "record-review": (ReviewRecordArguments,),
        }
        if not isinstance(self.arguments, required_types[self.tool_id]):
            raise ValueError("tool arguments do not match the requested tool")
        return self


class ClarificationOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer_ref: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=800)


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    finding_id: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=1200)
    options: list[ClarificationOption] = Field(min_length=2, max_length=8)


class AgentTurn(BaseModel):
    """The provider boundary.  No provider-side lifecycle authority exists."""
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    intent: Literal["RESPOND", "CALL_TOOL", "ASK_CLARIFICATION", "MANUAL_REVIEW_REQUIRED"]
    message: str = Field(min_length=1, max_length=4000)
    tool_request: ToolRequest | None = None
    clarification: Clarification | None = None
    checkpoint: str = Field(min_length=1, max_length=128)
    session_complete: bool
    deployment_requested: Literal[False]

    @model_validator(mode="after")
    def _fields_match_intent(self) -> "AgentTurn":
        if (self.intent == "CALL_TOOL") != (self.tool_request is not None):
            raise ValueError("CALL_TOOL requires exactly one tool_request")
        if (self.intent == "ASK_CLARIFICATION") != (self.clarification is not None):
            raise ValueError("ASK_CLARIFICATION requires exactly one clarification")
        return self


class SessionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    event_type: Literal["SESSION_STARTED", "USER_MESSAGE", "AGENT_TURN", "TOOL_REQUESTED", "TOOL_COMPLETED", "CLARIFICATION_ANSWERED", "SESSION_WAITING", "CHECKPOINT", "SESSION_COMPLETED", "SESSION_FAILED"]
    payload: dict[str, Any]


class GuidedProvider(Protocol):
    def next_turn(self, *, prompt: str, context: Mapping[str, Any]) -> AgentTurn: ...


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        if item.is_symlink():
            raise GuidedConversionError("UNSAFE_SOURCE_PATH", "Power BI source cannot contain symbolic links.", 2)
        if item.is_file():
            digest.update(item.relative_to(path).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(item.read_bytes())
    return digest.hexdigest()


def _load_dotenv() -> dict[str, str]:
    """Read only the ignored repository-local dotenv file and only allowlisted keys."""
    path = PROJECT_ROOT / ".env"
    if not path.is_file() or path.is_symlink():
        return {}
    allowed = {"OPENAI_API_KEY", "SEMANTIC_AGENT_MODEL", "SEMANTIC_AGENT_TIMEOUT_SECONDS"}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in allowed:
            values[key] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str = field(repr=False)
    api_key_source: Literal["process_environment", "repository_dotenv"]
    model: str
    timeout_seconds: float

    @classmethod
    def resolve(cls, *, model_override: str | None = None) -> "OpenAIConfig":
        dotenv = _load_dotenv()
        process_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        dotenv_api_key = dotenv.get("OPENAI_API_KEY", "").strip()
        api_key = process_api_key or dotenv_api_key
        model = (model_override or os.environ.get("SEMANTIC_AGENT_MODEL", "").strip() or dotenv.get("SEMANTIC_AGENT_MODEL", "").strip() or "gpt-5.6-sol")
        if not api_key:
            raise GuidedConversionError("OPENAI_API_KEY_REQUIRED", "Set OPENAI_API_KEY in the process environment or repository-local .env.", 2)
        if not SAFE_MODEL.fullmatch(model):
            raise GuidedConversionError("OPENAI_MODEL_INVALID", "The requested model identifier is not safe.", 2)
        try:
            timeout = float(os.environ.get("SEMANTIC_AGENT_TIMEOUT_SECONDS", "") or dotenv.get("SEMANTIC_AGENT_TIMEOUT_SECONDS", "") or "30")
        except ValueError as exc:
            raise GuidedConversionError("OPENAI_TIMEOUT_INVALID", "SEMANTIC_AGENT_TIMEOUT_SECONDS must be numeric.", 2) from exc
        if not 1 <= timeout <= 120:
            raise GuidedConversionError("OPENAI_TIMEOUT_INVALID", "SEMANTIC_AGENT_TIMEOUT_SECONDS must be between 1 and 120.", 2)
        return cls(
            api_key=api_key,
            api_key_source=("process_environment" if process_api_key else "repository_dotenv"),
            model=model,
            timeout_seconds=timeout,
        )


class ProviderDiagnostic(BaseModel):
    """Non-network, non-secret startup evidence for the OpenAI provider."""

    model_config = ConfigDict(extra="forbid", strict=True)
    api_key_available: Literal[True]
    api_key_source: Literal["process_environment", "repository_dotenv"]
    sdk_available: Literal[True]
    model: str
    recommended_action: Literal[
        "CLEAR_PROCESS_OPENAI_API_KEY_TO_USE_REPOSITORY_DOTENV",
        "READY_FOR_CAPPED_LIVE_SMOKE",
    ]
    timeout_seconds: float


def diagnose_openai_provider(*, model_override: str | None = None) -> ProviderDiagnostic:
    """Validate local provider prerequisites without constructing a request."""
    try:
        import openai  # noqa: F401 - import establishes the optional SDK gate.
    except ImportError as exc:
        raise GuidedConversionError(
            "OPENAI_SDK_UNAVAILABLE",
            "Install the optional agent dependency to use --provider openai.",
            2,
        ) from exc
    config = OpenAIConfig.resolve(model_override=model_override)
    return ProviderDiagnostic(
        api_key_available=True,
        api_key_source=config.api_key_source,
        sdk_available=True,
        model=config.model,
        recommended_action=(
            "CLEAR_PROCESS_OPENAI_API_KEY_TO_USE_REPOSITORY_DOTENV"
            if config.api_key_source == "process_environment"
            else "READY_FOR_CAPPED_LIVE_SMOKE"
        ),
        timeout_seconds=config.timeout_seconds,
    )


_MODEL_ERROR_CODES = frozenset({
    "invalid_model",
    "model_access_denied",
    "model_not_allowed",
    "model_not_available",
    "model_not_found",
    "unsupported_model",
})
_RESPONSES_PARAMETER_ERROR_CODES = frozenset({
    "invalid_parameter",
    "invalid_parameter_combination",
    "unknown_parameter",
    "unsupported_parameter",
    "unsupported_value",
})
_SAFE_PROVIDER_ERROR_FIELDS = frozenset({"code", "param", "type"})
_SAFE_RESPONSES_PARAMETERS = frozenset({
    "input",
    "instructions",
    "max_output_tokens",
    "model",
    "store",
    "text.format",
    "text.format.type",
    "text.format.schema",
    "text.format.schema.schema",
})
_STRUCTURED_OUTPUT_ERROR_CODES = frozenset({
    "invalid_json_schema",
    "invalid_schema",
    "schema_validation_failed",
})
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class LiveProviderProbe(BaseModel):
    """Terminal-only, redacted outcome of one bounded provider probe.

    This object must not be added to sessions, receipts, or evidence files.
    It contains no response message, response body, prompt, or credential.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    stage: Literal["model_access", "minimal_response", "structured_smoke"]
    classification: str = Field(pattern=r"^[A-Z0-9_]{1,80}$")
    selected_model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    http_status: int | None = Field(default=None, ge=100, le=599)
    error_type: str | None = Field(default=None, max_length=80)
    error_code: str | None = Field(default=None, max_length=80)
    error_param: str | None = Field(default=None, max_length=80)
    request_id: str | None = Field(default=None, max_length=128)


def _safe_provider_error_field(exc: Exception, field: str) -> str | None:
    """Read one allowlisted scalar from root or nested errors, then discard it."""
    if field not in _SAFE_PROVIDER_ERROR_FIELDS:
        return None
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        return None
    nested = body.get("error")
    for candidate in (body, nested):
        if not isinstance(candidate, Mapping):
            continue
        value = candidate.get(field)
        if isinstance(value, str) and value.isascii() and len(value) <= 80:
            return value
    return None


def _safe_request_id(value: Any) -> str | None:
    """Keep only the public request identifier supplied by the SDK."""
    if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return None


def _safe_http_status(value: Any) -> int | None:
    return value if isinstance(value, int) and 100 <= value <= 599 else None


def _provider_failure_code(exc: Exception, *, structured_output: bool = False) -> str:
    """Classify provider errors without retaining provider-supplied text."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    public_code = _safe_provider_error_field(exc, "code")
    public_parameter = _safe_provider_error_field(exc, "param")
    public_type = _safe_provider_error_field(exc, "type")
    if name == "AuthenticationError" or status == 401:
        return "OPENAI_AUTHENTICATION_FAILED"
    if name == "PermissionDeniedError" or status == 403:
        return "OPENAI_MODEL_ACCESS_DENIED"
    if (
        name == "NotFoundError"
        or status == 404
        or public_code in _MODEL_ERROR_CODES
        or (
            public_parameter == "model"
            and public_code in _RESPONSES_PARAMETER_ERROR_CODES
        )
    ):
        return "OPENAI_MODEL_NOT_AVAILABLE"
    if name == "RateLimitError" or status == 429:
        return "OPENAI_QUOTA_OR_RATE_LIMIT"
    if name in {"APITimeoutError", "TimeoutError"}:
        return "OPENAI_TIMEOUT"
    if name in {"APIConnectionError", "ConnectionError"} or isinstance(exc, OSError):
        return "OPENAI_TRANSPORT_FAILURE"
    if public_parameter == "max_output_tokens" and public_code in _RESPONSES_PARAMETER_ERROR_CODES:
        return "OPENAI_OUTPUT_BUDGET_REJECTED"
    if (
        structured_output
        and public_code in _STRUCTURED_OUTPUT_ERROR_CODES
        and public_parameter in _SAFE_RESPONSES_PARAMETERS
    ):
        return "OPENAI_INVALID_STRUCTURED_OUTPUT_SCHEMA"
    if public_code in _RESPONSES_PARAMETER_ERROR_CODES and public_parameter in _SAFE_RESPONSES_PARAMETERS:
        return "OPENAI_UNSUPPORTED_RESPONSES_PARAMETER"
    if public_type == "insufficient_quota":
        return "OPENAI_QUOTA_OR_RATE_LIMIT"
    if name == "BadRequestError" or status == 400:
        return "OPENAI_MODEL_OR_REQUEST_REJECTED"
    return "OPENAI_PROVIDER_FAILURE"


class OpenAIResponsesGuidedProvider:
    def __init__(self, *, model_override: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise GuidedConversionError("OPENAI_SDK_UNAVAILABLE", "Install the optional agent dependency to use --provider openai.", 2) from exc
        self.config = OpenAIConfig.resolve(model_override=model_override)
        self._client = OpenAI(api_key=self.config.api_key, timeout=self.config.timeout_seconds, max_retries=0)

    def _failure_probe(self, *, stage: Literal["model_access", "minimal_response", "structured_smoke"], model: str, exc: Exception, structured_output: bool = False) -> LiveProviderProbe:
        """Project a provider exception into terminal-safe metadata only."""
        return LiveProviderProbe(
            stage=stage,
            classification=_provider_failure_code(exc, structured_output=structured_output),
            selected_model=model,
            http_status=_safe_http_status(getattr(exc, "status_code", None)),
            error_type=_safe_provider_error_field(exc, "type"),
            error_code=_safe_provider_error_field(exc, "code"),
            error_param=_safe_provider_error_field(exc, "param"),
            request_id=_safe_request_id(getattr(exc, "request_id", None)),
        )

    @staticmethod
    def _success_probe(*, stage: Literal["model_access", "minimal_response", "structured_smoke"], model: str, response: Any) -> LiveProviderProbe:
        return LiveProviderProbe(
            stage=stage,
            classification="PASSED",
            selected_model=model,
            http_status=200,
            request_id=_safe_request_id(getattr(response, "_request_id", None)),
        )

    def probe_model_access(self, *, model: str) -> LiveProviderProbe:
        """Retrieve public model metadata without creating a model response."""
        try:
            response = self._client.models.retrieve(model)
        except Exception as exc:
            return self._failure_probe(stage="model_access", model=model, exc=exc)
        return self._success_probe(stage="model_access", model=model, response=response)

    def probe_minimal_response(self) -> LiveProviderProbe:
        """Send the smallest sanitized Responses request without a format schema."""
        try:
            response = self._client.responses.create(
                model=self.config.model,
                input="Return OK.",
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
        except Exception as exc:
            return self._failure_probe(stage="minimal_response", model=self.config.model, exc=exc)
        return self._success_probe(stage="minimal_response", model=self.config.model, response=response)

    def probe_structured_smoke(self, *, prompt: str, context: Mapping[str, Any]) -> LiveProviderProbe:
        """Run the actual strict AgentTurn request without writing session state."""
        try:
            response = self._client.responses.parse(
                model=self.config.model,
                instructions=prompt,
                input=_safe_json(context),
                text_format=AgentTurn,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                raise ValueError("missing structured output")
            AgentTurn.model_validate(parsed)
        except (ValueError, TypeError):
            return LiveProviderProbe(
                stage="structured_smoke",
                classification="PROVIDER_MALFORMED_OUTPUT",
                selected_model=self.config.model,
            )
        except Exception as exc:
            return self._failure_probe(
                stage="structured_smoke", model=self.config.model, exc=exc, structured_output=True
            )
        return self._success_probe(stage="structured_smoke", model=self.config.model, response=response)

    def next_turn(self, *, prompt: str, context: Mapping[str, Any]) -> AgentTurn:
        try:
            response = self._client.responses.parse(
                model=self.config.model,
                instructions=prompt,
                input=_safe_json(context),
                text_format=AgentTurn,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                raise ValueError("missing structured output")
            return AgentTurn.model_validate(parsed)
        except (ValueError, TypeError) as exc:
            raise GuidedConversionError("PROVIDER_MALFORMED_OUTPUT", "Provider response did not match the strict AgentTurn schema.") from exc
        except GuidedConversionError:
            raise
        except Exception as exc:
            code = _provider_failure_code(exc)
            raise GuidedConversionError(code, "OpenAI provider failed without exposing provider response details.") from None


class ScriptedGuidedProvider:
    """Deterministic offline provider used only by tests and golden evidence."""
    def next_turn(self, *, prompt: str, context: Mapping[str, Any]) -> AgentTurn:
        if "phase" not in context:
            return AgentTurn(schema_version=1, intent="MANUAL_REVIEW_REQUIRED", message="Deterministic evidence requires review.", checkpoint="inventory-reviewed", session_complete=True, deployment_requested=False)
        phase = context["phase"]
        if phase == "start":
            tool, arguments, checkpoint = "inspect-source", {"source_ref": "session-source"}, "inventory"
        elif phase == "inventory":
            tool, arguments, checkpoint = "audit-conversion", {"import_ref": context["ids"]["import_ref"]}, "audit"
        elif phase == "audit":
            tool, arguments, checkpoint = "list-findings", {"audit_ref": context["ids"]["audit_ref"]}, "findings"
        elif phase == "findings":
            tool, arguments, checkpoint = "propose-supported", {"import_ref": context["ids"]["import_ref"]}, "proposal"
        elif phase in {"proposal", "create-preview"}:
            tool = "preview-sync"
            arguments = {"change_ref": context["ids"]["change_ref"], "mode": "create" if phase == "proposal" else "update"}
            checkpoint = "create-preview" if phase == "proposal" else "update-preview"
        elif phase == "candidate-summary" and context["ids"].get("review_change_ref"):
            tool, arguments, checkpoint = (
                "preview-sync",
                {"change_ref": context["ids"]["review_change_ref"], "mode": "create"},
                "review-preview",
            )
        elif phase == "review-preview":
            tool, arguments, checkpoint = (
                "suggest-review",
                {
                    "preview_ref": context["ids"]["preview_ref"],
                    "finding_ref": context["ids"]["finding_ref"],
                },
                "clarification",
            )
        elif phase == "clarification":
            options = [
                ClarificationOption(**item) for item in context["answer_options"]
            ]
            return AgentTurn(
                schema_version=1,
                intent="ASK_CLARIFICATION",
                message="Choose one answer offered by the current deterministic finding.",
                clarification=Clarification(
                    finding_id=context["ids"]["finding_ref"],
                    question="How should this unsupported semantic pattern be handled?",
                    options=options,
                ),
                checkpoint="clarification",
                session_complete=False,
                deployment_requested=False,
            )
        elif phase == "review-recorded":
            return AgentTurn(
                schema_version=1,
                intent="MANUAL_REVIEW_REQUIRED",
                message="The structured review was recorded; unsupported semantics remain blocked.",
                checkpoint="review-recorded",
                session_complete=True,
                deployment_requested=False,
            )
        else:
            return AgentTurn(schema_version=1, intent="MANUAL_REVIEW_REQUIRED", message="Deterministic candidates are ready; unresolved semantics remain in the validation queue.", checkpoint="candidate-summary", session_complete=True, deployment_requested=False)
        return AgentTurn(schema_version=1, intent="CALL_TOOL", message="Run the next deterministic proposal-only operation.", tool_request=ToolRequest(tool_id=tool, arguments=arguments), checkpoint=checkpoint, session_complete=False, deployment_requested=False)


class SessionStore:
    def __init__(self, session_id: str) -> None:
        if not SAFE_ID.fullmatch(session_id):
            raise GuidedConversionError("INVALID_SESSION_ID", "Session IDs must contain only letters, digits, underscores, and hyphens.", 2)
        self.path = SESSION_ROOT / session_id
        self.events = self.path / "events.jsonl"
        self.manifest = self.path / "session.json"

    def exists(self) -> bool:
        return self.path.is_dir() and self.manifest.is_file() and self.events.is_file()

    def _atomic_write(self, path: Path, payload: bytes) -> None:
        fd, name = tempfile.mkstemp(prefix=".agent-session-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def create(self, bindings: Mapping[str, str], *, state: Mapping[str, Any] | None = None) -> None:
        if self.path.exists():
            raise GuidedConversionError("SESSION_ALREADY_EXISTS", "Session already exists; use --resume.", 4)
        SESSION_ROOT.mkdir(parents=True, exist_ok=True)
        self.path.mkdir()
        initial = dict(state or {"phase": "start", "provider": "scripted", "model": "scripted"})
        self._atomic_write(self.manifest, _safe_json({"schema_version": 2, "bindings": dict(bindings), "state": initial, "event_count": 0}).encode("utf-8"))
        self._atomic_write(self.events, b"")
        self.append("SESSION_STARTED", {"provider": initial["provider"], "model": initial["model"]})

    def read(self) -> dict[str, Any]:
        if not self.exists():
            raise GuidedConversionError("SESSION_NOT_FOUND", "--resume requires an existing session.", 4)
        try:
            value = json.loads(self.manifest.read_text(encoding="utf-8"))
            if value.get("schema_version") != 2 or not isinstance(value["bindings"], dict) or not isinstance(value["state"], dict):
                raise ValueError("invalid session manifest")
            return value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GuidedConversionError("SESSION_LOG_MALFORMED", "Session manifest is malformed.", 4) from exc

    def assert_current(self, bindings: Mapping[str, str]) -> dict[str, Any]:
        value = self.read()
        prior = value["bindings"]
        changed = sorted(key for key in set(prior) | set(bindings) if prior.get(key) != bindings.get(key))
        if changed:
            raise GuidedConversionError("SESSION_STALE_" + changed[0].upper(), "Session binding changed: " + changed[0], 4)
        return value

    def update_state(self, state: Mapping[str, Any]) -> None:
        current = self.read()
        current["state"] = dict(state)
        self._atomic_write(self.manifest, _safe_json(current).encode("utf-8"))

    def append(self, event_type: str, payload: Mapping[str, Any]) -> SessionEvent:
        current = self.read()
        event = SessionEvent(sequence=int(current["event_count"]) + 1, event_type=event_type, payload=dict(payload))
        raw = _safe_json(event.model_dump(mode="json")) + "\n"
        self._atomic_write(self.events, self.events.read_bytes() + raw.encode("utf-8"))
        current["event_count"] = event.sequence
        self._atomic_write(self.manifest, _safe_json(current).encode("utf-8"))
        return event


def _bindings(model_dir: Path, *, provider: str, model: str) -> dict[str, str]:
    return {
        "powerbi_source_tree_sha256": _tree_hash(model_dir), "canonical_yaml_sha256": _sha_file(DBT_SEMANTIC_YAML),
        "reviewed_mapping_sha256": "ABSENT", "prompt_sha256": _sha_file(PROMPT),
        "agent_turn_schema_sha256": hashlib.sha256(_safe_json(AgentTurn.model_json_schema()).encode()).hexdigest(),
        "tool_registry_sha256": _sha_file(TOOL_REGISTRY), "review_registry_sha256": _sha_file(REVIEW_REGISTRY),
        "runtime_version": RUNTIME_VERSION, "provider": provider, "model": model,
    }


def _registry() -> dict[str, Mapping[str, Any]]:
    try:
        registry = json.loads(TOOL_REGISTRY.read_text(encoding="utf-8"))
        if registry.get("schema_version") != 2 or len(registry.get("tools", [])) != 7:
            raise ValueError("registry version or tool count differs")
        return {item["tool_id"]: json.loads((TOOL_ROOT / item["request_schema"]).read_text(encoding="utf-8")) for item in registry["tools"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GuidedConversionError("TOOL_REGISTRY_INVALID", "The proposal-only tool registry is invalid.") from exc


def _validate_request(request: ToolRequest) -> None:
    if request.tool_id not in TOOL_IDS:
        raise GuidedConversionError("TOOL_NOT_ALLOWED", "The requested tool is not allowlisted.")
    arguments = request.arguments.model_dump(mode="json")
    try:
        jsonschema.Draft202012Validator(_registry()[request.tool_id]).validate(arguments)
    except jsonschema.ValidationError as exc:
        raise GuidedConversionError("TOOL_REQUEST_INVALID", "The requested tool arguments are not valid for the current session.") from exc
    if any(PROHIBITED_ARGUMENT.search(value) for value in _strings(arguments)):
        raise GuidedConversionError("TOOL_REQUEST_PROHIBITED", "The requested tool arguments crossed the governed boundary.")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str): return [value]
    if isinstance(value, Mapping): return [item for nested in value.values() for item in _strings(nested)]
    if isinstance(value, list): return [item for nested in value for item in _strings(nested)]
    return []


def _materialize_answer_schema(schema: Mapping[str, Any]) -> Any:
    if "const" in schema:
        return schema["const"]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        return {
            key: _materialize_answer_schema(properties[key])
            for key in schema.get("required", [])
        }
    raise GuidedConversionError(
        "REVIEW_ANSWER_SCHEMA_UNSUPPORTED",
        "The current finding does not expose a bounded structured answer.",
    )


def _offered_answers(finding: Mapping[str, Any]) -> list[dict[str, Any]]:
    answers = [
        _materialize_answer_schema(item)
        for item in finding["allowed_answer_schema"]["oneOf"]
    ]
    return [item for item in answers if isinstance(item, dict)]


def _answer_ref(answer: Mapping[str, Any]) -> str:
    return "answer_" + hashlib.sha256(_safe_json(answer).encode("utf-8")).hexdigest()[:20]


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(PROJECT_ROOT.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise GuidedConversionError(
            "SESSION_ARTIFACT_OUTSIDE_REPOSITORY",
            "A governed review artifact is outside the repository.",
        ) from exc


def _review_rule_path(rule_id: str) -> Path:
    accepted = PROJECT_ROOT / "semantic_poc" / "review_memory" / "accepted"
    matches: list[Path] = []
    for path in sorted(accepted.glob("*.yml")):
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(value, Mapping) and value.get("rule_id") == rule_id:
            matches.append(path)
    if len(matches) != 1:
        raise GuidedConversionError(
            "REVIEW_RULE_NOT_UNIQUE",
            "The exact accepted review rule could not be resolved uniquely.",
        )
    return matches[0]


class GuidedDispatcher:
    def __init__(self, *, session: SessionStore, model_dir: Path, state: dict[str, Any]) -> None:
        self.session, self.model_dir, self.state = session, model_dir, state
        self.imports = ImportStore(session.path / "imports")
        self.changes = ChangeStore(session.path / "changes")

    def _result(self, tool_id: str, status: str, reason: str, result: Mapping[str, Any], actions: list[str]) -> dict[str, Any]:
        value = {"schema_version": 2, "tool_id": tool_id, "status": status, "reason_code": reason,
                 "source_hashes": {"powerbi_source_tree_sha256": _tree_hash(self.model_dir)},
                 "canonical_source": "models/semantic/triathlon_semantic.yml", "canonical_sha256": _sha_file(DBT_SEMANTIC_YAML),
                 "permitted_next_actions": actions, "result": dict(result)}
        schema = json.loads((TOOL_ROOT / "result-envelope.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(value)
        return value

    def _preview_path(self, preview_id: str) -> Path:
        relative = (self.state.get("preview_paths") or {}).get(preview_id)
        if not isinstance(relative, str):
            raise GuidedConversionError(
                "TOOL_REFERENCE_STALE",
                "The requested preview is not bound to this session.",
            )
        path = (self.session.path / relative).resolve()
        try:
            path.relative_to(self.session.path.resolve())
        except ValueError as exc:
            raise GuidedConversionError(
                "SESSION_PREVIEW_OUTSIDE_SESSION",
                "The requested preview is outside the current session.",
            ) from exc
        if not path.is_dir():
            raise GuidedConversionError(
                "SESSION_PREVIEW_MISSING",
                "The requested preview is no longer available.",
                4,
            )
        return path

    def _preview_finding(
        self, preview_path: Path, finding_id: str
    ) -> Mapping[str, Any]:
        queue = json.loads(
            (preview_path / "validation-queue.json").read_text(encoding="utf-8")
        )
        matches = [
            item for item in queue["findings"] if item["finding_id"] == finding_id
        ]
        if len(matches) != 1:
            raise GuidedConversionError(
                "REVIEW_FINDING_NOT_FOUND",
                "The requested review finding is not current.",
            )
        return matches[0]

    def _record_decision(
        self,
        *,
        preview_id: str,
        finding_id: str,
        answer_ref: str,
    ) -> Mapping[str, Any]:
        pending = self.state.get("pending_clarification")
        if (
            not isinstance(pending, Mapping)
            or pending.get("preview_id") != preview_id
            or pending.get("finding_id") != finding_id
        ):
            raise GuidedConversionError(
                "CLARIFICATION_NOT_PENDING",
                "No matching clarification is pending for this session.",
            )
        options = pending.get("answers")
        if not isinstance(options, Mapping) or answer_ref not in options:
            raise GuidedConversionError(
                "CLARIFICATION_ANSWER_NOT_OFFERED",
                "The answer was not offered by the current deterministic finding.",
            )
        selected = options[answer_ref]
        if not isinstance(selected, Mapping):
            raise GuidedConversionError(
                "CLARIFICATION_ANSWER_INVALID",
                "The pending structured answer is invalid.",
            )
        preview_path = self._preview_path(preview_id)
        finding = self._preview_finding(preview_path, finding_id)
        queue_path = preview_path / "validation-queue.json"
        manifest_path = preview_path / "manifest.json"
        evidence = [
            {
                "evidence_id": "evidence_queue",
                "finding_id": finding_id,
                "kind": "PREVIEW_ARTIFACT",
                "path": _repo_relative(queue_path),
                "sha256": _sha_file(queue_path),
            }
        ]
        decision: dict[str, Any] = {
            "actor": "guided_conversion_reviewer",
            "application_state": "NOT_REQUESTED",
            "approval_state": "NOT_REQUESTED",
            "bound_input_hashes": finding["bound_input_hashes"],
            "decision_id": "review_" + hashlib.sha256(
                f"{self.session.path.name}\0{finding_id}\0{answer_ref}".encode()
            ).hexdigest()[:24],
            "deployment_authorized": False,
            "evidence_references": evidence,
            "finding_id": finding_id,
            "human_confirmation": "RECORDED",
            "preview_id": preview_id,
            "preview_manifest_sha256": _sha_file(manifest_path),
            "propose_review_rule": False,
            "rationale": "The reviewer selected an answer offered by the current deterministic finding.",
            "recorded_at": (
                "2026-07-25T12:00:00Z"
                if self.state.get("provider") == "scripted"
                else datetime.now(timezone.utc).isoformat()
            ),
            "schema_version": 1,
            "selected_answer": dict(selected),
        }
        suggestion = pending.get("suggestion")
        if (
            selected.get("answer_id") == "CONFIRM_REGISTERED_REVIEW_RULE"
            and isinstance(suggestion, Mapping)
            and suggestion.get("match_status") == "EXACT"
        ):
            rule_path = _review_rule_path(str(suggestion["registered_rule_id"]))
            evidence.append(
                {
                    "evidence_id": "evidence_rule",
                    "finding_id": finding_id,
                    "kind": "REVIEW_RULE",
                    "path": _repo_relative(rule_path),
                    "sha256": _sha_file(rule_path),
                }
            )
            decision["suggested_rule"] = {
                "rule_id": suggestion["registered_rule_id"],
                "sha256": suggestion["registered_rule_sha256"],
            }
        decision_dir = self.session.path / "decisions"
        decision_dir.mkdir(parents=True, exist_ok=True)
        decision_file = decision_dir / f"{decision['decision_id']}.yml"
        decision_file.write_text(
            yaml.safe_dump(decision, sort_keys=True), encoding="utf-8"
        )
        output_dir = self.session.path / "reviews" / str(decision["decision_id"])
        recorded = record_review(
            preview_path,
            decision_file=decision_file,
            output_dir=output_dir,
        )
        self.state.pop("pending_clarification", None)
        self.state.update(
            {
                "phase": "review-recorded",
                "recorded_decision_id": recorded.decision_id,
                "recorded_finding_id": recorded.finding_id,
            }
        )
        return recorded.to_dict()

    def dispatch(self, request: ToolRequest) -> dict[str, Any]:
        _validate_request(request)
        tool = request.tool_id
        if tool == "inspect-source":
            if self.state.get("import_id"):
                return self._result(tool, "OK", "IMPORT_ALREADY_CURRENT", {"import_id": self.state["import_id"]}, ["audit-conversion"])
            fixed = datetime(2026, 7, 25, tzinfo=timezone.utc) if self.state["provider"] == "scripted" else None
            run = create_import_run(self.model_dir, store=self.imports, now=fixed, entropy=hashlib.sha256(self.session.path.name.encode()).hexdigest()[:8] if fixed else None)
            self.state.update({"import_id": run.import_id, "phase": "inventory"})
            return self._result(tool, "OK", "IMMUTABLE_IMPORT_CREATED", {"import_id": run.import_id, "measure_count": len(run.inventory.get("measures", []))}, ["audit-conversion", "propose-supported"])
        if tool == "audit-conversion":
            run = self.imports.load_run(self.state["import_id"])
            counts: dict[str, int] = {}
            for item in run.classifications: counts[str(item["classification"])] = counts.get(str(item["classification"]), 0) + 1
            audit_id = "audit_" + run.import_id[4:]
            self.state.update({"phase": "audit", "audit_id": audit_id})
            return self._result(tool, "MANUAL_REVIEW_REQUIRED", "IMPORT_CLASSIFICATIONS_AVAILABLE", {"audit_id": audit_id, "classifications": counts}, ["propose-supported", "list-findings"])
        if tool == "list-findings":
            arguments = request.arguments.model_dump(mode="json")
            if arguments["audit_ref"] != self.state.get("audit_id"):
                raise GuidedConversionError(
                    "TOOL_REFERENCE_STALE",
                    "The requested audit is not bound to this session.",
                )
            batch = self.imports.try_load_proposal_batch(self.state["import_id"])
            if batch is None:
                batch = create_import_proposal_batch(
                    self.state["import_id"],
                    store=self.imports,
                    change_store=self.changes,
                    now=(
                        datetime(2026, 7, 25, tzinfo=timezone.utc)
                        if self.state["provider"] == "scripted"
                        else None
                    ),
                )
            blocked_ids = list(batch.blocked_child_ids)
            findings: list[dict[str, Any]] = []
            preferred: str | None = None
            for change_id in blocked_ids:
                proposal = self.changes.load_proposal(change_id)
                codes = sorted(item.code for item in proposal.diagnostics)
                finding = {
                    "finding_ref": change_id,
                    "source_measure": proposal.resolution.get("source_measure"),
                    "reason_codes": codes,
                    "status": proposal.status.value,
                }
                findings.append(finding)
                if preferred is None and any(
                    code
                    in {
                        "DAX_FILTER_STATE_DEPENDENCY",
                        "DAX_VISUAL_SCOPE_DEPENDENCY",
                        "DAX_INACTIVE_RELATIONSHIP_DEPENDENCY",
                    }
                    for code in codes
                ):
                    preferred = change_id
            self.state.update(
                {
                    "phase": "findings",
                    "blocked_change_ids": blocked_ids,
                    "review_change_id": preferred or (blocked_ids[0] if blocked_ids else None),
                }
            )
            return self._result(
                tool,
                "MANUAL_REVIEW_REQUIRED",
                "UNRESOLVED_QUEUE_AVAILABLE",
                {
                    "manual_review_count": len(batch.manual_review_items),
                    "unsupported_count": len(batch.unsupported_items),
                    "findings": findings,
                },
                ["preview-sync", "propose-supported"],
            )
        if tool == "propose-supported":
            batch = self.imports.try_load_proposal_batch(self.state["import_id"])
            if batch is None:
                batch = create_import_proposal_batch(self.state["import_id"], store=self.imports, change_store=self.changes, now=datetime(2026, 7, 25, tzinfo=timezone.utc) if self.state["provider"] == "scripted" else None)
            if not batch.proposals:
                self.state["phase"] = "candidate-summary"
                return self._result(tool, "MANUAL_REVIEW_REQUIRED", "NO_SUPPORTED_PROPOSAL", {"unresolved": len(batch.manual_review_items) + len(batch.unsupported_items)}, [])
            # Import evidence remains immutable intake.  The supported target
            # candidate is generated by the established canonical request
            # compiler, never by an imported DAX expression or the provider.
            request_path = PROJECT_ROOT / "semantic_poc" / "examples" / "requests" / "valid_sbr_finishers_add_filter.json"
            request = MetricChangeRequest.from_dict(json.loads(request_path.read_text(encoding="utf-8")))
            proposal = propose_change(request, powerbi_definition_dir=PORTABLE_POWERBI_DEFINITION)
            self.changes.save_proposal(proposal)
            change_id = proposal.change_id
            self.state.update({"change_id": change_id, "phase": "proposal", "unresolved": len(batch.manual_review_items) + len(batch.unsupported_items)})
            return self._result(tool, "OK", "SUPPORTED_PROPOSAL_CREATED", {"change_id": change_id, "supported_count": len(batch.proposals), "unresolved_count": self.state["unresolved"]}, ["preview-sync"])
        if tool == "preview-sync":
            arguments = request.arguments.model_dump(mode="json")
            change_id, mode = arguments["change_ref"], arguments["mode"]
            permitted_change_ids = {
                value
                for value in [
                    self.state.get("change_id"),
                    *(self.state.get("blocked_change_ids") or []),
                ]
                if isinstance(value, str)
            }
            if change_id not in permitted_change_ids:
                raise GuidedConversionError("TOOL_REFERENCE_STALE", "The requested change is not bound to this session.")
            preview_key = hashlib.sha256(
                f"{change_id}\0{mode}".encode("utf-8")
            ).hexdigest()[:16]
            output = self.session.path / "previews" / preview_key
            existing = (
                PROJECT_ROOT / "semantic_poc" / "output" / "snowflake_semantic_view.yml"
                if mode == "update"
                else None
            )
            if output.exists():
                manifest = json.loads(
                    (output / "manifest.json").read_text(encoding="utf-8")
                )
                preview_id = manifest["preview_id"]
                result = {"preview_id": preview_id, "target_mode": mode, "output": "session-preview"}
            else:
                created = preview_sync(change_id, target_mode=mode, output_dir=output, existing_snowflake_yaml=existing, change_store=self.changes, import_store=self.imports)
                preview_id = created.preview_id
                result = {"preview_id": created.preview_id, "target_mode": mode, "status": created.status, "blocking_findings": created.blocking_findings}
            preview_paths = dict(self.state.get("preview_paths") or {})
            preview_paths[preview_id] = output.relative_to(self.session.path).as_posix()
            self.state["preview_paths"] = preview_paths
            if change_id == self.state.get("review_change_id"):
                queue = json.loads(
                    (output / "validation-queue.json").read_text(encoding="utf-8")
                )
                current_finding = (
                    queue["findings"][0]["finding_id"]
                    if queue.get("findings")
                    else None
                )
                self.state.update(
                    {
                        "phase": "review-preview",
                        "review_preview_id": preview_id,
                        "review_finding_id": current_finding,
                    }
                )
                actions = ["suggest-review"] if current_finding else []
            else:
                self.state["preview_" + mode] = preview_id
                self.state["phase"] = "create-preview" if mode == "create" else "candidate-summary"
                actions = ["preview-sync"] if mode == "create" else []
            return self._result(tool, "OK", "PREVIEW_CREATED", result, actions)
        if tool == "suggest-review":
            arguments = request.arguments.model_dump(mode="json")
            preview_id = arguments["preview_ref"]
            finding_id = arguments["finding_ref"]
            preview_path = self._preview_path(preview_id)
            finding = self._preview_finding(preview_path, finding_id)
            suggestion_result = suggest_review(preview_path, finding_id=finding_id)
            suggestion = suggestion_result.evaluations[0]
            offered = _offered_answers(finding)
            exact_answer = suggestion.get("permitted_structured_answer")
            if isinstance(exact_answer, Mapping):
                offered = [dict(exact_answer), *[item for item in offered if item != exact_answer]]
            answers = {_answer_ref(item): item for item in offered}
            options = [
                {
                    "answer_ref": ref,
                    "label": str(answer.get("answer_id", "STRUCTURED_ANSWER")),
                }
                for ref, answer in sorted(answers.items())
            ]
            self.state.update(
                {
                    "phase": "clarification",
                    "pending_clarification": {
                        "preview_id": preview_id,
                        "finding_id": finding_id,
                        "answers": answers,
                        "suggestion": dict(suggestion),
                    },
                }
            )
            return self._result(
                tool,
                "MANUAL_REVIEW_REQUIRED",
                (
                    "EXACT_REVIEW_RULE_SUGGESTED"
                    if suggestion.get("match_status") == "EXACT"
                    else "STRUCTURED_CLARIFICATION_REQUIRED"
                ),
                {
                    "preview_id": preview_id,
                    "finding_id": finding_id,
                    "human_confirmation": "REQUIRED",
                    "match_status": suggestion.get("match_status"),
                    "answer_options": options,
                },
                ["record-review"],
            )
        if tool == "record-review":
            arguments = request.arguments.model_dump(mode="json")
            if arguments["answer_ref"] != self.state.get("confirmed_answer_ref"):
                raise GuidedConversionError(
                    "HUMAN_CONFIRMATION_REQUIRED",
                    "Recording a review requires an answer supplied by the reviewer on resume.",
                )
            recorded = self._record_decision(
                preview_id=arguments["preview_ref"],
                finding_id=arguments["finding_ref"],
                answer_ref=arguments["answer_ref"],
            )
            self.state.pop("confirmed_answer_ref", None)
            return self._result(
                tool,
                "OK",
                "STRUCTURED_REVIEW_RECORDED",
                recorded,
                [],
            )
        raise GuidedConversionError(
            "TOOL_NOT_IMPLEMENTED",
            "The requested proposal-only tool is not implemented.",
        )


def _emit(event: SessionEvent, *, json_events: bool) -> None:
    if json_events: print(_safe_json(event.model_dump(mode="json")))
    elif event.event_type == "AGENT_TURN": print(event.payload["message"])


def _context(state: Mapping[str, Any]) -> dict[str, Any]:
    ids = {
        "import_ref": state.get("import_id"),
        "audit_ref": state.get("audit_id"),
        "change_ref": state.get("change_id"),
        "review_change_ref": state.get("review_change_id"),
        "preview_ref": state.get("review_preview_id"),
        "finding_ref": state.get("review_finding_id"),
    }
    pending = state.get("pending_clarification")
    answer_options: list[dict[str, str]] = []
    if isinstance(pending, Mapping) and isinstance(pending.get("answers"), Mapping):
        answer_options = [
            {
                "answer_ref": ref,
                "label": str(answer.get("answer_id", "STRUCTURED_ANSWER")),
            }
            for ref, answer in sorted(pending["answers"].items())
            if isinstance(answer, Mapping)
        ]
    permitted_tools = [
        tool
        for tool in TOOL_IDS
        if tool != "record-review" or state.get("confirmed_answer_ref")
    ]
    return {
        "phase": state["phase"],
        "ids": {key: value for key, value in ids.items() if value},
        "permitted_tools": permitted_tools,
        "unresolved_count": state.get("unresolved", 0),
        "answer_options": answer_options,
    }


def _provider(args: Any) -> tuple[GuidedProvider, str]:
    if args.provider == "scripted": return ScriptedGuidedProvider(), "scripted"
    provider = OpenAIResponsesGuidedProvider(model_override=args.model)
    return provider, provider.config.model


def run_guide_conversion(args: Any) -> int:
    try:
        model_dir = resolve_powerbi_model_dir(args.model_dir, PROJECT_ROOT)
        provider, model = _provider(args)
        session_id = args.session or ("session_" + secrets.token_hex(12))
        store = SessionStore(session_id)
        bindings = _bindings(model_dir, provider=args.provider, model=model)
        answer_ref = getattr(args, "answer_ref", None)
        if answer_ref and not args.resume:
            raise GuidedConversionError(
                "ANSWER_REQUIRES_RESUME",
                "--answer-ref is valid only with --resume.",
                2,
            )
        if args.resume:
            manifest = store.assert_current(bindings); state = dict(manifest["state"])
            _emit(store.append("CHECKPOINT", {"checkpoint": "resumed-current-session"}), json_events=args.json_events)
        else:
            state = {"phase": "start", "provider": args.provider, "model": model, "tool_failures": 0}
            store.create(bindings, state=state)
        dispatcher = GuidedDispatcher(session=store, model_dir=model_dir, state=state)
        pending = state.get("pending_clarification")
        if args.resume and isinstance(pending, Mapping):
            if not answer_ref:
                event = store.append(
                    "SESSION_WAITING",
                    {
                        "session_status": "WAITING_FOR_CLARIFICATION",
                        "finding_id": pending["finding_id"],
                        "answer_refs": sorted(pending["answers"]),
                    },
                )
                _emit(event, json_events=args.json_events)
                if not args.json_events:
                    print("Session status: WAITING_FOR_CLARIFICATION")
                return 0
            state["confirmed_answer_ref"] = answer_ref
            store.update_state(state)
            review_request = ToolRequest(
                tool_id="record-review",
                arguments=ReviewRecordArguments(
                    preview_ref=str(pending["preview_id"]),
                    finding_ref=str(pending["finding_id"]),
                    answer_ref=answer_ref,
                ),
            )
            _emit(
                store.append(
                    "CLARIFICATION_ANSWERED",
                    {
                        "finding_id": pending["finding_id"],
                        "answer_ref": answer_ref,
                    },
                ),
                json_events=args.json_events,
            )
            _emit(
                store.append("TOOL_REQUESTED", {"tool_id": "record-review"}),
                json_events=args.json_events,
            )
            recorded = dispatcher.dispatch(review_request)
            store.update_state(state)
            _emit(
                store.append(
                    "TOOL_COMPLETED",
                    {
                        "tool_id": "record-review",
                        "status": recorded["status"],
                        "reason_code": recorded["reason_code"],
                    },
                ),
                json_events=args.json_events,
            )
        prompt = PROMPT.read_text(encoding="utf-8")
        for _ in range(MAX_TURNS):
            try:
                turn = provider.next_turn(prompt=prompt, context=_context(state))
            except GuidedConversionError as exc:
                if exc.code == "PROVIDER_MALFORMED_OUTPUT" and state.get("malformed_retries", 0) < 1:
                    state["malformed_retries"] = 1; store.update_state(state); continue
                raise
            if PROHIBITED_TEXT.search(turn.message):
                raise GuidedConversionError("PROVIDER_OUTPUT_PROHIBITED", "Provider output crossed the executable or lifecycle boundary.")
            _emit(store.append("AGENT_TURN", {"intent": turn.intent, "message": turn.message, "checkpoint": turn.checkpoint}), json_events=args.json_events)
            if turn.intent == "ASK_CLARIFICATION":
                current = state.get("pending_clarification")
                if not isinstance(current, Mapping) or turn.clarification is None:
                    raise GuidedConversionError(
                        "CLARIFICATION_NOT_BOUND",
                        "The provider requested a clarification without a current deterministic finding.",
                    )
                offered_refs = set(current["answers"])
                requested_refs = {
                    item.answer_ref for item in turn.clarification.options
                }
                if (
                    turn.clarification.finding_id != current["finding_id"]
                    or requested_refs != offered_refs
                ):
                    raise GuidedConversionError(
                        "CLARIFICATION_OPTIONS_CHANGED",
                        "The provider changed the deterministic clarification options.",
                    )
                state["phase"] = "clarification"
                store.update_state(state)
                _emit(
                    store.append(
                        "SESSION_WAITING",
                        {
                            "session_status": "WAITING_FOR_CLARIFICATION",
                            "finding_id": current["finding_id"],
                            "answer_refs": sorted(offered_refs),
                        },
                    ),
                    json_events=args.json_events,
                )
                if not args.json_events:
                    print("Session status: WAITING_FOR_CLARIFICATION")
                return 0
            if turn.intent != "CALL_TOOL":
                state["phase"] = turn.checkpoint; store.update_state(state)
                _emit(store.append("CHECKPOINT", {"checkpoint": turn.checkpoint}), json_events=args.json_events)
                _emit(store.append("SESSION_COMPLETED", {"supported": bool(state.get("change_id")), "unresolved": state.get("unresolved", 0), "deployment": "NOT_PERFORMED"}), json_events=args.json_events)
                if not args.json_events:
                    print("Audience: SEMANTIC_ENGINEER\nInterface: INTERACTIVE_CLI\nSupported conversion: SAFE_CANDIDATE\nUnsupported change: MANUAL_REVIEW_REQUIRED\nDeployment: NOT_PERFORMED")
                return 0
            assert turn.tool_request is not None
            key = turn.tool_request.tool_id + ":" + _safe_json(turn.tool_request.arguments.model_dump(mode="json"))
            if key == state.get("last_tool"):
                raise GuidedConversionError("TOOL_LOOP_DETECTED", "The provider repeated an identical tool call.")
            state["last_tool"] = key; store.update_state(state)
            _emit(store.append("TOOL_REQUESTED", {"tool_id": turn.tool_request.tool_id}), json_events=args.json_events)
            try:
                result = dispatcher.dispatch(turn.tool_request)
            except GuidedConversionError:
                raise
            except Exception as exc:
                state["tool_failures"] = state.get("tool_failures", 0) + 1; store.update_state(state)
                if state["tool_failures"] > MAX_TOOL_FAILURES: raise GuidedConversionError("TOOL_FAILURE_BUDGET_EXHAUSTED", "Deterministic tool failure budget exhausted.") from exc
                raise GuidedConversionError("DETERMINISTIC_TOOL_FAILED", f"Deterministic tool failed with redacted type {type(exc).__name__}.") from exc
            store.update_state(state)
            _emit(store.append("TOOL_COMPLETED", {"tool_id": turn.tool_request.tool_id, "status": result["status"], "reason_code": result["reason_code"]}), json_events=args.json_events)
            _emit(store.append("CHECKPOINT", {"checkpoint": state["phase"]}), json_events=args.json_events)
        raise GuidedConversionError("TURN_BUDGET_EXHAUSTED", "The guided conversation exceeded its bounded turn budget.")
    except GuidedConversionError as exc:
        payload = {"status": "MANUAL_REVIEW_REQUIRED", "reason_code": exc.code, "message": str(exc)}
        print(_safe_json(payload) if getattr(args, "json_events", False) else f"{exc.code}: {exc}")
        return exc.exit_code
    except (PowerBIPathError, ValueError, OSError, RuntimeError) as exc:
        print(f"GUIDED_CONVERSION_FAILED: {type(exc).__name__}")
        return 3


__all__ = ["AgentTurn", "Clarification", "GuidedConversionError", "GuidedDispatcher", "GuidedProvider", "LiveProviderProbe", "OpenAIConfig", "OpenAIResponsesGuidedProvider", "ProviderDiagnostic", "ScriptedGuidedProvider", "SessionEvent", "SessionStore", "ToolRequest", "diagnose_openai_provider", "run_guide_conversion"]
