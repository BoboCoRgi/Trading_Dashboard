import json

from pydantic import BaseModel

from ai.pricing import estimate_cost_usd
from ai.prompts.v1 import PROMPT_VERSION
from ai.providers.base import AIProvider, AIResult, Usage
from ai.schemas import DISCLAIMER, AnalysisResponse, Claim
from core.enums import ClaimType


def _block(prompt: str, start: str, end: str) -> dict | None:
    if start not in prompt:
        return None
    body = prompt.split(start, 1)[1].split(end, 1)[0].strip()
    if not body:
        return None
    return json.loads(body)


def _claim(
    text: str,
    claim_type: ClaimType,
    confidence: float,
    source: str | None = None,
    counter: str | None = None,
    source_timestamp: str | None = None,
) -> Claim:
    return Claim(
        text=text,
        claim_type=claim_type,
        confidence=confidence,
        sources=[source] if source else [],
        source_timestamp=source_timestamp,
        counter_evidence=counter,
    )


def _unknown(text: str) -> Claim:
    return _claim(text, ClaimType.UNKNOWN, 0.0, counter="No source was supplied for this point.")


def _metric_facts(context: dict) -> list[Claim]:
    totals = context.get("totals") or {}
    facts: list[Claim] = []
    mapping = (
        ("total_gbp", "Portfolio value"),
        ("cash_gbp", "Cash"),
        ("invested_gbp", "Invested value"),
        ("daily_pnl_gbp", "Daily P&L"),
        ("drawdown_from_ath", "Drawdown from the portfolio high"),
        ("top1_concentration", "Largest instrument weight"),
        ("top3_concentration", "Top 3 weight"),
        ("top5_concentration", "Top 5 weight"),
    )
    timestamp = totals.get("snapshot_time")
    for key, label in mapping:
        if key not in totals or totals[key] in (None, ""):
            facts.append(_unknown(f"{label} was not in the calculated context."))
            continue
        extra = ""
        if key == "top1_concentration" and totals.get("largest_symbol"):
            extra = f" The largest symbol is {totals['largest_symbol']}."
        facts.append(
            _claim(
                f"{label} is {totals[key]}.{extra} This figure was copied from the calculation layer.",
                ClaimType.FACT,
                1.0,
                source="portfolio_snapshot",
                source_timestamp=str(timestamp) if timestamp else None,
            )
        )
    return facts


def _missing() -> list[Claim]:
    return [
        _unknown("No filings, news, or primary documents were provided."),
        _unknown("Whether the stored thesis is correct is UNKNOWN."),
        _unknown("Live broker state was not queried. These figures come from the supplied snapshot."),
    ]


class MockProvider(AIProvider):
    """Rule-based stand-in. It copies calculated figures and refuses to invent evidence."""

    provider_name = "mock"

    def __init__(
        self,
        *,
        voice: str = "primary",
        provider_label: str = "mock",
        model_name: str = "mock-1",
        reference_model: str = "claude-sonnet-4-5",
    ) -> None:
        super().__init__()
        self.voice = voice
        self.provider_name = provider_label
        self.model_name = model_name
        self.reference_model = reference_model

    def structured_response(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> AIResult:
        if schema is not AnalysisResponse:
            raise TypeError("mock provider only builds AnalysisResponse")
        context = _block(user_prompt, "CONTEXT_JSON", "END_CONTEXT") or {}
        prior = _block(user_prompt, "PRIOR_JSON", "END_PRIOR")
        role = "ANALYST"
        for candidate in ("DAILY_BRIEF", "SYNTHESIS", "RISK_OFFICER", "SKEPTIC", "ANALYST"):
            if f"ROLE: {candidate}" in system_prompt:
                role = candidate
                break
        builders = {
            "ANALYST": _analyst,
            "SKEPTIC": _skeptic,
            "RISK_OFFICER": _risk,
            "SYNTHESIS": _synthesis,
            "DAILY_BRIEF": _daily,
        }
        structured = builders[role](context, prior, self.voice)
        raw = structured.model_dump_json()
        input_tokens = max(1, len(system_prompt + user_prompt) // 4)
        output_tokens = max(1, len(raw) // 4)
        cost, note = estimate_cost_usd(self.reference_model, input_tokens, output_tokens)
        result = AIResult(
            provider=self.provider_name,
            model=self.model_name,
            model_version="mock-1",
            prompt_version=PROMPT_VERSION,
            structured=structured,
            raw_text=raw,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
                cost_note=f"MOCK response. {note}. No API call was made.",
            ),
        )
        return self._remember(result)


def _thesis_fact(context: dict) -> Claim | None:
    thesis = context.get("thesis")
    if not thesis:
        return None
    return _claim(
        f"A stored thesis titled '{thesis.get('title', 'untitled')}' has status {thesis.get('status', 'UNKNOWN')}. "
        "The text is the user's writing, not external evidence.",
        ClaimType.FACT,
        1.0,
        source="thesis",
        source_timestamp=thesis.get("updated_at"),
    )


def _event_facts(context: dict) -> list[Claim]:
    facts = []
    for event in context.get("market_events") or []:
        facts.append(
            _claim(
                f"{event.get('symbol')} price move {event.get('price_change')}, "
                f"portfolio impact {event.get('impact_gbp')} ({event.get('impact_pct')}). "
                f"Stored classification is {event.get('classification')}. No news source was supplied.",
                ClaimType.FACT,
                1.0,
                source="market_event",
                counter="A price move is not a cause.",
            )
        )
    return facts


def _opportunity_claims(context: dict, empty: str) -> list[Claim]:
    rows = context.get("opportunity_candidates") or []
    if not rows:
        return [_unknown(empty)]
    claims = []
    for row in rows:
        claims.append(
            _claim(
                f"{row.get('symbol')} was flagged by a screen. {row.get('why')} "
                f"Portfolio fit is {row.get('fit')}. This is not a recommendation.",
                ClaimType.FACT,
                1.0,
                source="opportunity_screen",
                counter=row.get("why_market"),
            )
        )
    return claims


def _thesis_judgement(context: dict) -> str:
    if context.get("market_events"):
        return "UNCERTAIN"
    return (context.get("thesis") or {}).get("status", "UNKNOWN")


def _analyst(context: dict, prior: dict | None, voice: str) -> AnalysisResponse:
    del prior
    facts = _metric_facts(context)
    facts.extend(_event_facts(context))
    thesis_claim = _thesis_fact(context)
    if thesis_claim:
        facts.append(thesis_claim)
    opinion = (
        "The stored bull case is a hypothesis to test, not a forecast."
        if voice == "primary"
        else "I would treat the stored bull case as a claim that still needs a source outside the journal."
    )
    changes = [item.get("message", "") for item in context.get("changes") or []]
    if context.get("market_events"):
        changes = [
            (
                f"{event.get('symbol')} moved {event.get('price_change')}. "
                f"Portfolio impact {event.get('impact_gbp')}. "
                f"Stored classification is {event.get('classification')}. No cause is attached."
            )
            for event in context["market_events"]
        ] + changes
    return AnalysisResponse(
        summary="Analyst reading of the calculated snapshot and the stored notes. No external evidence was added.",
        what_changed=changes,
        important_facts=facts,
        portfolio_risks=[
            _claim(
                "Position size and scenario impact are already in the context. Those are the portfolio facts an analyst should not talk past.",
                ClaimType.INFERENCE,
                0.7,
                source="scenario_engine",
                counter="A scenario is not a prediction of what will happen.",
            )
        ],
        opportunities=_opportunity_claims(
            context,
            "No research queue beyond the stored watchlist and theses was supplied, so no opportunity is asserted.",
        ),
        bull_case=[_claim(opinion, ClaimType.OPINION, 0.3, counter="The bear case is also only a stored note.")],
        bear_case=[
            _claim(
                "The bear case in the thesis, if present, is a stored scenario rather than evidence that it is underway.",
                ClaimType.OPINION,
                0.3,
            )
        ],
        hidden_assumptions=[
            _claim(
                "A common assumption is that the stored thesis still describes the position. The context does not prove that.",
                ClaimType.INFERENCE,
                0.5,
            )
        ],
        counter_evidence=[_unknown("No counter-evidence from filings or news was supplied.")],
        missing_information=_missing(),
        thesis_status=_thesis_judgement(context),
        decision_consistency="UNKNOWN" if not context.get("decision") else (
            f"The stored decision type is {context['decision'].get('decision_type', 'UNKNOWN')}. "
            "Consistency with the thesis is a judgement, not a calculated fact."
        ),
        what_would_change_the_view=[
            "A primary source that supports or breaks the stored invalidation condition.",
            "A material change in the calculated weight, drawdown, or scenario impact.",
        ],
        areas_of_agreement=[],
        areas_of_disagreement=[],
        confidence=0.34,
        disclaimer=DISCLAIMER,
    )


def _skeptic(context: dict, prior: dict | None, voice: str) -> AnalysisResponse:
    analyst_summary = (prior or {}).get("analyst_summary") or "The analyst output was not supplied."
    top1 = (context.get("totals") or {}).get("top1_concentration")
    concentration_note = (
        f"The calculated largest weight is {top1}. Size alone is a reason to challenge a bullish reading."
        if top1 not in (None, "")
        else "Largest weight was not supplied."
    )
    opinion = (
        "Disagreeing with the analyst is justified only where the context shows a gap. The gap here is evidence, not tone."
        if voice == "primary"
        else "The analyst is working from stored notes. Stored notes can repeat a view without testing it."
    )
    return AnalysisResponse(
        summary=f"Skeptical review. Analyst said: {analyst_summary}",
        what_changed=[item.get("message", "") for item in context.get("changes") or []],
        important_facts=_metric_facts(context) + _event_facts(context),
        portfolio_risks=[
            _claim(concentration_note, ClaimType.INFERENCE, 0.65, source="concentration", counter="Weight is not the same thing as a bad business.")
        ],
        opportunities=[_unknown("No opportunity is being endorsed.")],
        bull_case=[_claim(opinion, ClaimType.OPINION, 0.4)],
        bear_case=[
            _claim(
                "Alternative explanation: recent price strength, if any, may be in the mock path rather than new information. No news was provided.",
                ClaimType.INFERENCE,
                0.45,
                counter="The price path alone does not identify the cause.",
            )
        ],
        hidden_assumptions=[
            _claim(
                "The analyst treats the stored thesis as the frame. That frame may be the thing that is wrong.",
                ClaimType.INFERENCE,
                0.55,
            )
        ],
        counter_evidence=[
            _claim(
                "Nothing in the context independently corroborates the thesis. Absence of corroboration is the counter-evidence.",
                ClaimType.FACT,
                0.9,
                source="context_inventory",
            )
        ]
        + (
            [
                _claim(
                    "A difference versus other holdings is context. It does not establish a company-specific cause.",
                    ClaimType.INFERENCE,
                    0.6,
                    counter="The peer move can itself be unexplained.",
                )
            ]
            if context.get("market_events")
            else []
        ),
        missing_information=_missing(),
        thesis_status=_thesis_judgement(context),
        decision_consistency="UNKNOWN",
        what_would_change_the_view=["A source that is not the original thesis and that addresses the invalidation condition."],
        confidence=0.41,
        disclaimer=DISCLAIMER,
    )


def _risk(context: dict, prior: dict | None, voice: str) -> AnalysisResponse:
    del prior, voice
    scenarios = context.get("scenarios") or []
    risks: list[Claim] = []
    for scenario in scenarios:
        risks.append(
            _claim(
                f"Scenario '{scenario.get('name')}' has calculated portfolio impact {scenario.get('portfolio_impact')}. "
                f"Assumption: {scenario.get('assumption')}",
                ClaimType.FACT,
                1.0,
                source="scenario_engine",
            )
        )
    if not risks:
        risks.append(_unknown("No scenario results were supplied."))
    alerts = context.get("alerts") or []
    for alert in alerts:
        risks.append(
            _claim(
                f"Risk rule {alert.get('code')}: {alert.get('message')}",
                ClaimType.FACT,
                1.0,
                source="risk_rules",
            )
        )
    return AnalysisResponse(
        summary="Portfolio impact if the thesis is wrong, using the calculated weights and scenarios. Business quality is ignored.",
        what_changed=[item.get("message", "") for item in context.get("changes") or []],
        important_facts=_metric_facts(context) + _event_facts(context),
        portfolio_risks=risks,
        opportunities=[_unknown("The risk role does not identify opportunities.")],
        bull_case=[_unknown("Whether the company is good is outside this role.")],
        bear_case=[
            _claim(
                "The downside that matters here is the portfolio impact already calculated, not a new price target.",
                ClaimType.OPINION,
                0.5,
            )
        ],
        hidden_assumptions=[
            _claim(
                "Theme and index scenarios assume the shocks in their labels. They do not estimate correlation or beta from history.",
                ClaimType.FACT,
                1.0,
                source="scenario_assumption",
            )
        ],
        counter_evidence=[_unknown("No independent evidence was supplied about whether the shock will occur.")],
        missing_information=_missing()
        + [_unknown("Correlation, factor exposure, and event calendars were not calculated in this version.")],
        thesis_status="UNKNOWN",
        decision_consistency="The risk reading does not confirm or reject the decision. It states the calculated portfolio impact.",
        what_would_change_the_view=["A smaller calculated weight, more cash, or a scenario impact inside the threshold."],
        confidence=0.62,
        disclaimer=DISCLAIMER,
    )


def _synthesis(context: dict, prior: dict | None, voice: str) -> AnalysisResponse:
    del voice
    prior = prior or {}
    agreement = [
        "Calculated portfolio figures can be quoted as facts.",
        "No filings or news were in the context, so the truth of the thesis is UNKNOWN.",
    ]
    disagreement = [
        "How much weight to give the stored thesis: the analyst uses it as a frame, the skeptic treats that as the risk.",
    ]
    if prior.get("risk_summary"):
        agreement.append("The risk reading uses scenario impact rather than a view on the company.")
    return AnalysisResponse(
        summary="Synthesis of the three readings. This does not select a trade.",
        what_changed=[item.get("message", "") for item in context.get("changes") or []],
        important_facts=_metric_facts(context),
        portfolio_risks=[
            _claim(
                "Portfolio implications are the calculated concentration, drawdown, cash, and scenario impacts. They are not restated as a new number here.",
                ClaimType.FACT,
                1.0,
                source="calculation_layer",
            )
        ],
        opportunities=[_unknown("No opportunity is asserted without a source.")],
        bull_case=[
            _claim(
                "Evidence supporting the thesis: only the thesis text itself was supplied. That is not independent support.",
                ClaimType.FACT,
                1.0,
                source="thesis",
            )
        ],
        bear_case=[
            _claim(
                "Evidence challenging the thesis: none was supplied, which leaves the thesis untested.",
                ClaimType.INFERENCE,
                0.6,
                counter="Lack of evidence in this packet is not evidence the thesis is false.",
            )
        ],
        hidden_assumptions=[
            _claim(
                "Assumption: the snapshot and the thesis describe the same situation. Check the timestamps.",
                ClaimType.INFERENCE,
                0.5,
            )
        ],
        counter_evidence=[_unknown("No external counter-evidence was in the packet.")],
        missing_information=_missing(),
        thesis_status=(context.get("thesis") or {}).get("status", "UNKNOWN"),
        decision_consistency=(
            "UNKNOWN"
            if not context.get("decision")
            else "The decision is recorded. This synthesis does not upgrade it into an instruction."
        ),
        what_would_change_the_view=[
            "Independent evidence on the invalidation condition.",
            "A calculated breach or a clear of the concentration and drawdown rules.",
        ],
        areas_of_agreement=agreement,
        areas_of_disagreement=disagreement,
        confidence=0.36,
        disclaimer=DISCLAIMER,
    )


def _daily(context: dict, prior: dict | None, voice: str) -> AnalysisResponse:
    del prior, voice
    changes = [item.get("message", "") for item in context.get("changes") or []]
    if not changes:
        changes = ["No prior snapshot was supplied, so there is no calculated change to describe."]
    return AnalysisResponse(
        summary="Daily interpretation of the calculation layer. Uncertain where the packet is silent.",
        what_changed=changes,
        important_facts=_metric_facts(context) + _event_facts(context),
        portfolio_risks=[
            _claim(alert.get("message", "Alert"), ClaimType.FACT, 1.0, source="risk_rules")
            for alert in (context.get("alerts") or [])
        ]
        or [_unknown("No risk alerts were supplied.")],
        opportunities=_opportunity_claims(
            context,
            "Research candidates are not inferred. Use the watchlist and theses already stored.",
        ),
        bull_case=[_unknown("A daily brief does not create a bull case.")],
        bear_case=[_unknown("A daily brief does not create a bear case.")],
        hidden_assumptions=[
            _claim(
                "Why a change matters depends on the thesis. If no thesis was supplied, the importance is UNKNOWN.",
                ClaimType.INFERENCE,
                0.5,
            )
        ],
        counter_evidence=[_unknown("No news packet was supplied to challenge yesterday's view.")],
        missing_information=_missing(),
        thesis_status="UNKNOWN",
        decision_consistency="This brief does not review a specific decision unless one was included.",
        what_would_change_the_view=["A primary source, or a threshold breach that is not already in the alerts."],
        confidence=0.33,
        disclaimer=DISCLAIMER,
    )
