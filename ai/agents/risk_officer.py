from ai.context import sanitize_context
from ai.prompts.v1 import PROMPT_VERSION, RISK_SYSTEM, render_user
from ai.providers.base import AIProvider, AIResult
from ai.schemas import AnalysisResponse


def run_risk_officer(provider: AIProvider, context: dict, analyst: AnalysisResponse) -> AIResult:
    safe = sanitize_context(context)
    prior = {"analyst_summary": analyst.summary}
    return provider.analyse(system_prompt=RISK_SYSTEM, user_prompt=render_user(safe, prior))


__all__ = ["PROMPT_VERSION", "run_risk_officer"]
