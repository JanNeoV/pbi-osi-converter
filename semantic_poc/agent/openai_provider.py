"""Official OpenAI Responses API adapter with redacted failure handling."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from typing import Any, Mapping, Protocol

from .interpretation import InterpretationEnvelope, MalformedStructuredOutput


MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 1
MAX_OUTPUT_TOKENS = 1200


class AgentConfigurationError(ValueError):
    code = "SEMANTIC_AGENT_CONFIGURATION_ERROR"


class AgentProviderError(RuntimeError):
    code = "SEMANTIC_AGENT_PROVIDER_ERROR"


class AgentRefusalError(AgentProviderError):
    code = "SEMANTIC_AGENT_REFUSAL"


@dataclass(frozen=True)
class AgentConfig:
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AgentConfig":
        source = os.environ if environ is None else environ
        api_key = source.get("OPENAI_API_KEY", "").strip()
        model = source.get("SEMANTIC_AGENT_MODEL", "").strip()
        if not api_key:
            raise AgentConfigurationError("OPENAI_API_KEY is required for natural-language commands.")
        if not model:
            raise AgentConfigurationError("SEMANTIC_AGENT_MODEL is required for natural-language commands.")
        if not MODEL_PATTERN.fullmatch(model):
            raise AgentConfigurationError("SEMANTIC_AGENT_MODEL is not a safe model identifier.")
        timeout_text = source.get("SEMANTIC_AGENT_TIMEOUT_SECONDS", "").strip()
        retries_text = source.get("SEMANTIC_AGENT_MAX_RETRIES", "").strip()
        try:
            timeout = float(timeout_text) if timeout_text else DEFAULT_TIMEOUT_SECONDS
        except ValueError as exc:
            raise AgentConfigurationError("SEMANTIC_AGENT_TIMEOUT_SECONDS must be numeric.") from exc
        try:
            max_retries = int(retries_text) if retries_text else DEFAULT_MAX_RETRIES
        except ValueError as exc:
            raise AgentConfigurationError("SEMANTIC_AGENT_MAX_RETRIES must be 0 or 1.") from exc
        if not 1 <= timeout <= 120:
            raise AgentConfigurationError("SEMANTIC_AGENT_TIMEOUT_SECONDS must be between 1 and 120.")
        if max_retries not in {0, 1}:
            raise AgentConfigurationError("SEMANTIC_AGENT_MAX_RETRIES must be 0 or 1.")
        return cls(api_key=api_key, model=model, timeout_seconds=timeout, max_retries=max_retries)


class InterpretationProvider(Protocol):
    max_retries: int

    def parse(self, *, instructions: str, context: Mapping[str, Any]) -> Any: ...


class OpenAIResponsesProvider:
    """Lazily imports and uses only the official OpenAI Python SDK."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig.from_env()
        self.max_retries = self.config.max_retries
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AgentConfigurationError(
                'The optional OpenAI SDK is unavailable; install with python -m pip install -e ".[agent]".'
            ) from exc
        # SDK retries are deliberately disabled.  The orchestrator owns the one
        # optional malformed-output retry and therefore caps calls and cost.
        self._client = OpenAI(
            api_key=self.config.api_key,
            timeout=self.config.timeout_seconds,
            max_retries=0,
        )

    def parse(self, *, instructions: str, context: Mapping[str, Any]) -> Any:
        try:
            response = self._client.responses.parse(
                model=self.config.model,
                instructions=instructions,
                input=json.dumps(context, sort_keys=True, ensure_ascii=False),
                text_format=InterpretationEnvelope,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
        except Exception as exc:  # SDK error bodies/headers are never surfaced.
            module = type(exc).__module__
            if module == "pydantic" or module.startswith("pydantic.") or isinstance(exc, json.JSONDecodeError):
                raise MalformedStructuredOutput(
                    "The response could not be parsed into the strict interpretation schema."
                ) from None
            if module == "openai" or module.startswith("openai."):
                raise AgentProviderError(
                    f"OpenAI request failed with redacted diagnostic type {type(exc).__name__}."
                ) from None
            raise AgentProviderError(
                f"Provider request failed with redacted diagnostic type {type(exc).__name__}."
            ) from None
        parsed = getattr(response, "output_parsed", None)
        if parsed is not None:
            return parsed
        for output in getattr(response, "output", ()) or ():
            for item in getattr(output, "content", ()) or ():
                if getattr(item, "type", None) == "refusal":
                    raise AgentRefusalError("The configured model refused the interpretation request.")
        raise MalformedStructuredOutput("The response contained no parsed structured interpretation.")
