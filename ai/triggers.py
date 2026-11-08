"""Trigger names and whether V1 will run them.

The dispatcher lives in services so this module stays free of database imports.
Disabled triggers are still named so later jobs can register without a new concept.
"""

from core.enums import AnalysisTrigger

ALL_TRIGGERS = tuple(AnalysisTrigger)

# Wired in V1. The others are accepted by the dispatcher and recorded as skipped.
V1_IMPLEMENTED = {
    AnalysisTrigger.MANUAL_DEEP_DIVE,
    AnalysisTrigger.PRE_DECISION_REVIEW,
    AnalysisTrigger.DAILY_CLOSE_REVIEW,
    AnalysisTrigger.LARGE_PRICE_MOVE,
}
