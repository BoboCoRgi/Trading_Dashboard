from dataclasses import dataclass, field
from decimal import Decimal

from core.money import D


@dataclass(frozen=True)
class ShockPosition:
    key: str
    weight: Decimal
    theme: str


@dataclass(frozen=True)
class ScenarioDefinition:
    name: str
    assumption: str
    shocks_by_symbol: dict[str, Decimal] = field(default_factory=dict)
    theme_shocks: dict[str, Decimal] = field(default_factory=dict)
    default_shock: Decimal = D(0)


@dataclass(frozen=True)
class ScenarioLine:
    key: str
    weight: Decimal
    shock: Decimal
    portfolio_impact: Decimal


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    assumption: str
    lines: tuple[ScenarioLine, ...]
    portfolio_impact: Decimal


def position_impact(weight: Decimal, shock: Decimal) -> Decimal:
    """Portfolio return contribution. 25% weight and -20% shock is -5%."""
    return D(weight) * D(shock)


def _shock_for(position: ShockPosition, scenario: ScenarioDefinition) -> Decimal:
    if position.key in scenario.shocks_by_symbol:
        return scenario.shocks_by_symbol[position.key]
    if position.theme in scenario.theme_shocks:
        return scenario.theme_shocks[position.theme]
    return scenario.default_shock


def run_scenario(positions: list[ShockPosition], scenario: ScenarioDefinition) -> ScenarioResult:
    lines: list[ScenarioLine] = []
    total = D(0)
    for position in positions:
        shock = _shock_for(position, scenario)
        impact = position_impact(position.weight, shock)
        total += impact
        if shock != 0:
            lines.append(
                ScenarioLine(
                    key=position.key,
                    weight=position.weight,
                    shock=shock,
                    portfolio_impact=impact,
                )
            )
    lines.sort(key=lambda line: line.portfolio_impact)
    return ScenarioResult(
        name=scenario.name,
        assumption=scenario.assumption,
        lines=tuple(lines),
        portfolio_impact=total,
    )


def single_name_grid(positions: list[ShockPosition], shocks: tuple[Decimal, ...]) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for position in positions:
        for shock in shocks:
            scenario = ScenarioDefinition(
                name=f"{position.key} {shock}",
                assumption="Single-name price shock. Other holdings unchanged. Not a correlation model.",
                shocks_by_symbol={position.key: shock},
            )
            results.append(run_scenario(positions, scenario))
    return results


def standard_stress(positions: list[ShockPosition]) -> list[ScenarioResult]:
    """Small scenario set. The equity shock assumes beta of 1 and says so."""
    definitions = [
        ScenarioDefinition(
            name="Nasdaq -10% (beta = 1)",
            assumption="Applies -10% to every equity position and leaves cash unchanged. This is not a factor model. Beta is assumed to be 1.",
            default_shock=D("-0.10"),
        ),
        ScenarioDefinition(
            name="Semiconductor theme -20%",
            assumption="Applies -20% only to positions tagged Semiconductors. No cross-theme correlation.",
            theme_shocks={"Semiconductors": D("-0.20")},
        ),
        ScenarioDefinition(
            name="RKLB -30%",
            assumption="Applies -30% to RKLB only.",
            shocks_by_symbol={"RKLB": D("-0.30")},
        ),
    ]
    return [run_scenario(positions, definition) for definition in definitions]
