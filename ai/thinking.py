"""Thinking depth by analysis trigger. Roles stay independent of the provider."""

from core.enums import AnalysisTrigger

_LEVELS = {
    AnalysisTrigger.DAILY_CLOSE_REVIEW: "medium",
    AnalysisTrigger.MANUAL_DEEP_DIVE: "high",
    AnalysisTrigger.PRE_DECISION_REVIEW: "high",
    AnalysisTrigger.LARGE_PRICE_MOVE: "medium",
}


def thinking_level_for(trigger: AnalysisTrigger) -> str:
    return _LEVELS.get(trigger, "low")
