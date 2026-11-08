"""Gemini adapter. Domain code sees AIResult, not the SDK response."""

import json
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from ai.pricing import estimate_cost_usd
from ai.prompts.v1 import PROMPT_VERSION
from ai.providers.base import AIProvider, AIProviderError, AIResult, Usage
from ai.schemas import AnalysisResponse
from core.logging import redact_text

_TRANSIENT_ATTEMPTS = 3


class GeminiProvider(AIProvider):
    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str = "gemini-3.8-flash",
        thinking_level: str = "medium",
        transport: Callable[..., object] | None = None,
    ) -> None:
        super().__init__()
        if not api_key or not api_key.strip():
            raise AIProviderError("Gemini is selected, but GEMINI_API_KEY is not set.")
        self.model_name = model_name
        self.thinking_level = thinking_level.strip().lower()
        self._api_key = api_key
        self._transport = transport

    def structured_response(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> AIResult:
        if self._api_key and self._api_key in f"{system_prompt}\n{user_prompt}":
            raise AIProviderError("Refusing to send the Gemini API key in the prompt.")
        response = self._call(system_prompt, user_prompt, schema)
        raw = _response_text(response)
        try:
            payload = json.loads(raw)
            structured = schema.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise AIProviderError("Gemini returned data that did not match the analysis schema.") from exc
        if not isinstance(structured, AnalysisResponse):
            raise AIProviderError("Unexpected schema from Gemini provider")
        input_tokens, output_tokens = _usage_counts(response)
        cost, note = estimate_cost_usd(self.model_name, input_tokens, output_tokens)
        model_version = getattr(response, "model_version", None) or self.model_name
        result = AIResult(
            provider=self.provider_name,
            model=self.model_name,
            model_version=str(model_version),
            prompt_version=PROMPT_VERSION,
            structured=structured,
            raw_text=raw,
            thinking_level=self.thinking_level,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
                cost_note=f"thinking={self.thinking_level}. {note}",
            ),
        )
        return self._remember(result)

    def _call(self, system_prompt: str, user_prompt: str, schema: type[BaseModel]) -> object:
        attempts = 0
        while True:
            attempts += 1
            try:
                if self._transport is not None:
                    return self._transport(system_prompt, user_prompt, schema, self.thinking_level)
                return _sdk_generate(self._api_key, self.model_name, self.thinking_level, system_prompt, user_prompt, schema)
            except AIProviderError:
                raise
            except Exception as exc:
                kind = _failure_kind(exc)
                if kind == "rate" or kind == "fatal" or attempts >= _TRANSIENT_ATTEMPTS:
                    raise AIProviderError(redact_text(str(exc) or "Gemini request failed.")) from exc


def _sdk_generate(api_key: str, model_name: str, thinking_level: str, system_prompt: str, user_prompt: str, schema: type[BaseModel]):
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise AIProviderError("Install google-genai to call Gemini.") from exc
    client = genai.Client(api_key=api_key)
    config_kwargs: dict = {
        "system_instruction": system_prompt,
        "response_mime_type": "application/json",
        "response_json_schema": schema.model_json_schema(),
    }
    thinking = _thinking_config(types, thinking_level)
    if thinking is not None:
        config_kwargs["thinking_config"] = thinking
    return client.models.generate_content(
        model=model_name,
        contents=user_prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )


def _thinking_config(types, thinking_level: str):
    config_type = getattr(types, "ThinkingConfig", None)
    if config_type is None:
        return None
    level = thinking_level.upper()
    enum = getattr(types, "ThinkingLevel", None)
    value = getattr(enum, level, thinking_level) if enum is not None else thinking_level
    try:
        return config_type(thinking_level=value)
    except TypeError:
        return config_type(thinking_level=thinking_level)


def _response_text(response: object) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    raise AIProviderError("Gemini returned data that did not match the analysis schema.")


def _usage_counts(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0
    return (
        int(getattr(usage, "prompt_token_count", 0) or 0),
        int(getattr(usage, "candidates_token_count", 0) or 0),
    )


def _failure_kind(exc: Exception) -> str:
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status == 429 or "resource_exhausted" in text or "rate limit" in text or "429" in text:
        return "rate"
    if isinstance(exc, TimeoutError) or "timeout" in text or status in {500, 502, 503, 504}:
        return "transient"
    return "fatal"
