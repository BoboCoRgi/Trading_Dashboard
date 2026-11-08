"""Pure Trading 212 history mapping. No network and no database."""

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from core.enums import CashMovementType, FxConversionStatus, TradeAction
from core.money import D, q8

QUANTITY_TOLERANCE = D("0.00000001")
_EXECUTED = {"FILLED", "PARTIALLY_FILLED"}
_CORPORATE_FILLS = {"STOCK_SPLIT", "REVERSE_SPLIT"}
_TRANSACTION_TYPES = {
    "DEPOSIT": CashMovementType.DEPOSIT,
    "WITHDRAW": CashMovementType.WITHDRAWAL,
    "FEE": CashMovementType.FEE,
    "TRANSFER": CashMovementType.TRANSFER,
    "INTEREST_ON_FREE_CASH": CashMovementType.INTEREST,
    "LENDING_INTEREST": CashMovementType.INTEREST,
}


def parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp was not provided")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp had no timezone")
    return parsed.astimezone(timezone.utc)


def _fingerprint(kind: str, parts: list[object]) -> str:
    material = "|".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"{kind}-fp:{digest}"


def order_event_id(item: dict) -> str:
    """Stable id for one history-order row.

    Prefer the fill id. Otherwise the order id. If neither exists, hash the
    ticker, side, timestamps, quantity, and price. The hash is not a random UUID.
    """
    fill = item.get("fill") if isinstance(item.get("fill"), dict) else {}
    order = item.get("order") if isinstance(item.get("order"), dict) else {}
    if fill.get("id") is not None:
        return f"fill:{fill['id']}"
    if order.get("id") is not None:
        return f"order:{order['id']}"
    return _fingerprint(
        "order",
        [
            order.get("ticker"),
            order.get("side"),
            order.get("createdAt"),
            fill.get("filledAt"),
            fill.get("quantity"),
            fill.get("price"),
        ],
    )


def dividend_event_id(item: dict) -> str:
    if item.get("reference"):
        return f"dividend:{item['reference']}"
    return _fingerprint(
        "dividend",
        [item.get("paidOn"), item.get("ticker"), item.get("amount"), item.get("type")],
    )


def transaction_event_id(item: dict) -> str:
    if item.get("reference"):
        return f"transaction:{item['reference']}"
    return _fingerprint(
        "transaction",
        [item.get("dateTime"), item.get("type"), item.get("amount"), item.get("currency")],
    )


def order_occurred_at(item: dict) -> datetime:
    fill = item.get("fill") if isinstance(item.get("fill"), dict) else {}
    order = item.get("order") if isinstance(item.get("order"), dict) else {}
    if fill.get("filledAt"):
        return parse_utc(fill["filledAt"])
    if order.get("createdAt"):
        return parse_utc(order["createdAt"])
    raise ValueError("order had no timestamp")


def order_ticker(item: dict) -> str:
    order = item.get("order") if isinstance(item.get("order"), dict) else {}
    instrument = order.get("instrument") if isinstance(order.get("instrument"), dict) else {}
    ticker = instrument.get("ticker") or order.get("ticker") or ""
    return str(ticker) if ticker else ""


def classify_order(item: dict) -> str:
    """Return trade, corporate, ignore, or error.

    A stock split changes quantity. It is not an investor buy or sell.
    Cancelled and rejected orders are ignore.
    """
    order = item.get("order") if isinstance(item.get("order"), dict) else {}
    fill = item.get("fill") if isinstance(item.get("fill"), dict) else {}
    status = str(order.get("status") or "")
    if status not in _EXECUTED:
        return "ignore"
    if fill.get("quantity") is None:
        return "error"
    if not order_ticker(item):
        return "error"
    if str(fill.get("type") or "") in _CORPORATE_FILLS:
        return "corporate"
    if fill.get("price") is None:
        return "error"
    if str(order.get("side") or "") not in {"BUY", "SELL"}:
        return "error"
    return "trade"


def cash_gbp_fields(amount: Decimal, currency: str) -> tuple[Decimal | None, FxConversionStatus]:
    """GBP cash needs no conversion. Any other currency stays unknown.

    A genuine zero in GBP stays zero. Unknown is NULL, not zero.
    Historical FX rates are not guessed.
    """
    if currency == "GBP":
        return q8(amount), FxConversionStatus.NOT_REQUIRED
    return None, FxConversionStatus.MISSING


def fx_rate_to_gbp(trade_currency: str, wallet: dict | None) -> Decimal | None:
    """GBP wallet fxRate is instrument-currency units per 1 GBP.

    A live USD fill reported fxRate 1.3265798 against a GBP wallet, so the
    rate back to GBP is the reciprocal. Other wallet currencies are not guessed.
    """
    if trade_currency == "GBP":
        return D(1)
    if not wallet or wallet.get("currency") != "GBP" or wallet.get("fxRate") in (None, 0, "0"):
        return None
    rate = D(str(wallet["fxRate"]))
    if rate == 0:
        return None
    return D(1) / rate


def split_taxes(taxes: list | None, trade_currency: str, fx_rate: Decimal | None) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    fees_native = D(0)
    fees_gbp = D(0)
    fx_native = D(0)
    fx_gbp = D(0)
    for tax in taxes or []:
        if not isinstance(tax, dict) or tax.get("quantity") is None:
            continue
        amount = D(str(tax["quantity"]))
        fx_fee = tax.get("name") == "CURRENCY_CONVERSION_FEE"
        currency = tax.get("currency")
        if currency == "GBP":
            if fx_fee:
                fx_gbp += amount
            else:
                fees_gbp += amount
        elif currency == trade_currency:
            if fx_fee:
                fx_native += amount
            else:
                fees_native += amount
    if trade_currency == "GBP":
        return fees_gbp, fees_gbp, fx_gbp, fx_gbp
    if fx_rate is not None:
        if fees_native == 0 and fees_gbp != 0:
            fees_native = fees_gbp / fx_rate
        if fx_native == 0 and fx_gbp != 0:
            fx_native = fx_gbp / fx_rate
        if fees_gbp == 0:
            fees_gbp = fees_native * fx_rate
        if fx_gbp == 0:
            fx_gbp = fx_native * fx_rate
    return fees_native, fees_gbp, fx_native, fx_gbp


def transaction_type(raw_type: object) -> CashMovementType:
    return _TRANSACTION_TYPES.get(str(raw_type or ""), CashMovementType.OTHER)


def signed_quantity(action: TradeAction, quantity: Decimal) -> Decimal:
    amount = D(quantity)
    return amount if action is TradeAction.BUY else -amount


def reconstruct_quantities(
    trades: list[tuple[str, TradeAction, Decimal]],
    corporate: list[tuple[str, Decimal]] | None = None,
) -> dict[str, Decimal]:
    """Open quantity is executed buys and sells plus corporate quantity deltas.

    Live positions are not an input. A corporate delta is already signed.
    """
    totals: dict[str, Decimal] = {}
    for ticker, action, quantity in trades:
        totals[ticker] = totals.get(ticker, D(0)) + signed_quantity(action, quantity)
    for ticker, delta in corporate or []:
        totals[ticker] = totals.get(ticker, D(0)) + D(delta)
    return {ticker: quantity for ticker, quantity in totals.items() if abs(quantity) > QUANTITY_TOLERANCE}


def reconcile_positions(
    api_rows: list[dict],
    reconstructed: dict[str, Decimal],
    *,
    unmapped: int = 0,
    duplicate_executions: int = 0,
    normalisation_errors: int = 0,
) -> dict:
    api: dict[str, Decimal] = {}
    for row in api_rows:
        ticker = ""
        instrument = row.get("instrument") if isinstance(row.get("instrument"), dict) else {}
        if instrument.get("ticker"):
            ticker = str(instrument["ticker"])
        elif row.get("ticker"):
            ticker = str(row["ticker"])
        if not ticker or row.get("quantity") is None:
            continue
        quantity = D(str(row["quantity"]))
        if abs(quantity) <= QUANTITY_TOLERANCE:
            continue
        api[ticker] = api.get(ticker, D(0)) + quantity

    tickers = sorted(set(api) | set(reconstructed))
    lines = []
    mismatches = 0
    missing = 0
    unexpected = 0
    for ticker in tickers:
        api_quantity = api.get(ticker)
        rebuilt = reconstructed.get(ticker)
        if api_quantity is None:
            difference = rebuilt
            status = "FAIL"
            unexpected += 1
        elif rebuilt is None:
            difference = -api_quantity
            status = "FAIL"
            missing += 1
        else:
            difference = rebuilt - api_quantity
            if abs(difference) <= QUANTITY_TOLERANCE:
                status = "PASS"
                difference = D(0)
            else:
                status = "FAIL"
                mismatches += 1
        lines.append(
            {
                "ticker": ticker,
                "api_quantity": api_quantity,
                "reconstructed_quantity": rebuilt,
                "difference": difference,
                "status": status,
            }
        )
    material = mismatches or missing or unexpected or unmapped or duplicate_executions
    if material:
        overall = "FAIL"
    elif normalisation_errors:
        overall = "WARN"
    else:
        overall = "PASS"
    return {
        "lines": lines,
        "live_positions": len(api),
        "reconstructed_open_positions": len(reconstructed),
        "matched": sum(1 for line in lines if line["status"] == "PASS"),
        "quantity_mismatches": mismatches,
        "missing_from_history": missing,
        "unexpected_reconstructed_positions": unexpected,
        "unmapped_instruments": unmapped,
        "duplicate_broker_events": duplicate_executions,
        "normalisation_errors": normalisation_errors,
        "overall": overall,
    }
