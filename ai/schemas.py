from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from core.enums import ClaimType

DISCLAIMER = (
    "This is an interpretation of supplied context. It is not an instruction to buy, sell, hold, or size a trade. "
    "You make the decision."
)


class Claim(BaseModel):
    text: str
    claim_type: ClaimType
    confidence: float = Field(ge=0, le=1)
    sources: list[str] = Field(default_factory=list)
    source_reference: str | None = None
    source_timestamp: str | None = None
    counter_evidence: str | None = None

    @field_validator("text")
    @classmethod
    def text_present(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("claim text cannot be empty")
        return cleaned

    @model_validator(mode="after")
    def fold_source_reference(self) -> "Claim":
        if self.source_reference and self.source_reference not in self.sources:
            self.sources.append(self.source_reference)
        return self


class AnalysisResponse(BaseModel):
    summary: str
    what_changed: list[str] = Field(default_factory=list)
    important_facts: list[Claim] = Field(default_factory=list)
    portfolio_risks: list[Claim] = Field(default_factory=list)
    opportunities: list[Claim] = Field(default_factory=list)
    bull_case: list[Claim] = Field(default_factory=list)
    bear_case: list[Claim] = Field(default_factory=list)
    hidden_assumptions: list[Claim] = Field(default_factory=list)
    counter_evidence: list[Claim] = Field(default_factory=list)
    missing_information: list[Claim] = Field(default_factory=list)
    thesis_status: str = "UNKNOWN"
    decision_consistency: str = "UNKNOWN"
    what_would_change_the_view: list[str] = Field(default_factory=list)
    areas_of_agreement: list[str] = Field(default_factory=list)
    areas_of_disagreement: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    is_trading_instruction: Literal[False] = False
    disclaimer: str = DISCLAIMER

    @model_validator(mode="after")
    def never_an_order(self) -> "AnalysisResponse":
        self.is_trading_instruction = False
        if not self.disclaimer.strip():
            self.disclaimer = DISCLAIMER
        return self


CLAIM_FIELDS = (
    "important_facts",
    "portfolio_risks",
    "opportunities",
    "bull_case",
    "bear_case",
    "hidden_assumptions",
    "counter_evidence",
    "missing_information",
)
