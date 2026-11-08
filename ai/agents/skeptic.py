from ai.context import sanitize_context
from ai.prompts.skeptic_v1 import PROMPT_VERSION, SKEPTIC_SYSTEM
from ai.prompts.v1 import render_user
from ai.providers.base import AIProvider, AIResult
from ai.schemas import AnalysisResponse


def run_skeptic(provider: AIProvider, context: dict, analyst: AnalysisResponse) -> AIResult:
    safe = sanitize_context(context)
    prior = {"analyst_summary": analyst.summary, "analyst_confidence": analyst.confidence}
    return provider.analyse(system_prompt=SKEPTIC_SYSTEM, user_prompt=render_user(safe, prior), prompt_version=PROMPT_VERSION)


__all__ = ["PROMPT_VERSION", "run_skeptic"]
