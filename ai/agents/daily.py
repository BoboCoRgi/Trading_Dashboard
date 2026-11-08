from ai.context import sanitize_context
from ai.prompts.daily_review_v1 import DAILY_SYSTEM, PROMPT_VERSION
from ai.prompts.v1 import render_user
from ai.providers.base import AIProvider, AIResult


def run_daily_brief(provider: AIProvider, context: dict) -> AIResult:
    safe = sanitize_context(context)
    return provider.analyse(system_prompt=DAILY_SYSTEM, user_prompt=render_user(safe), prompt_version=PROMPT_VERSION)


__all__ = ["PROMPT_VERSION", "run_daily_brief"]
