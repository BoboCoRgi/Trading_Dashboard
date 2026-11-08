"""Canonical schema.

Enums are stored as strings so Alembic revisions stay simple.
Money and quantities use Numeric. Weights and returns are fractions, not percents.
Drawdown is zero or negative: (value - ATH) / ATH.
external_account_ref is never copied into AI context.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.enums import (
    AIRole,
    AnalysisTrigger,
    AssetType,
    Broker,
    CashMovementType,
    CatalystStatus,
    CorporateActionType,
    FxConversionStatus,
    ClaimType,
    AliasType,
    DataOrigin,
    DecisionStatus,
    DecisionType,
    OutcomeHorizon,
    OutcomeStatus,
    ProcessingStatus,
    SnapshotSource,
    EventAnalysisStatus,
    EventClassification,
    EventTriggerType,
    FitLevel,
    OpportunityStatus,
    OpportunityType,
    ResearchPriorityLevel,
    ThesisStatus,
    TradeAction,
    WatchlistStatus,
    AttentionPriority,
)
from db.base import Base

MONEY = Numeric(24, 8)


def _enum(enum_cls: type, name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=64,
        values_callable=lambda members: [member.value for member in members],
    )


def _now() -> datetime:
    return datetime.now().astimezone()


class BrokerAccount(Base):
    __tablename__ = "broker_account"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    broker: Mapped[Broker] = mapped_column(_enum(Broker, "broker_code"))
    account_type: Mapped[str] = mapped_column(String(64))
    base_currency: Mapped[str] = mapped_column(String(8))
    display_name: Mapped[str] = mapped_column(String(128))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Real broker account numbers stay here and must not be sent to a model.
    external_account_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    trades: Mapped[list["Trade"]] = relationship(back_populates="account")


class Instrument(Base):
    __tablename__ = "instrument"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    symbol: Mapped[str] = mapped_column(String(32), unique=True)
    company_name: Mapped[str] = mapped_column(String(256))
    asset_type: Mapped[AssetType] = mapped_column(_enum(AssetType, "asset_type"))
    exchange: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8))
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ibkr_conid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trading212_ticker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    theme: Mapped[str] = mapped_column(String(64))
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_instrument"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InstrumentAlias(Base):
    """A ticker that has referred to one instrument.

    ``symbol`` on the instrument is the current display ticker. A historical
    ticker stays here. ``valid_from`` and ``valid_to`` stay empty when the
    source does not give the change date.
    """

    __tablename__ = "instrument_alias"
    __table_args__ = (
        UniqueConstraint("instrument_id", "symbol", "alias_type", name="uq_instrument_alias"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alias_type: Mapped[AliasType] = mapped_column(_enum(AliasType, "instrument_alias_type"))
    source: Mapped[str] = mapped_column(String(64))

    instrument: Mapped[Instrument] = relationship()


class RawBrokerEvent(Base):
    __tablename__ = "raw_broker_event"
    __table_args__ = (UniqueConstraint("broker", "broker_event_id", name="uq_raw_broker_event_identity"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    broker: Mapped[Broker] = mapped_column(_enum(Broker, "broker_code_event"))
    broker_event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_payload_json: Mapped[dict] = mapped_column(JSONB)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        _enum(ProcessingStatus, "processing_status"),
        default=ProcessingStatus.PENDING,
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_event"))


class Trade(Base):
    __tablename__ = "trade"
    __table_args__ = (UniqueConstraint("account_id", "broker_trade_id", name="uq_trade_broker_trade"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("broker_account.id"))
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    action: Mapped[TradeAction] = mapped_column(_enum(TradeAction, "trade_action"))
    quantity: Mapped[Decimal] = mapped_column(MONEY)
    fill_price: Mapped[Decimal] = mapped_column(MONEY)
    trade_currency: Mapped[str] = mapped_column(String(8))
    fx_rate_to_gbp: Mapped[Decimal] = mapped_column(MONEY)
    gross_value_native: Mapped[Decimal] = mapped_column(MONEY)
    gross_value_gbp: Mapped[Decimal] = mapped_column(MONEY)
    fees_native: Mapped[Decimal] = mapped_column(MONEY)
    fees_gbp: Mapped[Decimal] = mapped_column(MONEY)
    fx_fee_native: Mapped[Decimal] = mapped_column(MONEY)
    fx_fee_gbp: Mapped[Decimal] = mapped_column(MONEY)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    broker_trade_id: Mapped[str] = mapped_column(String(128))
    source_event_id: Mapped[UUID] = mapped_column(ForeignKey("raw_broker_event.id"))
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_trade"))

    account: Mapped[BrokerAccount] = relationship(back_populates="trades")
    instrument: Mapped[Instrument] = relationship()
    source_event: Mapped[RawBrokerEvent] = relationship()


class CashMovement(Base):
    __tablename__ = "cash_movement"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("broker_account.id"))
    movement_type: Mapped[CashMovementType] = mapped_column(_enum(CashMovementType, "cash_movement_type"))
    amount_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    fx_conversion_status: Mapped[FxConversionStatus] = mapped_column(
        _enum(FxConversionStatus, "fx_conversion_status"),
        default=FxConversionStatus.NOT_REQUIRED,
        server_default=FxConversionStatus.NOT_REQUIRED.value,
    )
    amount_native: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    instrument_id: Mapped[UUID | None] = mapped_column(ForeignKey("instrument.id"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_trade_id: Mapped[UUID | None] = mapped_column(ForeignKey("trade.id"), nullable=True)
    source_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("raw_broker_event.id"), nullable=True)
    note: Mapped[str] = mapped_column(String(256), default="")
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_cash"))


class CorporateAction(Base):
    """Quantity-changing broker action that is not an investor buy or sell."""

    __tablename__ = "corporate_action"
    __table_args__ = (
        UniqueConstraint("account_id", "broker_event_id", name="uq_corporate_action_broker_event"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("broker_account.id"))
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    action_type: Mapped[CorporateActionType] = mapped_column(_enum(CorporateActionType, "corporate_action_type"))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quantity_delta: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    split_ratio_from: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    split_ratio_to: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    broker_event_id: Mapped[str] = mapped_column(String(128))
    source_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("raw_broker_event.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_corporate"))

    account: Mapped[BrokerAccount] = relationship()
    instrument: Mapped[Instrument] = relationship()
    source_event: Mapped[RawBrokerEvent | None] = relationship()


class PriceObservation(Base):
    __tablename__ = "price_observation"
    __table_args__ = (
        UniqueConstraint("instrument_id", "as_of", name="uq_price_observation_instrument_as_of"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(8))
    fx_rate_to_gbp: Mapped[Decimal] = mapped_column(MONEY)
    source: Mapped[str] = mapped_column(String(64))
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_price"))


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshot"
    __table_args__ = (
        Index(
            "uq_live_portfolio_snapshot_time",
            "snapshot_time",
            unique=True,
            postgresql_where=text("data_origin = 'LIVE'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    total_portfolio_value_gbp: Mapped[Decimal] = mapped_column(MONEY)
    cash_gbp: Mapped[Decimal] = mapped_column(MONEY)
    invested_value_gbp: Mapped[Decimal] = mapped_column(MONEY)
    daily_pnl_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    daily_pnl_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ath_portfolio_value_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    drawdown_from_ath: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    max_drawdown_to_date: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    top1_concentration: Mapped[Decimal] = mapped_column(MONEY)
    top3_concentration: Mapped[Decimal] = mapped_column(MONEY)
    top5_concentration: Mapped[Decimal] = mapped_column(MONEY)
    realised_pnl_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    risk_score: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    source: Mapped[SnapshotSource] = mapped_column(_enum(SnapshotSource, "snapshot_source"))
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_snapshot"))

    positions: Mapped[list["PositionSnapshot"]] = relationship(
        back_populates="portfolio_snapshot",
        cascade="all, delete-orphan",
    )


class PositionSnapshot(Base):
    __tablename__ = "position_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_snapshot_id",
            "account_id",
            "instrument_id",
            name="uq_position_snapshot_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    portfolio_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_snapshot.id"))
    account_id: Mapped[UUID] = mapped_column(ForeignKey("broker_account.id"))
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    quantity: Mapped[Decimal] = mapped_column(MONEY)
    market_price: Mapped[Decimal] = mapped_column(MONEY)
    market_price_currency: Mapped[str] = mapped_column(String(8))
    fx_rate_to_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    market_value_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    average_cost_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    cost_basis_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    broker_average_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    broker_average_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    unrealised_pnl_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    unrealised_pnl_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    realised_pnl_gbp: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    portfolio_weight: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_position"))

    portfolio_snapshot: Mapped[PortfolioSnapshot] = relationship(back_populates="positions")


class BrokerSyncStatus(Base):
    """Sanitised live-broker status. No credentials, account number, or payload."""

    __tablename__ = "broker_sync_status"
    __table_args__ = (UniqueConstraint("broker", name="uq_broker_sync_status_broker"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    broker: Mapped[Broker] = mapped_column(_enum(Broker, "broker_code_status"))
    connection_status: Mapped[str] = mapped_column(String(32))
    last_successful_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_snapshot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    position_reconciliation_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cost_basis_reconciliation_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    snapshot_integrity_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    position_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Thesis(Base):
    __tablename__ = "thesis"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_now
    )
    title: Mapped[str] = mapped_column(String(256))
    thesis_summary: Mapped[str] = mapped_column(Text)
    time_horizon: Mapped[str] = mapped_column(String(64))
    bull_case: Mapped[str] = mapped_column(Text)
    base_case: Mapped[str] = mapped_column(Text)
    bear_case: Mapped[str] = mapped_column(Text)
    catalysts: Mapped[str] = mapped_column(Text)
    invalidation_conditions: Mapped[str] = mapped_column(Text)
    status: Mapped[ThesisStatus] = mapped_column(_enum(ThesisStatus, "thesis_status"))
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_thesis"))

    instrument: Mapped[Instrument] = relationship()


class Catalyst(Base):
    __tablename__ = "catalyst"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    thesis_id: Mapped[UUID | None] = mapped_column(ForeignKey("thesis.id"), nullable=True)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    description: Mapped[str] = mapped_column(Text)
    expected_window: Mapped[str] = mapped_column(String(128))
    status: Mapped[CatalystStatus] = mapped_column(_enum(CatalystStatus, "catalyst_status"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_catalyst"))


class Decision(Base):
    __tablename__ = "decision"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_now
    )
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    thesis_id: Mapped[UUID | None] = mapped_column(ForeignKey("thesis.id"), nullable=True)
    decision_type: Mapped[DecisionType] = mapped_column(_enum(DecisionType, "decision_type"))
    proposed_action: Mapped[str] = mapped_column(Text)
    actual_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_trade_id: Mapped[UUID | None] = mapped_column(ForeignKey("trade.id"), nullable=True)
    portfolio_before_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("portfolio_snapshot.id"), nullable=True
    )
    portfolio_after_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("portfolio_snapshot.id"), nullable=True
    )
    thesis_text: Mapped[str] = mapped_column(Text, default="")
    catalyst: Mapped[str] = mapped_column(Text, default="")
    expected_horizon: Mapped[str] = mapped_column(String(64), default="")
    expected_upside_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    expected_downside_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    invalidation_condition: Mapped[str] = mapped_column(Text, default="")
    user_confidence: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[DecisionStatus] = mapped_column(_enum(DecisionStatus, "decision_status"))
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_decision"))

    instrument: Mapped[Instrument] = relationship()
    thesis: Mapped[Thesis | None] = relationship()


class JournalEntry(Base):
    __tablename__ = "journal_entry"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    decision_id: Mapped[UUID] = mapped_column(ForeignKey("decision.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    what_i_thought: Mapped[str] = mapped_column(Text)
    what_i_expected: Mapped[str] = mapped_column(Text)
    what_would_prove_me_wrong: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    ai_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_run.id", ondelete="SET NULL"), nullable=True)
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_journal"))


class DecisionOutcome(Base):
    __tablename__ = "decision_outcome"
    __table_args__ = (
        UniqueConstraint("decision_id", "horizon_code", name="uq_decision_outcome_horizon"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    decision_id: Mapped[UUID] = mapped_column(ForeignKey("decision.id"))
    horizon_code: Mapped[OutcomeHorizon] = mapped_column(_enum(OutcomeHorizon, "outcome_horizon"))
    trading_days: Mapped[int] = mapped_column()
    status: Mapped[OutcomeStatus] = mapped_column(_enum(OutcomeStatus, "outcome_status"))
    evaluated_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    end_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    instrument_return_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    method: Mapped[str] = mapped_column(String(64), default="price_return_marker")
    notes: Mapped[str] = mapped_column(Text, default="")
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_outcome"))


class AIRun(Base):
    __tablename__ = "ai_run"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    trigger: Mapped[AnalysisTrigger] = mapped_column(_enum(AnalysisTrigger, "analysis_trigger"))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64))
    portfolio_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("portfolio_snapshot.id"), nullable=True
    )
    decision_id: Mapped[UUID | None] = mapped_column(ForeignKey("decision.id"), nullable=True)
    instrument_id: Mapped[UUID | None] = mapped_column(ForeignKey("instrument.id"), nullable=True)
    thesis_id: Mapped[UUID | None] = mapped_column(ForeignKey("thesis.id"), nullable=True)
    role: Mapped[AIRole] = mapped_column(_enum(AIRole, "ai_role"))
    parent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_run.id"), nullable=True)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    cost_is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    cost_note: Mapped[str] = mapped_column(String(256), default="")
    thinking_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="completed", server_default="completed")
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_response_json: Mapped[dict] = mapped_column(JSONB)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_context_json: Mapped[dict] = mapped_column(JSONB)
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_ai_run"))

    claims: Mapped[list["AIClaim"]] = relationship(back_populates="ai_run", cascade="all, delete-orphan")


class AIClaim(Base):
    __tablename__ = "ai_claim"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ai_run_id: Mapped[UUID] = mapped_column(ForeignKey("ai_run.id"))
    claim_text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[ClaimType] = mapped_column(_enum(ClaimType, "claim_type"))
    confidence: Mapped[Decimal] = mapped_column(MONEY)
    supporting_evidence: Mapped[str] = mapped_column(Text, default="")
    counter_evidence: Mapped[str] = mapped_column(Text, default="")
    sources_json: Mapped[list] = mapped_column(JSONB, default=list)
    source_timestamp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    what_would_change_the_view: Mapped[str] = mapped_column(Text, default="")

    ai_run: Mapped[AIRun] = relationship(back_populates="claims")


class WatchlistItem(Base):
    __tablename__ = "watchlist_item"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"), unique=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[WatchlistStatus] = mapped_column(_enum(WatchlistStatus, "watchlist_status"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_watchlist"))


class ResearchNote(Base):
    __tablename__ = "research_note"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID | None] = mapped_column(ForeignKey("instrument.id"), nullable=True)
    thesis_id: Mapped[UUID | None] = mapped_column(ForeignKey("thesis.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_note"))


class MarketEvent(Base):
    __tablename__ = "market_event"
    __table_args__ = (UniqueConstraint("instrument_id", "event_date", name="uq_market_event_day"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_change_pct: Mapped[Decimal] = mapped_column(MONEY)
    portfolio_impact_gbp: Mapped[Decimal] = mapped_column(MONEY)
    portfolio_impact_pct: Mapped[Decimal] = mapped_column(MONEY)
    abnormality_score: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    trigger_type: Mapped[EventTriggerType] = mapped_column(_enum(EventTriggerType, "event_trigger_type"))
    trigger_types: Mapped[str] = mapped_column(String(128), default="")
    benchmark_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    benchmark_change_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    sector_or_peer_change_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    event_classification: Mapped[EventClassification] = mapped_column(
        _enum(EventClassification, "event_classification"),
        default=EventClassification.UNKNOWN,
    )
    priority: Mapped[AttentionPriority] = mapped_column(_enum(AttentionPriority, "attention_priority"))
    analysis_status: Mapped[EventAnalysisStatus] = mapped_column(
        _enum(EventAnalysisStatus, "event_analysis_status"),
        default=EventAnalysisStatus.NOT_REVIEWED,
    )
    linked_ai_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_run.id"), nullable=True)
    linked_thesis_id: Mapped[UUID | None] = mapped_column(ForeignKey("thesis.id"), nullable=True)
    linked_decision_id: Mapped[UUID | None] = mapped_column(ForeignKey("decision.id"), nullable=True)
    context_note: Mapped[str] = mapped_column(Text, default="")
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_market_event"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpportunityCandidate(Base):
    __tablename__ = "opportunity_candidate"
    __table_args__ = (UniqueConstraint("instrument_id", "detected_at", name="uq_opportunity_detected"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opportunity_type: Mapped[OpportunityType] = mapped_column(_enum(OpportunityType, "opportunity_type"))
    screen_score: Mapped[Decimal] = mapped_column(MONEY)
    price_change_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    relative_change_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    abnormality_score: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    reason_flagged: Mapped[str] = mapped_column(Text, default="")
    supporting_evidence: Mapped[str] = mapped_column(Text, default="")
    counter_evidence: Mapped[str] = mapped_column(Text, default="")
    portfolio_fit: Mapped[FitLevel] = mapped_column(_enum(FitLevel, "fit_level"))
    portfolio_fit_reason: Mapped[str] = mapped_column(Text, default="")
    research_priority: Mapped[ResearchPriorityLevel] = mapped_column(
        _enum(ResearchPriorityLevel, "research_priority_level")
    )
    confidence: Mapped[str] = mapped_column(String(16), default="LOW")
    status: Mapped[OpportunityStatus] = mapped_column(
        _enum(OpportunityStatus, "opportunity_status"),
        default=OpportunityStatus.NEW,
    )
    linked_market_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("market_event.id"), nullable=True)
    linked_ai_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_run.id"), nullable=True)
    linked_decision_id: Mapped[UUID | None] = mapped_column(ForeignKey("decision.id"), nullable=True)
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_opportunity"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpportunityOutcome(Base):
    """Forward returns stay empty until that many sessions exist after detection."""

    __tablename__ = "opportunity_outcome"
    __table_args__ = (UniqueConstraint("candidate_id", "horizon_code", name="uq_opportunity_outcome_horizon"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    candidate_id: Mapped[UUID] = mapped_column(ForeignKey("opportunity_candidate.id"))
    horizon_code: Mapped[OutcomeHorizon] = mapped_column(_enum(OutcomeHorizon, "opportunity_outcome_horizon"))
    status: Mapped[OutcomeStatus] = mapped_column(
        _enum(OutcomeStatus, "opportunity_outcome_status"),
        default=OutcomeStatus.PENDING,
    )
    price_at_detection: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    return_pct: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    max_forward_gain: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    max_forward_drawdown: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_opportunity_outcome"))


class MarketPriceBar(Base):
    """One OHLC bar. Volume is the volume of this bar, not a daily total and not a cumulative total."""

    __tablename__ = "market_price_bar"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "interval_code",
            "bar_start",
            "data_origin",
            name="uq_market_price_bar",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    bar_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    interval_code: Mapped[str] = mapped_column(String(8))
    open: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    high: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    low: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    close: Mapped[Decimal] = mapped_column(MONEY)
    volume: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(64))
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_price_bar"))


class BenchmarkMapping(Base):
    """Explicit benchmark symbols for one instrument. An empty mapping is not guessed."""

    __tablename__ = "benchmark_mapping"
    __table_args__ = (UniqueConstraint("instrument_id", "data_origin", name="uq_benchmark_mapping_instrument"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instrument.id"))
    market_benchmark_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sector_benchmark_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    definition: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data_origin: Mapped[DataOrigin] = mapped_column(_enum(DataOrigin, "data_origin_benchmark"))


Index(
    "uq_cash_movement_source_event",
    CashMovement.source_event_id,
    unique=True,
    postgresql_where=text("source_event_id IS NOT NULL"),
)
Index("ix_trade_executed_at", Trade.executed_at)
Index("ix_decision_created_at", Decision.created_at)
