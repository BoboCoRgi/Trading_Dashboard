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
