"""Display formatting for values the services already calculated.

This module does not recompute portfolio risk. It only turns stored labels
and strings into something a person can scan.
"""

import re
from decimal import Decimal, InvalidOperation

from core.config import get_settings

_NUM = re.compile(r"([A-Za-z0-9_]+)=(-?[0-9]+(?:\.[0-9]+)?)")

_ALERT_TITLES = {
    "CONCENTRATION": "Concentration",
    "DRAWDOWN": "Drawdown from the high",
    "CASH": "Cash level",
    "SCENARIO": "A single-name move",
}

_CHANGE_TITLES = {
    "TOTAL_VALUE": "Portfolio value",
    "WEIGHT_CHANGE": "Weight",
    "NEW_POSITION": "New position",
    "CLOSED_POSITION": "Closed position",
}


def summary_map(pairs: list[tuple[str, str]]) -> dict[str, str]:
    return {label: value for label, value in pairs}


def split_pnl(text: str) -> tuple[str, str]:
    if " (" in text and text.endswith(")"):
        amount, rest = text.split(" (", 1)
        return amount, rest[:-1]
    return text, ""


def tone_for_signed(text: str, *, gains: bool = False) -> str:
    cleaned = text.strip()
    if not cleaned or cleaned == "—":
        return ""
    if cleaned.startswith("-") or cleaned.startswith("−") or cleaned.startswith("-£"):
        return "neg"
    if cleaned.startswith("+"):
        return "pos"
    if gains and cleaned not in {"£0.00", "£0", "0.00%", "0%"}:
        return "pos"
    return ""


def parse_percent(text: str) -> Decimal | None:
    cleaned = text.strip().replace("%", "").replace("−", "-")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _money(raw: str) -> str:
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return raw
    sign = "-" if amount < 0 else ""
    return f"{sign}£{abs(amount):,.2f}"


def _percent_fraction(raw: str) -> str:
    try:
        fraction = Decimal(raw)
    except InvalidOperation:
        return raw
    return f"{(fraction * 100):.1f}%"


def confidence_label(raw: str) -> str:
    if raw in {"", "—"}:
        return "—"
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if value <= 1:
        return f"{(value * 100):.0f}%"
    return f"{value:.0f}%"


def bar_width(weight_text: str) -> int:
    parsed = parse_percent(weight_text)
    if parsed is None:
        return 0
    return max(0, min(100, int(parsed)))


def _evidence(text: str) -> dict[str, str]:
    return {key: value for key, value in _NUM.findall(text or "")}


def _ticker_from(message: str) -> str:
    token = (message or "").split(" ", 1)[0].strip(".,")
    if token.isupper() and 1 < len(token) <= 6:
        return token
    found = re.search(r"\b([A-Z]{2,6})\b", message or "")
    return found.group(1) if found else ""


def format_gbp(amount: float, *, decimals: int = 0) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}£{abs(amount):,.{decimals}f}"


def tidy_quantity(text: str) -> str:
    try:
        value = Decimal(str(text))
    except InvalidOperation:
        return str(text)
    rendered = format(value, "f").rstrip("0").rstrip(".")
    return rendered or "0"


def attention_items(view: dict) -> list[dict]:
    """Semantic attention cards from alerts the risk rules already produced."""
    summary = summary_map(view.get("summary") or [])
    settings = get_settings()
    items: list[dict] = []
    seen_drawdown = False
    for alert in view.get("alerts") or []:
        code = alert.get("code") or ""
        if code == "CLEAR":
            continue
        severity = alert.get("severity") or "INFO"
        level = "HIGH" if severity == "BREACH" else "WATCH" if severity == "WATCH" else "INFO"
        tone = "high" if level == "HIGH" else "watch" if level == "WATCH" else "info"
        title = _ALERT_TITLES.get(code, "Attention")
        detail = alert.get("message") or ""
        if code == "CONCENTRATION":
            ticker = _ticker_from(detail)
            title = f"{ticker} concentration" if ticker else "Concentration"
            weight = summary.get("Largest weight", "")
            threshold = f"{(Decimal(str(settings.concentration_top1_alert)) * 100):.0f}%"
            detail = f"{weight} of the portfolio. Configured threshold {threshold}."
        elif code == "DRAWDOWN":
            seen_drawdown = True
            limit = f"{(Decimal(str(settings.drawdown_alert)) * 100):.0f}%"
            detail = f"Current {summary.get('Drawdown from high', '—')}. Personal limit {limit}."
        elif code == "CASH":
            detail = f"Cash is {summary.get('Cash', '—')}."
        elif code == "SCENARIO":
            evidence = _evidence(alert.get("evidence") or "")
            ticker = _ticker_from(detail)
            title = f"{ticker} scenario" if ticker else "A single-name move"
            impact = _percent_fraction(evidence["impact"]) if "impact" in evidence else "the calculated amount"
            detail = f"A move already calculated would change the portfolio by {impact}."
        items.append({"level": level, "tone": tone, "title": title, "detail": detail})

    current = parse_percent(summary.get("Drawdown from high", ""))
    limit = Decimal(str(settings.drawdown_alert)) * 100
    if current is not None and not seen_drawdown and limit < current <= limit + 2:
        items.append(
            {
                "level": "WATCH",
                "tone": "watch",
                "title": "Drawdown approaching the limit",
                "detail": f"Current {summary.get('Drawdown from high')}. Personal limit {limit:.0f}%.",
            }
        )
    if not items:
        items.append(
            {
                "level": "CLEAR",
                "tone": "ok",
                "title": "No threshold is breached",
                "detail": "Concentration, drawdown, cash, and the single-name scenario are inside the configured lines.",
            }
        )
    order = {"HIGH": 0, "WATCH": 1, "INFO": 2, "CLEAR": 3}
    items.sort(key=lambda item: order.get(item["level"], 9))
    return items


def change_lines(changes: list[dict]) -> list[dict]:
    lines = []
    for change in changes or []:
        code = change.get("code") or ""
        evidence = _evidence(change.get("evidence") or "")
        title = _CHANGE_TITLES.get(code, "Change")
        message = change.get("message") or ""
        if code == "TOTAL_VALUE" and "previous" in evidence and "current" in evidence:
            message = f"From {_money(evidence['previous'])} to {_money(evidence['current'])}."
        elif code == "WEIGHT_CHANGE" and "previous_weight" in evidence and "current_weight" in evidence:
            ticker = _ticker_from(message) or "Position"
            message = (
                f"{ticker} moved from {_percent_fraction(evidence['previous_weight'])} "
                f"to {_percent_fraction(evidence['current_weight'])}."
            )
            title = ticker
        elif code in {"NEW_POSITION", "CLOSED_POSITION"}:
            ticker = _ticker_from(message)
            if ticker:
                title = ticker
        lines.append({"title": title, "message": message})
    return lines


DECISION_TONE = {
    "BUY": "sage",
    "SELL": "coral",
    "HOLD": "blue",
    "WATCH": "apricot",
    "PASS": "grey",
}

STATUS_TONE = {
    "OPEN": "blue",
    "CLOSED": "grey",
    "EXECUTED": "sage",
    "SUPERSEDED": "grey",
    "ACTIVE": "sage",
    "WEAKENED": "apricot",
    "INVALIDATED": "coral",
}

AGENT_CLASS = {
    "ANALYST": "analyst",
    "SKEPTIC": "skeptic",
    "RISK_OFFICER": "risk",
    "SYNTHESIS": "synthesis",
    "DAILY_BRIEF": "brief",
}

AGENT_LABEL = {
    "ANALYST": "Analyst",
    "SKEPTIC": "Skeptic",
    "RISK_OFFICER": "Risk officer",
    "SYNTHESIS": "Synthesis",
    "DAILY_BRIEF": "Daily brief",
}
