"""Load the local mock book through the same path live events will use."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai.providers.mock import MockProvider
from connectors.ibkr import IBKRReadConnector
from connectors.trading212 import Trading212ReadConnector
from core.enums import (
    AnalysisTrigger,
    CatalystStatus,
    DataOrigin,
    DecisionStatus,
    DecisionType,
    ThesisStatus,
    WatchlistStatus,
)
from core.money import D
from db.models import Catalyst, Instrument, JournalEntry, ResearchNote, Thesis, Trade, WatchlistItem
from services.decisions import DecisionInput, create_decision
from services.ledger import _account_rows, _instrument_rows, ingest_connector, load_prices, rebuild_snapshots, reset_database
from services.orchestrator import run_and_store


def _at(year: int, month: int, day: int, hour: int = 15, minute: int = 30) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _symbol_id(session: Session, symbol: str):
    return session.scalar(select(Instrument.id).where(Instrument.symbol == symbol))


def _trade_id(session: Session, broker_trade_id: str):
    return session.scalar(select(Trade.id).where(Trade.broker_trade_id == broker_trade_id))


def _thesis(session: Session, **kwargs) -> Thesis:
    row = Thesis(data_origin=DataOrigin.MOCK, **kwargs)
    session.add(row)
    session.flush()
    return row


def seed(session: Session) -> None:
    reset_database(session)
    session.add_all(_instrument_rows())
    accounts = _account_rows()
    session.add_all(accounts.values())
    session.flush()
    ingest_connector(session, Trading212ReadConnector(), accounts["t212"])
    ingest_connector(session, IBKRReadConnector(), accounts["ibkr"])
    load_prices(session)
    session.flush()
    rebuild_snapshots(session)

    rklb = _thesis(
        session,
        instrument_id=_symbol_id(session, "RKLB"),
        created_at=_at(2026, 2, 10, 12, 0),
        updated_at=_at(2026, 8, 5, 18, 0),
        title="MOCK DATA — Launch cadence before Neutron",
        thesis_summary="MOCK DATA. The position exists because launch cadence and a later vehicle were the stated reason. This text is a journal fixture, not a research report.",
        time_horizon="18-36 months",
        bull_case="MOCK DATA. Cadence stays high enough that the current book of business is not the whole story.",
        base_case="MOCK DATA. The position works only if execution stays ahead of the cash the business needs.",
        bear_case="MOCK DATA. A slip in the later vehicle, or a funding need, breaks the reason the size is this large.",
        catalysts="MOCK DATA. No live launch calendar is connected. The stored catalyst is a placeholder window, not a date from a filing.",
        invalidation_conditions="MOCK DATA. Invalidate if the later vehicle slips without a stated funding path, or if the position stays above the concentration threshold without a fresh review.",
        status=ThesisStatus.ACTIVE,
    )
    mu = _thesis(
        session,
        instrument_id=_symbol_id(session, "MU"),
        created_at=_at(2026, 3, 12, 12, 0),
        updated_at=_at(2026, 7, 1, 18, 0),
        title="MOCK DATA — Memory cycle, size kept smaller than RKLB",
        thesis_summary="MOCK DATA. Held as a cycle exposure. The fixture does not claim to know where in the cycle we are.",
        time_horizon="6-18 months",
        bull_case="MOCK DATA. Supply stays tight and the book was bought before the move was finished.",
        base_case="MOCK DATA. The cycle pays, but the entry is no longer early.",
        bear_case="MOCK DATA. A downcycle reprices the position faster than the thesis can be updated.",
        catalysts="MOCK DATA. Earnings window is a placeholder, not a confirmed date.",
        invalidation_conditions="MOCK DATA. Invalidate if inventory builds and pricing commentary reverses.",
        status=ThesisStatus.ACTIVE,
    )
    meta = _thesis(
        session,
        instrument_id=_symbol_id(session, "META"),
        created_at=_at(2026, 2, 2, 12, 0),
        updated_at=_at(2026, 8, 20, 18, 0),
        title="MOCK DATA — Hold the advertising compounder",
        thesis_summary="MOCK DATA. A hold means the original reason is still the reason. It is not a new buy case.",
        time_horizon="3-5 years",
        bull_case="MOCK DATA. Cash generation continues to fund the spending.",
        base_case="MOCK DATA. The position is left alone unless the invalidation condition prints.",
        bear_case="MOCK DATA. Spending rises and the advertising engine stops covering it.",
        catalysts="MOCK DATA. No live earnings date is stored.",
        invalidation_conditions="MOCK DATA. Invalidate if core advertising growth and the spending plan diverge for two reported periods.",
        status=ThesisStatus.ACTIVE,
    )
    mrvl = _thesis(
        session,
        instrument_id=_symbol_id(session, "MRVL"),
        created_at=_at(2026, 1, 20, 12, 0),
        updated_at=_at(2026, 7, 15, 18, 0),
        title="MOCK DATA — Do not add",
        thesis_summary="MOCK DATA. The original custom-silicon note is weakened. The pass is the decision not to average up.",
        time_horizon="12 months",
        bull_case="MOCK DATA. Design wins could still show up in the numbers later.",
        base_case="MOCK DATA. The existing shares are kept. New cash is not added on this note.",
        bear_case="MOCK DATA. The customer set is narrower than the original note assumed.",
        catalysts="MOCK DATA. Customer commentary is unknown until a real filing is attached.",
        invalidation_conditions="MOCK DATA. The add is allowed only after a primary source, not after the price rises.",
        status=ThesisStatus.WEAKENED,
    )
    nok = _thesis(
        session,
        instrument_id=_symbol_id(session, "NOK"),
        created_at=_at(2026, 3, 1, 12, 0),
        updated_at=_at(2026, 8, 12, 18, 0),
        title="MOCK DATA — Telecom stub, partly sold",
        thesis_summary="MOCK DATA. The original reason was a small, separate exposure. Part of it was sold. The rest is no longer a high-conviction hold.",
        time_horizon="closed as a conviction",
        bull_case="MOCK DATA. A restructuring could still matter. That was not why the remainder was kept.",
        base_case="MOCK DATA. The remainder is a leftover, not a fresh decision to own it.",
        bear_case="MOCK DATA. The stub keeps using attention that belongs on the concentrated names.",
        catalysts="MOCK DATA. None stored.",
        invalidation_conditions="MOCK DATA. Already acted on: the conviction was closed by the partial sale.",
        status=ThesisStatus.CLOSED,
    )

    session.add_all(
        [
            Catalyst(
                thesis_id=rklb.id,
                instrument_id=rklb.instrument_id,
                description="MOCK DATA. Placeholder: a later-vehicle milestone. Not taken from a live calendar.",
                expected_window="MOCK window, not a confirmed date",
                status=CatalystStatus.UNKNOWN,
                data_origin=DataOrigin.MOCK,
            ),
            Catalyst(
                thesis_id=mu.id,
                instrument_id=mu.instrument_id,
                description="MOCK DATA. Placeholder: next reported inventory and pricing commentary.",
                expected_window="MOCK window, not a confirmed earnings date",
                status=CatalystStatus.UNKNOWN,
                data_origin=DataOrigin.MOCK,
            ),
            Catalyst(
                thesis_id=mrvl.id,
                instrument_id=mrvl.instrument_id,
                description="MOCK DATA. Placeholder: a primary customer disclosure before any add.",
                expected_window="UNKNOWN",
                status=CatalystStatus.UNKNOWN,
                data_origin=DataOrigin.MOCK,
            ),
        ]
    )
    session.add_all(
        [
            WatchlistItem(
                instrument_id=_symbol_id(session, "VRT"),
                notes="MOCK DATA. Held, and still on the watchlist because the power-and-cooling note is unfinished.",
                status=WatchlistStatus.ACTIVE,
                data_origin=DataOrigin.MOCK,
            ),
            WatchlistItem(
                instrument_id=_symbol_id(session, "NOK"),
                notes="MOCK DATA. Watched only to decide whether the leftover stub should be closed.",
                status=WatchlistStatus.ACTIVE,
                data_origin=DataOrigin.MOCK,
            ),
        ]
    )
    session.add_all(
        [
            ResearchNote(
                instrument_id=rklb.instrument_id,
                thesis_id=rklb.id,
                title="MOCK DATA — What would count as evidence",
                body="MOCK DATA. A launch count from the company, a funding note, and the position weight after the latest buy. Price action is not the evidence.",
                created_at=_at(2026, 6, 18, 20, 0),
                data_origin=DataOrigin.MOCK,
            ),
            ResearchNote(
                instrument_id=mrvl.instrument_id,
                thesis_id=mrvl.id,
                title="MOCK DATA — Why the add was refused",
                body="MOCK DATA. The price was higher and the note had no new primary source. Passing is a decision. It should stay in the ledger.",
                created_at=_at(2026, 7, 15, 20, 0),
                data_origin=DataOrigin.MOCK,
            ),
        ]
    )

    decisions = [
        create_decision(
            session,
            DecisionInput(
                instrument_id=rklb.instrument_id,
                thesis_id=rklb.id,
                decision_type=DecisionType.BUY,
                proposed_action="Buy the first RKLB block in the ISA.",
                actual_action="Bought. See the linked fill.",
                reasoning="MOCK DATA. The size was intentional. The later review has to live with that, not rewrite it.",
                thesis_text=rklb.thesis_summary,
                catalyst=rklb.catalysts,
                expected_horizon="18-36 months",
                expected_upside_percent=D(80),
                expected_downside_percent=D(40),
                invalidation_condition=rklb.invalidation_conditions,
                user_confidence=D("0.62"),
                linked_trade_id=_trade_id(session, "MOCK-T212-RKLB-BUY-2026-02-10"),
                what_i_thought="MOCK DATA. Cadence was the reason. Concentration was accepted, not unnoticed.",
                what_i_expected="MOCK DATA. A volatile path. The journal does not record a price target as a fact.",
                what_would_prove_me_wrong=rklb.invalidation_conditions,
                status=DecisionStatus.EXECUTED,
                created_at=_at(2026, 2, 10),
            ),
        ),
        create_decision(
            session,
            DecisionInput(
                instrument_id=mu.instrument_id,
                thesis_id=mu.id,
                decision_type=DecisionType.BUY,
                proposed_action="Buy MU as a smaller cycle position.",
                actual_action="Bought. See the linked fill.",
                reasoning="MOCK DATA. Sized below RKLB on purpose so one memory name cannot dominate the book.",
                thesis_text=mu.thesis_summary,
                catalyst=mu.catalysts,
                expected_horizon="6-18 months",
                expected_upside_percent=D(35),
                expected_downside_percent=D(30),
                invalidation_condition=mu.invalidation_conditions,
                user_confidence=D("0.55"),
                linked_trade_id=_trade_id(session, "MOCK-IBKR-MU-BUY-2026-03-12"),
                what_i_thought="MOCK DATA. This is a cycle, so the hold period is shorter than RKLB.",
                what_i_expected="MOCK DATA. I expected the position to be volatile and the weight to stay in the teens or below.",
                what_would_prove_me_wrong=mu.invalidation_conditions,
                status=DecisionStatus.EXECUTED,
                created_at=_at(2026, 3, 12),
            ),
        ),
        create_decision(
            session,
            DecisionInput(
                instrument_id=meta.instrument_id,
                thesis_id=meta.id,
                decision_type=DecisionType.HOLD,
                proposed_action="Do not add and do not sell.",
                actual_action="No order.",
                reasoning="MOCK DATA. Nothing in the stored note invalidated the original reason. Holding is the decision.",
                thesis_text=meta.thesis_summary,
                catalyst=meta.catalysts,
                expected_horizon="3-5 years",
                expected_upside_percent=D(25),
                expected_downside_percent=D(20),
                invalidation_condition=meta.invalidation_conditions,
                user_confidence=D("0.58"),
                what_i_thought="MOCK DATA. Activity would have been a new decision. I did not have one.",
                what_i_expected="MOCK DATA. The position can drift without requiring a trade.",
                what_would_prove_me_wrong=meta.invalidation_conditions,
                status=DecisionStatus.OPEN,
                created_at=_at(2026, 8, 20, 22, 0),
            ),
        ),
        create_decision(
            session,
            DecisionInput(
                instrument_id=mrvl.instrument_id,
                thesis_id=mrvl.id,
                decision_type=DecisionType.PASS,
                proposed_action="Do not add to MRVL.",
                actual_action="No order.",
                reasoning="MOCK DATA. The price was not a reason. The missing primary source was the reason.",
                thesis_text=mrvl.thesis_summary,
                catalyst=mrvl.catalysts,
                expected_horizon="review in 6 months",
                expected_upside_percent=D(20),
                expected_downside_percent=D(25),
                invalidation_condition=mrvl.invalidation_conditions,
                user_confidence=D("0.48"),
                what_i_thought="MOCK DATA. Passing should be reviewable later, including if the shares rise without me.",
                what_i_expected="MOCK DATA. I might feel late. That feeling is not evidence.",
                what_would_prove_me_wrong="MOCK DATA. A primary customer disclosure that restores the original add case.",
                status=DecisionStatus.CLOSED,
                created_at=_at(2026, 7, 15, 22, 0),
            ),
        ),
        create_decision(
            session,
            DecisionInput(
                instrument_id=_symbol_id(session, "VRT"),
                decision_type=DecisionType.WATCH,
                proposed_action="Keep VRT on the list. Do not add size yet.",
                actual_action="No new order on this date.",
                reasoning="MOCK DATA. The power-and-cooling exposure is already owned. More size needs a separate note.",
                thesis_text="MOCK DATA. No standalone thesis beyond the watchlist note.",
                catalyst="MOCK DATA. None confirmed.",
                expected_horizon="90 days",
                expected_upside_percent=D(15),
                expected_downside_percent=D(20),
                invalidation_condition="MOCK DATA. Drop the watch if the note is still only a theme label.",
                user_confidence=D("0.40"),
                what_i_thought="MOCK DATA. Watching is not a hidden buy.",
                what_i_expected="MOCK DATA. Either a real note appears or the watch expires.",
                what_would_prove_me_wrong="MOCK DATA. Treating a theme label as if it were evidence.",
                status=DecisionStatus.OPEN,
                created_at=_at(2026, 9, 1, 22, 0),
            ),
        ),
        create_decision(
            session,
            DecisionInput(
                instrument_id=nok.instrument_id,
                thesis_id=nok.id,
                decision_type=DecisionType.SELL,
                proposed_action="Sell part of the NOK stub.",
                actual_action="Sold. See the linked fill.",
                reasoning="MOCK DATA. The position had become a distraction relative to its weight.",
                thesis_text=nok.thesis_summary,
                catalyst="MOCK DATA. None.",
                expected_horizon="immediate",
                expected_upside_percent=D(5),
                expected_downside_percent=D(15),
                invalidation_condition=nok.invalidation_conditions,
                user_confidence=D("0.70"),
                linked_trade_id=_trade_id(session, "MOCK-T212-NOK-SELL-2026-08-12"),
                what_i_thought="MOCK DATA. Small positions can still be wrong if they consume the review.",
                what_i_expected="MOCK DATA. The remainder is a leftover, not a new thesis.",
                what_would_prove_me_wrong="MOCK DATA. If the stub becomes a researched position again, that is a new decision.",
                status=DecisionStatus.EXECUTED,
                created_at=_at(2026, 8, 12),
            ),
        ),
    ]

    provider = MockProvider()
    run_and_store(session, provider, AnalysisTrigger.DAILY_CLOSE_REVIEW, daily=True)
    rklb_runs = run_and_store(
        session,
        provider,
        AnalysisTrigger.PRE_DECISION_REVIEW,
        instrument_id=rklb.instrument_id,
        decision_id=decisions[0].id,
    )
    run_and_store(
        session,
        provider,
        AnalysisTrigger.MANUAL_DEEP_DIVE,
        instrument_id=mrvl.instrument_id,
        decision_id=decisions[3].id,
    )
    synthesis = next(run for run in rklb_runs if run.role.value == "SYNTHESIS")
    journal = session.scalar(select(JournalEntry).where(JournalEntry.decision_id == decisions[0].id))
    journal.ai_run_id = synthesis.id
    from services.scan import refresh_scan

    refresh_scan(session)
