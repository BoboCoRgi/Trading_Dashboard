from enum import Enum


class Broker(str, Enum):
    TRADING212 = "TRADING212"
    IBKR = "IBKR"


class DataOrigin(str, Enum):
    MOCK = "MOCK"
    LIVE = "LIVE"


class AliasType(str, Enum):
    CURRENT_TICKER = "CURRENT_TICKER"
    HISTORICAL_TICKER = "HISTORICAL_TICKER"


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class DecisionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WATCH = "WATCH"
    PASS = "PASS"


class DecisionStatus(str, Enum):
    OPEN = "OPEN"
    EXECUTED = "EXECUTED"
    CLOSED = "CLOSED"
    SUPERSEDED = "SUPERSEDED"


class ThesisStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WEAKENED = "WEAKENED"
    INVALIDATED = "INVALIDATED"
    CLOSED = "CLOSED"


class ProcessingStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    IGNORED = "IGNORED"


class SnapshotSource(str, Enum):
    SEED = "SEED"
    SCHEDULER = "SCHEDULER"
    MANUAL = "MANUAL"
    POST_TRADE = "POST_TRADE"


class FxConversionStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    NOT_REQUIRED = "NOT_REQUIRED"


class CorporateActionType(str, Enum):
    STOCK_SPLIT = "STOCK_SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    MERGER = "MERGER"
    SPINOFF = "SPINOFF"
    OTHER = "OTHER"


class CashMovementType(str, Enum):
    OPENING = "OPENING"
    TRADE = "TRADE"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    DIVIDEND = "DIVIDEND"
    FEE = "FEE"
    INTEREST = "INTEREST"
    TRANSFER = "TRANSFER"
    OTHER = "OTHER"


class ClaimType(str, Enum):
    FACT = "FACT"
    INFERENCE = "INFERENCE"
    OPINION = "OPINION"
    UNKNOWN = "UNKNOWN"


class AIRole(str, Enum):
    ANALYST = "ANALYST"
    SKEPTIC = "SKEPTIC"
    RISK_OFFICER = "RISK_OFFICER"
    SYNTHESIS = "SYNTHESIS"
    DAILY_BRIEF = "DAILY_BRIEF"
    DEBATE = "DEBATE"


class AnalysisTrigger(str, Enum):
    TRADE_DETECTED = "TRADE_DETECTED"
    POSITION_CONCENTRATION_CHANGED = "POSITION_CONCENTRATION_CHANGED"
    RISK_THRESHOLD_BREACHED = "RISK_THRESHOLD_BREACHED"
    LARGE_PRICE_MOVE = "LARGE_PRICE_MOVE"
    MANUAL_DEEP_DIVE = "MANUAL_DEEP_DIVE"
    PRE_DECISION_REVIEW = "PRE_DECISION_REVIEW"
    DAILY_CLOSE_REVIEW = "DAILY_CLOSE_REVIEW"
    WEEKLY_REVIEW = "WEEKLY_REVIEW"


class OutcomeHorizon(str, Enum):
    D1 = "D1"
    D5 = "D5"
    D20 = "D20"
    D60 = "D60"


class OutcomeStatus(str, Enum):
    PENDING = "PENDING"
    EVALUATED = "EVALUATED"


class WatchlistStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class CatalystStatus(str, Enum):
    UPCOMING = "UPCOMING"
    PASSED = "PASSED"
    UNKNOWN = "UNKNOWN"


class AssetType(str, Enum):
    EQUITY = "EQUITY"


class AttentionPriority(str, Enum):
    INFO = "INFO"
    WATCH = "WATCH"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EventTriggerType(str, Enum):
    ABSOLUTE_MOVE = "ABSOLUTE_MOVE"
    PORTFOLIO_IMPACT = "PORTFOLIO_IMPACT"
    ABNORMAL_MOVE = "ABNORMAL_MOVE"


class EventClassification(str, Enum):
    MARKET = "MARKET"
    SECTOR = "SECTOR"
    COMPANY = "COMPANY"
    MACRO = "MACRO"
    TECHNICAL = "TECHNICAL"
    UNKNOWN = "UNKNOWN"


class EventAnalysisStatus(str, Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    THESIS_REVIEW = "THESIS_REVIEW"
    REVIEWED = "REVIEWED"


class OpportunityType(str, Enum):
    POTENTIAL_OVERREACTION = "POTENTIAL_OVERREACTION"
    RELATIVE_DISLOCATION = "RELATIVE_DISLOCATION"
    POST_EVENT_MISPRICING = "POST_EVENT_MISPRICING"
    TECHNICAL_SELLOFF = "TECHNICAL_SELLOFF"
    THESIS_INTACT_RESET = "THESIS_INTACT_RESET"
    CATALYST_ASYMMETRY = "CATALYST_ASYMMETRY"


class OpportunityStatus(str, Enum):
    NEW = "NEW"
    RESEARCHING = "RESEARCHING"
    WATCHING = "WATCHING"
    REJECTED = "REJECTED"
    PROMOTED_TO_DECISION = "PROMOTED_TO_DECISION"
    EXPIRED = "EXPIRED"


class FitLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ResearchPriorityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
