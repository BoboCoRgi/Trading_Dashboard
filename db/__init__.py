from db.base import Base
from db.models.tables import (  # noqa: F401 — register metadata
    AIClaim,
    AIRun,
    BrokerAccount,
    BrokerSyncStatus,
    CashMovement,
    CorporateAction,
    Catalyst,
    Decision,
    DecisionOutcome,
    Instrument,
    JournalEntry,
    PortfolioSnapshot,
    PositionSnapshot,
    PriceObservation,
    RawBrokerEvent,
    ResearchNote,
    Thesis,
    Trade,
    WatchlistItem,
)

__all__ = ["Base"]
