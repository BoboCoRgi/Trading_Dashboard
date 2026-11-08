from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from ai.claims import iter_claims
from ai.debate import RoleRun
from ai.schemas import AnalysisResponse, Claim
from core.enums import AnalysisTrigger, ClaimType, DataOrigin
from core.money import D
from db.models import AIClaim, AIRun
from services.ai_reuse import prompt_version_for


def store_role_runs(
    session: Session,
    runs: list[RoleRun],
    *,
    trigger: AnalysisTrigger,
    context: dict,
    snapshot_id: UUID | None,
    decision_id: UUID | None,
    instrument_id: UUID | None,
    thesis_id: UUID | None,
    data_origin: DataOrigin,
) -> list[AIRun]:
    stored: list[AIRun] = []
    parent_id: UUID | None = None
    for index, role_run in enumerate(runs):
        result = role_run.result
        record = AIRun(
            trigger=trigger,
            provider=result.provider,
            model=result.model,
            model_version=result.model_version,
            prompt_version=result.prompt_version,
            portfolio_snapshot_id=snapshot_id,
            decision_id=decision_id,
            instrument_id=instrument_id,
            thesis_id=thesis_id,
            role=role_run.role,
            parent_run_id=None if index == 0 else parent_id,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            estimated_cost_usd=D(result.usage.estimated_cost_usd),
            cost_is_estimate=True,
            cost_note=result.usage.cost_note,
            thinking_level=result.thinking_level,
            status="completed",
            structured_response_json=result.structured.model_dump(mode="json"),
            raw_response=result.raw_text,
            input_context_json=context,
            data_origin=data_origin,
        )
        session.add(record)
        session.flush()
        if index == 0:
            parent_id = record.id
        change = "\n".join(result.structured.what_would_change_the_view)
        for _field, claim in iter_claims(result.structured):
            session.add(
                AIClaim(
                    ai_run_id=record.id,
                    claim_text=claim.text,
                    claim_type=ClaimType(claim.claim_type),
                    confidence=Decimal(str(claim.confidence)),
                    supporting_evidence=", ".join(claim.sources),
                    counter_evidence=claim.counter_evidence or "",
                    sources_json=claim.sources,
                    source_timestamp=claim.source_timestamp,
                    what_would_change_the_view=change,
                )
            )
        stored.append(record)
    return stored


def store_failure(
    session: Session,
    *,
    trigger: AnalysisTrigger,
    provider_name: str,
    model_name: str,
    thinking_level: str | None,
    snapshot_id: UUID | None,
    instrument_id: UUID | None,
    decision_id: UUID | None,
    detail: str,
) -> AIRun:
    """Keep the failed attempt without presenting it as a completed review."""
    unavailable = AnalysisResponse(
        summary="AI analysis temporarily unavailable.",
        missing_information=[
            Claim(
                text="The model did not return a usable analysis.",
                claim_type=ClaimType.UNKNOWN,
                confidence=0,
            )
        ],
        confidence=0,
    )
    record = AIRun(
        trigger=trigger,
        provider=provider_name,
        model=model_name,
        model_version=model_name,
        prompt_version=prompt_version_for(trigger) or "unavailable",
        portfolio_snapshot_id=snapshot_id,
        decision_id=decision_id,
        instrument_id=instrument_id,
        role=_failure_role(trigger),
        thinking_level=thinking_level,
        status="failed",
        error_detail=detail[:500],
        structured_response_json=unavailable.model_dump(mode="json"),
        raw_response=None,
        input_context_json={},
        data_origin=DataOrigin.MOCK if provider_name.startswith("mock") else DataOrigin.LIVE,
    )
    session.add(record)
    session.flush()
    return record


def _failure_role(trigger: AnalysisTrigger):
    from core.enums import AIRole

    if trigger is AnalysisTrigger.DAILY_CLOSE_REVIEW:
        return AIRole.DAILY_BRIEF
    return AIRole.ANALYST
