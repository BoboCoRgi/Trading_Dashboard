from dataclasses import dataclass

from sqlalchemy.orm import Session

from sqlalchemy import select

from ai.providers.base import AIProviderError
from ai.providers.factory import get_provider
from ai.thinking import thinking_level_for
from ai.triggers import V1_IMPLEMENTED
from core.config import Settings, get_settings
from core.enums import AnalysisTrigger
from core.logging import redact_text
from db.models import AIRun
from services.ai_reuse import analysis_is_current, prompt_version_for, role_for
from services.ledger import latest_snapshot
from services.orchestrator import run_and_store


@dataclass(frozen=True)
class TriggerResult:
    trigger: AnalysisTrigger
    status: str
    detail: str


class TriggerDispatcher:
    """Named analysis events. Disabled triggers are acknowledged and not run."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def emit(
        self,
        session: Session,
        trigger: AnalysisTrigger,
        *,
        instrument_symbol: str | None = None,
        decision_id=None,
        allow_external: bool = False,
    ) -> TriggerResult:
        if trigger not in self.settings.trigger_set():
            return TriggerResult(trigger, "disabled", "Trigger is not in ENABLED_TRIGGERS")
        if trigger not in V1_IMPLEMENTED:
            return TriggerResult(trigger, "not_implemented", "The trigger is named but has no V1 handler")
        provider_choice = self.settings.ai_provider.strip().lower()
        if provider_choice == "gemini" and not self.settings.has_gemini:
            return TriggerResult(trigger, "unavailable", "Gemini is selected, but GEMINI_API_KEY is not set.")
        external = provider_choice in {"auto", "anthropic", "openai", "gemini"}
        if external and not allow_external:
            return TriggerResult(
                trigger,
                "needs_confirmation",
                "Set the confirmation flag before sending context to an external model",
            )
        from db.models import Instrument

        instrument_id = None
        if instrument_symbol:
            instrument = session.scalar(select(Instrument).where(Instrument.symbol == instrument_symbol))
            if instrument is None:
                return TriggerResult(trigger, "error", "unknown instrument")
            instrument_id = instrument.id
        try:
            provider = get_provider(self.settings, thinking_level=thinking_level_for(trigger))
        except AIProviderError:
            return TriggerResult(trigger, "unavailable", "AI analysis temporarily unavailable.")
        if provider.provider_name != "mock" and not allow_external:
            return TriggerResult(
                trigger,
                "needs_confirmation",
                "Set the confirmation flag before sending context to an external model",
            )
        snapshot = latest_snapshot(session)
        if snapshot is not None and _reuse(session, trigger, snapshot.id, instrument_id, provider.provider_name):
            return TriggerResult(trigger, "stored", "Using the stored analysis for this snapshot.")
        daily = trigger is AnalysisTrigger.DAILY_CLOSE_REVIEW
        try:
            runs = run_and_store(
                session,
                provider,
                trigger,
                instrument_id=instrument_id,
                decision_id=decision_id,
                daily=daily,
            )
        except Exception as exc:
            session.rollback()
            from services.ai_store import store_failure

            store_failure(
                session,
                trigger=trigger,
                provider_name=provider.provider_name,
                model_name=provider.model_name,
                thinking_level=getattr(provider, "thinking_level", None),
                snapshot_id=None if snapshot is None else snapshot.id,
                instrument_id=instrument_id,
                decision_id=decision_id,
                detail=redact_text(str(exc)),
            )
            return TriggerResult(trigger, "unavailable", "AI analysis temporarily unavailable.")
        return TriggerResult(trigger, "completed", f"{len(runs)} analysis rows stored")


def _reuse(session, trigger, snapshot_id, instrument_id, provider_name: str) -> bool:
    prompt = prompt_version_for(trigger)
    role = role_for(trigger)
    if prompt is None or role is None:
        return False
    statement = select(AIRun).where(
        AIRun.trigger == trigger,
        AIRun.role == role,
        AIRun.portfolio_snapshot_id == snapshot_id,
        AIRun.prompt_version == prompt,
        AIRun.provider == provider_name,
        AIRun.status == "completed",
    )
    if instrument_id is not None:
        statement = statement.where(AIRun.instrument_id == instrument_id)
    existing = session.scalar(statement.order_by(AIRun.created_at.desc()))
    if existing is None:
        return False
    return analysis_is_current(
        stored_snapshot_id=existing.portfolio_snapshot_id,
        current_snapshot_id=snapshot_id,
        stored_prompt=existing.prompt_version,
        current_prompt=prompt,
        stored_provider=existing.provider,
        current_provider=provider_name,
        stored_status=existing.status,
    )
