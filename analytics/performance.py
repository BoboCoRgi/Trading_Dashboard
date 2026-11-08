from decimal import Decimal

import numpy as np
import pandas as pd

from core.money import D


def daily_pnl(current_value: Decimal, previous_value: Decimal | None) -> tuple[Decimal, Decimal]:
    if previous_value is None:
        return D(0), D(0)
    previous = D(previous_value)
    current = D(current_value)
    pnl = current - previous
    if previous == 0:
        return pnl, D(0)
    return pnl, pnl / previous


def rebased_series(values: list[Decimal], dates: list[object] | None = None) -> pd.Series:
    """Display series rebased to 100. Stored ledger values stay Decimal."""
    if not values:
        return pd.Series(dtype="float64")
    array = np.array([float(value) for value in values], dtype=np.float64)
    rebased = array / array[0] * 100.0
    if dates is None:
        return pd.Series(rebased)
    return pd.Series(rebased, index=pd.Index(dates))


def return_frame(values: list[Decimal], dates: list[object]) -> pd.DataFrame:
    series = pd.Series([float(value) for value in values], index=pd.Index(dates), dtype="float64")
    frame = pd.DataFrame({"value_gbp": series})
    frame["daily_return"] = frame["value_gbp"].pct_change()
    return frame
