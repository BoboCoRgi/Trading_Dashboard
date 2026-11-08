from core.config import Settings, get_settings
from ai.providers.anthropic import AnthropicProvider
from ai.providers.base import AIProvider, AIProviderError
from ai.providers.gemini import GeminiProvider
from ai.providers.mock import MockProvider
from ai.providers.openai import OpenAIProvider


def _mock(settings: Settings, *, voice: str = "primary", label: str = "mock", model_name: str = "mock-1") -> MockProvider:
    return MockProvider(
        voice=voice,
        provider_label=label,
        model_name=model_name,
        reference_model=settings.cost_reference_model,
    )


def get_provider(
    settings: Settings | None = None,
    explicit: str | None = None,
    thinking_level: str | None = None,
) -> AIProvider:
    settings = settings or get_settings()
    choice = (explicit or settings.ai_provider).strip().lower()
    if choice == "gemini":
        if not settings.has_gemini:
            raise AIProviderError("Gemini is selected, but GEMINI_API_KEY is not set.")
        return _gemini(settings, thinking_level)
    if choice == "anthropic" and settings.has_anthropic:
        return AnthropicProvider(api_key=settings.anthropic_api_key, model_name=settings.anthropic_model)
    if choice == "openai" and settings.has_openai:
        return OpenAIProvider(api_key=settings.openai_api_key, model_name=settings.openai_model)
    if choice == "auto":
        if settings.has_anthropic:
            return AnthropicProvider(api_key=settings.anthropic_api_key, model_name=settings.anthropic_model)
        if settings.has_openai:
            return OpenAIProvider(api_key=settings.openai_api_key, model_name=settings.openai_model)
        if settings.has_gemini:
            return _gemini(settings, thinking_level)
    return _mock(settings)


def _gemini(settings: Settings, thinking_level: str | None) -> GeminiProvider:
    return GeminiProvider(
        api_key=settings.gemini_api_key,
        model_name=settings.gemini_model,
        thinking_level=thinking_level or settings.gemini_thinking_level,
    )


def configured_providers(settings: Settings | None = None) -> list[AIProvider]:
    """Providers that can actually be called. Empty when no keys are set."""
    settings = settings or get_settings()
    providers: list[AIProvider] = []
    if settings.has_anthropic:
        providers.append(AnthropicProvider(api_key=settings.anthropic_api_key, model_name=settings.anthropic_model))
    if settings.has_openai:
        providers.append(OpenAIProvider(api_key=settings.openai_api_key, model_name=settings.openai_model))
    if settings.has_gemini:
        providers.append(_gemini(settings, settings.gemini_thinking_level))
    return providers
