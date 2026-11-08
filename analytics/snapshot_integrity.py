"""Checks for one live portfolio snapshot. Values are not adjusted to match."""

from datetime import datetime, timezone
from decimal import Decimal

from core.money import D, q8

# A few pence is position-level rounding. A pound or more is reported as a failure.
PASS_GAP = D("0.10")
WARN_GAP = D("1")


def snapshot_bucket(moment: datetime, minutes: int) -> datetime:
    """Floor a UTC timestamp to the snapshot interval. Seconds are discarded."""
    if minutes < 1:
        raise ValueError("snapshot interval must be at least one minute")
    current = moment.astimezone(timezone.utc).replace(second=0, microsecond=0)
    floored = (current.minute // minutes) * minutes
    return current.replace(minute=floored)


def snapshot_integrity(*, total: Decimal, position_market_value: Decimal, cash: Decimal) -> dict:
    """Compare account total with position market values plus cash.

    The difference is reported. Neither side is changed to force a match.
    """
    components = D(position_market_value) + D(cash)
    difference = D(total) - components
    gap = abs(difference)
    if gap <= PASS_GAP:
        status = "PASS"
    elif gap <= WARN_GAP:
        status = "WARN"
    else:
        status = "FAIL"
    return {
        "status": status,
        "total": D(total),
        "position_market_value": D(position_market_value),
        "cash": D(cash),
        "components": components,
        "difference": difference,
    }


def portfolio_weights(market_values: list[Decimal], total: Decimal) -> list[Decimal | None]:
    """Position weight is market value divided by total account value."""
    if D(total) == 0:
        return [None for _ in market_values]
    return [q8(D(value) / D(total)) for value in market_values]
