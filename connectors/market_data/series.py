"""Deterministic mock market. Weekdays only, constant FX, labeled MOCK.

This is not a market-data vendor. Paths are piecewise linear so tests and the
dashboard stay stable. Fill prices are the mock close on the trade date.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from core.money import D

START = date(2026, 1, 5)
END = date(2026, 9, 25)
PEAK = date(2026, 9, 8)
FX_TO_GBP = D("0.78")
CLOSE_TIME = time(21, 0, tzinfo=timezone.utc)

SPECS: dict[str, dict[str, Decimal]] = {
    "RKLB": {"start": D("16.40"), "peak": D("64.20"), "end": D("58.10")},
    "MU": {"start": D("92.00"), "peak": D("158.00"), "end": D("145.20")},
    "MRVL": {"start": D("68.00"), "peak": D("112.00"), "end": D("96.40")},
    "VRT": {"start": D("88.00"), "peak": D("168.00"), "end": D("152.30")},
    "META": {"start": D("610.00"), "peak": D("680.00"), "end": D("624.50")},
    "NOK": {"start": D("4.7000"), "peak": D("5.8000"), "end": D("5.3500")},
}


def business_days(start: date = START, end: date = END) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def close_at(day: date) -> datetime:
    return datetime.combine(day, CLOSE_TIME)


def price_on(symbol: str, day: date) -> Decimal:
    spec = SPECS[symbol]
    start_px = spec["start"]
    peak_px = spec["peak"]
    end_px = spec["end"]
    if day <= START:
        value = start_px
    elif day >= END:
        value = end_px
    elif day <= PEAK:
        elapsed = Decimal((day - START).days)
        span = Decimal((PEAK - START).days)
        value = start_px + (peak_px - start_px) * (elapsed / span)
    else:
        elapsed = Decimal((day - PEAK).days)
        span = Decimal((END - PEAK).days)
        value = peak_px + (end_px - peak_px) * (elapsed / span)
    places = Decimal("0.0001") if symbol == "NOK" else Decimal("0.01")
    return value.quantize(places)
