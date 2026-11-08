from decimal import Decimal
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.enums import AnalysisTrigger
from core.money import D


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://invest:invest@localhost:5432/investment_intelligence"
    reporting_currency: str = "GBP"
    snapshot_interval_minutes: int = 15

    ai_provider: str = "mock"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    openai_model: str = "gpt-4.1-mini"
    gemini_model: str = "gemini-3.8-flash"
    gemini_thinking_level: str = "medium"
    cost_reference_model: str = "claude-sonnet-4-5"

    enabled_triggers: str = "MANUAL_DEEP_DIVE,PRE_DECISION_REVIEW,DAILY_CLOSE_REVIEW,LARGE_PRICE_MOVE"

    concentration_top1_alert: Decimal = Field(default=Decimal("0.25"))
    drawdown_alert: Decimal = Field(default=Decimal("-0.10"))
    cash_weight_alert: Decimal = Field(default=Decimal("0.05"))
    scenario_impact_alert: Decimal = Field(default=Decimal("-0.05"))
    move_abs_threshold: Decimal = Field(default=Decimal("0.05"))
    move_impact_threshold: Decimal = Field(default=Decimal("0.005"))
    move_z_threshold: Decimal = Field(default=Decimal("2"))
    move_critical_impact: Decimal = Field(default=Decimal("0.02"))
    screen_drawdown: Decimal = Field(default=Decimal("-0.08"))
    screen_peer_gap: Decimal = Field(default=Decimal("-0.03"))

    seed_enabled: bool = True

    # mock: dashboard reads stored seed snapshots.
    # live: dashboard reads the latest LIVE portfolio snapshot and its positions.
    data_mode: str = "mock"
    t212_enabled: bool = False
    t212_base_url: str = "https://live.trading212.com/api/v0"
    t212_api_key: str = ""
    t212_api_secret: str = ""
    t212_read_only: bool = True
    t212_position_poll_seconds: int = 30
    t212_account_poll_seconds: int = 60
    t212_history_poll_seconds: int = 300

    @field_validator(
        "concentration_top1_alert",
        "drawdown_alert",
        "cash_weight_alert",
        "scenario_impact_alert",
        "move_abs_threshold",
        "move_impact_threshold",
        "move_z_threshold",
        "move_critical_impact",
        "screen_drawdown",
        "screen_peer_gap",
        mode="before",
    )
    @classmethod
    def _decimal(cls, value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value
        return D(str(value))

    @field_validator("gemini_thinking_level")
    @classmethod
    def _thinking_level(cls, value: object) -> str:
        cleaned = str(value).strip().lower()
        if cleaned not in {"low", "medium", "high"}:
            raise ValueError("GEMINI_THINKING_LEVEL must be low, medium, or high")
        return cleaned

    def trigger_set(self) -> set[AnalysisTrigger]:
        enabled: set[AnalysisTrigger] = set()
        for part in self.enabled_triggers.split(","):
            name = part.strip()
            if not name:
                continue
            enabled.add(AnalysisTrigger(name))
        return enabled

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
