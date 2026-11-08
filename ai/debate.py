from dataclasses import dataclass

from ai.agents.analyst import run_analyst
from ai.agents.daily import run_daily_brief
from ai.agents.risk_officer import run_risk_officer
from ai.agents.skeptic import run_skeptic
from ai.agents.synthesis import run_synthesis
from ai.providers.base import AIProvider, AIResult
from core.enums import AIRole


@dataclass(frozen=True)
class RoleRun:
    role: AIRole
    result: AIResult


def run_debate(provider: AIProvider, context: dict) -> list[RoleRun]:
    analyst = run_analyst(provider, context)
    skeptic = run_skeptic(provider, context, analyst.structured)
    risk = run_risk_officer(provider, context, analyst.structured)
    synthesis = run_synthesis(provider, context, analyst.structured, skeptic.structured, risk.structured)
    return [
        RoleRun(AIRole.ANALYST, analyst),
        RoleRun(AIRole.SKEPTIC, skeptic),
        RoleRun(AIRole.RISK_OFFICER, risk),
        RoleRun(AIRole.SYNTHESIS, synthesis),
    ]


def run_brief(provider: AIProvider, context: dict) -> list[RoleRun]:
    return [RoleRun(AIRole.DAILY_BRIEF, run_daily_brief(provider, context))]
