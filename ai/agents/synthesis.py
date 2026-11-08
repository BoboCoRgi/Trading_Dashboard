from ai.context import sanitize_context
from ai.prompts.v1 import PROMPT_VERSION, SYNTHESIS_SYSTEM, render_user
from ai.providers.base import AIProvider, AIResult
from ai.schemas import AnalysisResponse


def run_synthesis(
    provider: AIProvider,
    context: dict,
    analyst: AnalysisResponse,
    skeptic: AnalysisResponse,
    risk: AnalysisResponse,
) -> AIResult:
    safe = sanitize_context(context)
    prior = {
        "analyst_summary": analyst.summary,
        "skeptic_summary": skeptic.summary,
        "risk_summary": risk.summary,
        "analyst_confidence": analyst.confidence,
        "skeptic_confidence": skeptic.confidence,
        "risk_confidence": risk.confidence,
    }
    return provider.analyse(system_prompt=SYNTHESIS_SYSTEM, user_prompt=render_user(safe, prior))


__all__ = ["PROMPT_VERSION", "run_synthesis"]
