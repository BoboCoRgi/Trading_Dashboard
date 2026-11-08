"""Gemini stays behind the existing provider contract. No live API key is used."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ai.context import UnsafeAIContext, minimise_context, sanitize_context
from ai.prompts.v1 import render_user
from ai.providers.base import AIProviderError
from ai.providers.factory import get_provider
from ai.providers.gemini import GeminiProvider, _failure_kind
from ai.providers.mock import MockProvider
from ai.schemas import AnalysisResponse, Claim
from ai.thinking import thinking_level_for
from core.config import Settings
from core.enums import AnalysisTrigger, ClaimType
from services.ai_reuse import analysis_is_current, should_invoke_model


SECRET = "AIzaSyTESTKEY1234567890"


def _settings(**overrides) -> Settings:
    values = {"ai_provider": "mock", "gemini_api_key": "", "anthropic_api_key": "", "openai_api_key": ""}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _response() -> str:
    body = AnalysisResponse(
        summary="Current causal evidence is insufficient.",
        missing_information=[
            Claim(text="No news was supplied.", claim_type=ClaimType.UNKNOWN, confidence=0, source_reference="context")
        ],
        confidence=0.2,
    )
    return body.model_dump_json()


def test_gemini_maps_structured_output_and_does_not_send_the_key():
    seen = {}

    def transport(system_prompt, user_prompt, schema, thinking_level):
        seen["user"] = user_prompt
        seen["level"] = thinking_level
        assert schema is AnalysisResponse
        return SimpleNamespace(
            text=_response(),
            model_version="gemini-3.8-flash",
            usage_metadata=SimpleNamespace(prompt_token_count=11, candidates_token_count=7),
        )

    provider = GeminiProvider(api_key=SECRET, model_name="gemini-3.8-flash", thinking_level="high", transport=transport)
    context = minimise_context(
        {
            "totals": {"daily_pnl_gbp": "-5.00"},
            "positions": [{"symbol": "MU", "account_display_name": "ISA-998877", "portfolio_weight": "0.2"}],
            "api_key": SECRET,
            "executions": [{"fill": index} for index in range(12)],
        }
    )
    result = provider.analyse(system_prompt="ROLE: ANALYST", user_prompt=render_user(context), prompt_version="deep-dive-v1")
    assert result.provider == "gemini"
    assert result.model == "gemini-3.8-flash"
    assert result.prompt_version == "deep-dive-v1"
    assert result.thinking_level == "high"
    assert result.structured.missing_information[0].claim_type is ClaimType.UNKNOWN
    assert result.structured.missing_information[0].sources == ["context"]
    assert result.structured.is_trading_instruction is False
    assert SECRET not in seen["user"]
    assert "ISA-998877" not in seen["user"]
    assert "account_display_name" not in seen["user"]
    assert seen["level"] == "high"


def test_missing_gemini_key_is_explicit():
    with pytest.raises(AIProviderError, match="GEMINI_API_KEY"):
        GeminiProvider(api_key="", model_name="gemini-3.8-flash")
    with pytest.raises(AIProviderError, match="GEMINI_API_KEY"):
        get_provider(_settings(ai_provider="gemini"))


def test_mock_provider_still_resolves():
    provider = get_provider(_settings(ai_provider="mock"))
    assert isinstance(provider, MockProvider)


def test_timeout_is_retried_and_rate_limit_is_not():
    timeouts = {"calls": 0}

    def timeout_transport(*_args):
        timeouts["calls"] += 1
        raise TimeoutError("connection timed out")

    provider = GeminiProvider(api_key=SECRET, transport=timeout_transport)
    with pytest.raises(AIProviderError):
        provider.structured_response(system_prompt="s", user_prompt="u", schema=AnalysisResponse)
    assert timeouts["calls"] == 3

    limited = {"calls": 0}

    def rate_transport(*_args):
        limited["calls"] += 1
        raise RuntimeError("429 rate limit")

    provider = GeminiProvider(api_key=SECRET, transport=rate_transport)
    with pytest.raises(AIProviderError):
        provider.structured_response(system_prompt="s", user_prompt="u", schema=AnalysisResponse)
    assert limited["calls"] == 1
    assert _failure_kind(RuntimeError("429 rate limit")) == "rate"
    assert _failure_kind(TimeoutError("timed out")) == "transient"


def test_invalid_structured_output_is_rejected_once():
    calls = {"n": 0}

    def transport(*_args):
        calls["n"] += 1
        return SimpleNamespace(text="not json", usage_metadata=None)

    provider = GeminiProvider(api_key=SECRET, transport=transport)
    with pytest.raises(AIProviderError, match="schema"):
        provider.structured_response(system_prompt="s", user_prompt="u", schema=AnalysisResponse)
    assert calls["n"] == 1


def test_sanitised_context_drops_account_identifiers_and_long_executions():
    cleaned = minimise_context(
        {
            "positions": [{"symbol": "RKLB", "account_number": "123456", "portfolio_weight": "0.8"}],
            "executions": [{"id": "trade-1"} for _ in range(10)],
        }
    )
    assert cleaned["positions"] == [{"symbol": "RKLB", "portfolio_weight": "0.8"}]
    assert cleaned["executions"] == {"count": 10, "note": "Individual executions were omitted."}
    with pytest.raises(UnsafeAIContext):
        sanitize_context({"note": "bearer supersecrettoken"})


def test_thinking_levels_follow_the_trigger():
    assert thinking_level_for(AnalysisTrigger.DAILY_CLOSE_REVIEW) == "medium"
    assert thinking_level_for(AnalysisTrigger.MANUAL_DEEP_DIVE) == "high"
    assert thinking_level_for(AnalysisTrigger.PRE_DECISION_REVIEW) == "high"


def test_daily_review_is_reused_until_the_snapshot_changes():
    assert analysis_is_current(
        stored_snapshot_id="snap-1",
        current_snapshot_id="snap-1",
        stored_prompt="daily-review-v1",
        current_prompt="daily-review-v1",
        stored_provider="gemini",
        current_provider="gemini",
    )
    assert not analysis_is_current(
        stored_snapshot_id="snap-1",
        current_snapshot_id="snap-2",
        stored_prompt="daily-review-v1",
        current_prompt="daily-review-v1",
        stored_provider="gemini",
        current_provider="gemini",
    )


def test_streamlit_rerender_does_not_request_a_new_call():
    assert should_invoke_model(button_clicked=False, cached=False) is False
    assert should_invoke_model(button_clicked=True, cached=True) is False
    assert should_invoke_model(button_clicked=True, cached=False) is True


def test_schema_rejects_a_non_numeric_confidence():
    with pytest.raises(ValidationError):
        AnalysisResponse(summary="Noted.", confidence="high")
