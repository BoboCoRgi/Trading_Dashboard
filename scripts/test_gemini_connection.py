"""Check that Gemini can return the analysis schema. No portfolio data is sent."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.providers.base import AIProviderError
from ai.providers.gemini import GeminiProvider
from ai.schemas import AnalysisResponse
from core.config import get_settings


def main() -> int:
    settings = get_settings()
    if not settings.has_gemini:
        print("Gemini connection: FAILED")
        print("GEMINI_API_KEY is not set.")
        return 1
    provider = GeminiProvider(
        api_key=settings.gemini_api_key,
        model_name=settings.gemini_model,
        thinking_level="low",
    )
    try:
        result = provider.structured_response(
            system_prompt=(
                "Connection test only. No portfolio, account, or market data is included. "
                "Return a structured analysis. summary must be exactly 'connection ok'. "
                "Use UNKNOWN for every claim. Do not invent a market event. "
                "This is not a trade instruction."
            ),
            user_prompt="No portfolio data is supplied. Confirm the structured response.",
            schema=AnalysisResponse,
        )
    except AIProviderError:
        print("Gemini connection: FAILED")
        print("Structured output: FAIL")
        return 1
    print("Gemini connection: OK")
    print("Model:")
    print(result.model)
    print("Structured output:")
    print("PASS" if isinstance(result.structured, AnalysisResponse) else "FAIL")
    return 0 if isinstance(result.structured, AnalysisResponse) else 1


if __name__ == "__main__":
    raise SystemExit(main())
