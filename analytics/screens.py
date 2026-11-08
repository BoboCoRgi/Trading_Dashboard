"""Extensible quantitative screens. Each signal is optional and evidence-only."""

from dataclasses import dataclass, field
from decimal import Decimal

from core.money import D


@dataclass(frozen=True)
class ScreenInput:
    symbol: str
    daily_return: Decimal | None
    drawdown_from_high: Decimal | None
    peer_return: Decimal | None
    abnormality: Decimal | None
    thesis_active: bool


@dataclass(frozen=True)
class SignalHit:
    code: str
    opportunity_type: str
    reason: str


@dataclass
class ScreenResult:
    hits: list[SignalHit] = field(default_factory=list)

    @property
    def score(self) -> Decimal:
        return D(len(self.hits))

    @property
    def opportunity_type(self) -> str | None:
        if not self.hits:
            return None
        order = (
            "RELATIVE_DISLOCATION",
            "POTENTIAL_OVERREACTION",
            "THESIS_INTACT_RESET",
        )
        kinds = {hit.opportunity_type for hit in self.hits}
        for kind in order:
            if kind in kinds:
                return kind
        return self.hits[0].opportunity_type


def screen(
    row: ScreenInput,
    *,
    drawdown_limit: Decimal,
    peer_gap: Decimal,
    z_threshold: Decimal,
) -> ScreenResult:
    result = ScreenResult()
    if row.drawdown_from_high is not None and D(row.drawdown_from_high) <= D(drawdown_limit):
        kind = "THESIS_INTACT_RESET" if row.thesis_active else "POTENTIAL_OVERREACTION"
        reason = (
            "Price is below its recent high while a thesis is still marked active. "
            "That does not show the thesis is intact."
            if row.thesis_active
            else "Price is materially below its recent high. No fundamental source is attached."
        )
        result.hits.append(SignalHit("multi_day_drawdown", kind, reason))
    if (
        row.daily_return is not None
        and row.peer_return is not None
        and D(row.daily_return) - D(row.peer_return) <= D(peer_gap)
    ):
        result.hits.append(
            SignalHit(
                "peer_divergence",
                "RELATIVE_DISLOCATION",
                "The day's move was weaker than other holdings in the same stored theme.",
            )
        )
    if row.abnormality is not None and abs(D(row.abnormality)) >= D(z_threshold):
        result.hits.append(
            SignalHit(
                "abnormal_day",
                "POTENTIAL_OVERREACTION",
                "The day's return is large versus this name's own recent daily moves.",
            )
        )
    return result
