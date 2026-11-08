"""Decide when a stored analysis can be shown again without a new model call."""

from core.enums import AIRole, AnalysisTrigger

_PROMPT = {
    AnalysisTrigger.DAILY_CLOSE_REVIEW: "daily-review-v1",
    AnalysisTrigger.MANUAL_DEEP_DIVE: "deep-dive-v1",
    AnalysisTrigger.PRE_DECISION_REVIEW: "deep-dive-v1",
}


def prompt_version_for(trigger: AnalysisTrigger) -> str | None:
    return _PROMPT.get(trigger)


def role_for(trigger: AnalysisTrigger) -> AIRole | None:
    if trigger is AnalysisTrigger.DAILY_CLOSE_REVIEW:
        return AIRole.DAILY_BRIEF
    if trigger in {AnalysisTrigger.MANUAL_DEEP_DIVE, AnalysisTrigger.PRE_DECISION_REVIEW}:
        return AIRole.ANALYST
    return None


def should_invoke_model(*, button_clicked: bool, cached: bool) -> bool:
    """A browser refresh is not a click, and a stored result is not a new call."""
    return bool(button_clicked) and not cached


def analysis_is_current(
    *,
    stored_snapshot_id,
    current_snapshot_id,
    stored_prompt: str | None,
    current_prompt: str | None,
    stored_provider: str | None,
    current_provider: str,
    stored_status: str | None = "completed",
) -> bool:
    return (
        stored_status in {None, "completed"}
        and stored_snapshot_id is not None
        and stored_snapshot_id == current_snapshot_id
        and stored_prompt == current_prompt
        and stored_provider == current_provider
    )
