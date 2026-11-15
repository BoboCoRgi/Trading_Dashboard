"""Chart ranges and the internal bar size for each range.

Streamlit passes a range code. Provider interval strings stay in the connector.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class RangeSpec:
    code: str
    interval: str
    calendar_days: int
    intraday: bool


_SPECS = {
    "1D": RangeSpec("1D", "5min", 1, True),
    "5D": RangeSpec("5D", "1h", 7, True),
    "1M": RangeSpec("1M", "1D", 31, False),
    "3M": RangeSpec("3M", "1D", 93, False),
    "1Y": RangeSpec("1Y", "1D", 370, False),
}

# Holding-detail timeframe is chosen separately from how far back to look.
TIMEFRAMES = ("5m", "15m", "1H", "1D", "1W")
HISTORY = ("1D", "1W", "1M", "3M", "1Y", "ALL")
_TIMEFRAME_INTERVAL = {"5m": "5min", "15m": "15min", "1H": "1h", "1D": "1D", "1W": "1W"}
_HISTORY_DAYS = {"1D": 1, "1W": 7, "1M": 31, "3M": 93, "1Y": 370}
# Intraday "ALL" is capped so one request cannot ask for years of 5-minute bars.
_ALL_CAP_DAYS = {"5m": 10, "15m": 30, "1H": 370, "1D": 365 * 20, "1W": 365 * 20}
# Longer menu ranges are shortened to the same cap. The chart says so.
_REQUEST_CAP_DAYS = {"5m": 10, "15m": 30}
INTRADAY_TIMEFRAMES = frozenset({"5m", "15m", "1H"})

MINI_RANGE = "1M"


def range_spec(code: str) -> RangeSpec:
    try:
        return _SPECS[code]
    except KeyError as exc:
        raise ValueError(f"Unknown chart range: {code}") from exc


def interval_for(code: str) -> str:
    return range_spec(code).interval


def window_for(code: str, now: datetime) -> tuple[datetime, datetime]:
    spec = range_spec(code)
    return now - timedelta(days=spec.calendar_days), now


def timeframe_interval(timeframe: str) -> str:
    try:
        return _TIMEFRAME_INTERVAL[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unknown chart timeframe: {timeframe}") from exc


def history_window(range_code: str, timeframe: str, now: datetime) -> tuple[datetime, datetime]:
    """Start and end of a holding chart. A shortened window is not filled with invented bars."""
    days = _requested_days(range_code, timeframe)
    return now - timedelta(days=days), now


def history_cap_days(range_code: str, timeframe: str) -> int | None:
    """Days actually requested when the menu range is longer than this timeframe allows."""
    if timeframe not in _TIMEFRAME_INTERVAL:
        raise ValueError(f"Unknown chart timeframe: {timeframe}")
    requested = _menu_days(range_code, timeframe)
    cap = _REQUEST_CAP_DAYS.get(timeframe)
    if cap is not None and requested > cap:
        return cap
    if range_code == "ALL":
        return _ALL_CAP_DAYS[timeframe]
    return None


def _requested_days(range_code: str, timeframe: str) -> int:
    requested = _menu_days(range_code, timeframe)
    cap = _REQUEST_CAP_DAYS.get(timeframe)
    if cap is not None:
        return min(requested, cap)
    return requested


def _menu_days(range_code: str, timeframe: str) -> int:
    if timeframe not in _TIMEFRAME_INTERVAL:
        raise ValueError(f"Unknown chart timeframe: {timeframe}")
    if range_code in _HISTORY_DAYS:
        return _HISTORY_DAYS[range_code]
    if range_code == "ALL":
        return _ALL_CAP_DAYS[timeframe]
    raise ValueError(f"Unknown chart range: {range_code}")
