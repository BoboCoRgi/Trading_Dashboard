"""Daily market P&L by instrument.

This does not read portfolio snapshots. A buy or sell is not profit by itself.
Contribution is price movement against the previous close, plus price movement
against the execution price for fills made today.
"""

from dataclasses import dataclass
from decimal import Decimal

from core.money import D, q8


@dataclass(frozen=True)
class OpeningPosition:
    instrument_id: str
    symbol: str
    quantity: Decimal
    previous_close: Decimal | None
    current_price: Decimal | None
    currency: str
    fx_to_reporting: Decimal | None


@dataclass(frozen=True)
class IntradayExecution:
    """``quantity`` is positive. The side decides the sign."""

    instrument_id: str
    symbol: str
    side: str
    quantity: Decimal
    execution_price: Decimal | None
    current_price: Decimal | None
    currency: str
    fx_to_reporting: Decimal | None


def calculate_daily_pnl_contribution(
    openings: list[OpeningPosition],
    executions: list[IntradayExecution],
    *,
    reporting_currency: str = "GBP",
) -> dict:
    """Attribute today's market P&L.

    For shares held at the start of the day::

        quantity × (current price − previous close) × FX

    For a fill today, signed quantity is positive for a buy and negative for a
    sell::

        signed quantity × (current price − execution price) × FX

    The cash paid or received for the fill is not added. Missing prices or FX
    make the whole result unavailable. Nothing is filled in from a snapshot.
    """
    if not openings and not executions:
        return _unavailable("No open position or intraday execution was supplied.")

    grouped: dict[str, dict] = {}
    for row in openings:
        bucket = grouped.setdefault(row.instrument_id, _bucket(row.instrument_id, row.symbol, row.currency))
        bucket["opening"] = row
    for row in executions:
        bucket = grouped.setdefault(row.instrument_id, _bucket(row.instrument_id, row.symbol, row.currency))
        bucket["fills"].append(row)

    lines = []
    for bucket in grouped.values():
        line = _one(bucket, reporting_currency)
        if line is None:
            return _unavailable("Today's contribution analysis requires market price data.")
        if line["contribution_native"] == 0 and line["opening_quantity"] == 0 and not line["had_fill"]:
            continue
        lines.append(line)
    if not lines:
        return _unavailable("Today's contribution analysis requires market price data.")

    total = sum((line["contribution_gbp"] for line in lines), start=D(0))
    rows = []
    for line in lines:
        share = None if total == 0 else q8(line["contribution_gbp"] / total)
        rows.append(
            {
                "instrument_id": line["instrument_id"],
                "symbol": line["symbol"],
                "contribution_native": line["contribution_native"],
                "contribution_gbp": line["contribution_gbp"],
                "portfolio_contribution_pct": share,
                "current_price": line["current_price"],
                "previous_close": line["previous_close"],
                "day_change_pct": line["day_change_pct"],
            }
        )
    rows.sort(key=lambda item: abs(item["contribution_gbp"]), reverse=True)
    return {"ready": True, "reason": None, "total_gbp": q8(total), "rows": rows}


def _bucket(instrument_id: str, symbol: str, currency: str) -> dict:
    return {
        "instrument_id": instrument_id,
        "symbol": symbol,
        "currency": currency,
        "opening": None,
        "fills": [],
    }


def _one(bucket: dict, reporting_currency: str) -> dict | None:
    opening: OpeningPosition | None = bucket["opening"]
    fills: list[IntradayExecution] = bucket["fills"]
    currency = opening.currency if opening is not None else fills[0].currency
    if any(fill.currency != currency for fill in fills):
        return None
    current = _current_price(opening, fills)
    if current is None:
        return None
    previous = None if opening is None else opening.previous_close
    opening_qty = D(0) if opening is None else D(opening.quantity)
    if opening_qty != 0 and previous is None:
        return None
    native = D(0)
    if opening_qty != 0:
        native += opening_qty * (D(current) - D(previous))
    had_fill = False
    for fill in fills:
        if fill.execution_price is None or fill.quantity is None:
            return None
        signed = _signed(fill.side, D(fill.quantity))
        if signed is None:
            return None
        native += signed * (D(current) - D(fill.execution_price))
        had_fill = True
    fx = _fx(opening, fills, currency, reporting_currency)
    if fx is None:
        return None
    day_change = None
    if previous not in (None, D(0)):
        day_change = q8((D(current) - D(previous)) / D(previous))
    return {
        "instrument_id": bucket["instrument_id"],
        "symbol": bucket["symbol"],
        "opening_quantity": opening_qty,
        "had_fill": had_fill,
        "contribution_native": q8(native),
        "contribution_gbp": q8(native * fx),
        "current_price": D(current),
        "previous_close": None if previous is None else D(previous),
        "day_change_pct": day_change,
    }


def _current_price(opening: OpeningPosition | None, fills: list[IntradayExecution]) -> Decimal | None:
    prices: list[Decimal] = []
    if opening is not None and opening.current_price is not None:
        prices.append(D(opening.current_price))
    for fill in fills:
        if fill.current_price is None:
            return None
        prices.append(D(fill.current_price))
    if not prices or any(price != prices[0] for price in prices):
        return None
    return prices[0]


def _signed(side: str, quantity: Decimal) -> Decimal | None:
    if quantity < 0:
        return None
    if side == "BUY":
        return quantity
    if side == "SELL":
        return -quantity
    return None


def _fx(
    opening: OpeningPosition | None,
    fills: list[IntradayExecution],
    currency: str,
    reporting_currency: str,
) -> Decimal | None:
    if currency == reporting_currency:
        return D(1)
    rates = []
    if opening is not None and opening.fx_to_reporting is not None:
        rates.append(D(opening.fx_to_reporting))
    for fill in fills:
        if fill.fx_to_reporting is None:
            return None
        rates.append(D(fill.fx_to_reporting))
    if not rates:
        return None
    if any(rate != rates[0] for rate in rates):
        return None
    return rates[0]


def _unavailable(reason: str) -> dict:
    return {"ready": False, "reason": reason, "total_gbp": None, "rows": []}
