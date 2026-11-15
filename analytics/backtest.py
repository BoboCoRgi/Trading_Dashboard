"""Single-symbol, single-position backtest.

A signal may use only bars up to and including the confirmation candle.
The fill is the next bar's open, worsened by slippage and half the spread.
If a later candle touches both the stop and the target, the stop is used.
Missing bars are counted and not invented.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import product

from analytics.market_stats import (
    MarketBar,
    exponential_moving_average,
    in_regular_session,
    minutes_from_open,
    session_date,
    session_vwap_series,
)
from analytics.setups import (
    CONFIRMED_BREAKOUT,
    PULLBACK_WATCH,
    SUPPORT_BOUNCE,
    SetupRules,
    detect_at,
    drop_unfinished,
)
from core.money import D, q2, q8

VERSION = "phase4-v1"
STRATEGIES = ("breakout", "breakout_retest", "pullback", "support_bounce", "vwap", "orb")
MAX_VARIANTS = 24
_BPS = Decimal("10000")
_BAR_MINUTES = {"5m": 5, "15m": 15, "1D": 24 * 60}
_PERIODS = {"5m": 252 * 78, "15m": 252 * 26, "1D": 252}


@dataclass(frozen=True)
class CostModel:
    commission_bps: Decimal = Decimal("1")
    slippage_bps: Decimal = Decimal("5")
    spread_bps: Decimal = Decimal("2")


@dataclass(frozen=True)
class RiskModel:
    capital: Decimal = Decimal("10000")
    risk_fraction: Decimal = Decimal("0.01")
    stop_fraction: Decimal = Decimal("0.02")
    target_r: Decimal = Decimal("2")
    max_hold_bars: int = 20
    orb_minutes: int = 30


@dataclass(frozen=True)
class Signal:
    index: int
    kind: str


@dataclass(frozen=True)
class SimTrade:
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    shares: Decimal
    pnl: Decimal
    r_multiple: Decimal
    reason: str


@dataclass(frozen=True)
class BacktestResult:
    trades: list[SimTrade]
    equity: list[tuple[datetime, Decimal]]
    metrics: dict
    notes: list[str]
    gaps: int
    buy_hold_return: Decimal | None


def assumptions_text() -> str:
    return (
        "Long only. One position. The signal candle must be complete. "
        "The order fills at the next bar's open, plus slippage and half the spread. "
        "A gap through the stop fills at that open. "
        "When one candle reaches both the stop and the target, the stop is filled. "
        "No missing candle is invented."
    )


def generate_signals(bars: list[MarketBar], strategy: str, *, timeframe: str, rules: SetupRules | None = None, orb_minutes: int = 30) -> list[Signal]:
    """Confirmation indexes. The last bar is never a signal, because it cannot fill later."""
    if strategy not in STRATEGIES:
        raise ValueError("unknown strategy")
    if len(bars) < 3:
        return []
    rules = rules or SetupRules()
    last = len(bars) - 1
    if strategy == "vwap":
        return _vwap_signals(bars, last)
    if strategy == "orb":
        return _orb_signals(bars, last, orb_minutes, timeframe)
    if strategy == "breakout_retest":
        return _retest_signals(bars, last, timeframe, rules)
    want = {"breakout": CONFIRMED_BREAKOUT, "pullback": PULLBACK_WATCH, "support_bounce": SUPPORT_BOUNCE}[strategy]
    ema9 = exponential_moving_average([bar.close for bar in bars], 9)
    ema20 = exponential_moving_average([bar.close for bar in bars], 20)
    found: list[Signal] = []
    start = max(rules.min_bars - 1, 20)
    for index in range(start, last):
        if not _complete(bars[index]):
            continue
        hits = detect_at(bars, index, symbol=bars[index].source or "BACKTEST", timeframe=timeframe, rules=rules)
        if not any(hit.setup_type == want for hit in hits):
            continue
        if strategy == "pullback" and (ema9[index] is None or ema20[index] is None or ema9[index] <= ema20[index]):
            continue
        found.append(Signal(index, strategy))
    return found


def run_backtest(
    bars: list[MarketBar],
    strategy: str,
    *,
    timeframe: str,
    costs: CostModel | None = None,
    risk: RiskModel | None = None,
    rules: SetupRules | None = None,
    now: datetime | None = None,
) -> BacktestResult:
    costs = costs or CostModel()
    risk = risk or RiskModel()
    rules = rules or SetupRules()
    notes = [assumptions_text()]
    usable = [bar for bar in bars if (bar.source or "").strip().lower() not in {"", "mock", "seed"}]
    if now is not None and usable:
        usable = drop_unfinished(usable, now)
    if len(usable) < rules.min_bars + 2:
        notes.append("Not enough completed candles. No prices were invented.")
        return BacktestResult([], [], _empty_metrics(), notes, 0, None)
    if strategy in {"vwap", "orb"} and timeframe == "1D":
        notes.append(f"{strategy} needs 5m or 15m bars. Daily candles were not used.")
        return BacktestResult([], _flat_equity(usable, risk.capital), _empty_metrics(), notes, _gaps(usable, timeframe), None)
    signals = generate_signals(usable, strategy, timeframe=timeframe, rules=rules, orb_minutes=risk.orb_minutes)
    return _simulate(usable, signals, timeframe, costs, risk, notes)


def compare_variants(
    bars: list[MarketBar],
    strategy: str,
    *,
    timeframe: str,
    costs: CostModel | None = None,
    risk: RiskModel | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """In-sample and out-of-sample results. Nothing is chosen as the best return."""
    costs = costs or CostModel()
    base = risk or RiskModel()
    grid = list(_grid(strategy, base))[:MAX_VARIANTS]
    split = int(len(bars) * Decimal("0.7"))
    rows = []
    for item in grid:
        result = run_backtest(bars, strategy, timeframe=timeframe, costs=costs, risk=item, now=now)
        ins = [trade for trade in result.trades if _index_of(bars, trade.signal_time) < split]
        oos = [trade for trade in result.trades if _index_of(bars, trade.signal_time) >= split]
        ins_pnl = sum((trade.pnl for trade in ins), Decimal("0"))
        oos_pnl = sum((trade.pnl for trade in oos), Decimal("0"))
        overfit = bool(ins) and ins_pnl > 0 and (len(oos) < 5 or oos_pnl <= 0)
        rows.append(
            {
                "stop_fraction": str(item.stop_fraction),
                "target_r": str(item.target_r),
                "orb_minutes": item.orb_minutes,
                "in_sample_trades": len(ins),
                "out_of_sample_trades": len(oos),
                "in_sample_pnl": q2(ins_pnl),
                "out_of_sample_pnl": q2(oos_pnl),
                "overfit_risk": overfit,
            }
        )
    return rows


def walk_forward(bars: list[MarketBar], strategy: str, *, timeframe: str, costs: CostModel | None = None, risk: RiskModel | None = None, now: datetime | None = None) -> list[dict]:
    """Two folds. Each test window is after its training window and is not used to pick a winner."""
    if len(bars) < 80:
        return []
    costs = costs or CostModel()
    risk = risk or RiskModel()
    folds = [(0, 50, 75), (0, 75, 100)]
    report = []
    for start_pct, train_pct, test_pct in folds:
        train_end = len(bars) * train_pct // 100
        test_end = len(bars) * test_pct // 100
        result = run_backtest(bars[:test_end], strategy, timeframe=timeframe, costs=costs, risk=risk, now=now)
        train = [trade for trade in result.trades if _index_of(bars, trade.signal_time) < train_end]
        test = [trade for trade in result.trades if train_end <= _index_of(bars, trade.signal_time) < test_end]
        report.append(
            {
                "train_end_pct": train_pct,
                "test_end_pct": test_pct,
                "train_trades": len(train),
                "test_trades": len(test),
                "train_pnl": q2(sum((trade.pnl for trade in train), Decimal("0"))),
                "test_pnl": q2(sum((trade.pnl for trade in test), Decimal("0"))),
                "start_pct": start_pct,
            }
        )
    return report


def _simulate(bars: list[MarketBar], signals: list[Signal], timeframe: str, costs: CostModel, risk: RiskModel, notes: list[str]) -> BacktestResult:
    cash = D(risk.capital)
    trades: list[SimTrade] = []
    equity: list[tuple[datetime, Decimal]] = []
    open_trade = None
    cursor = 0
    ordered = sorted(signals, key=lambda item: item.index)
    in_market = 0
    for index, bar in enumerate(bars):
        while cursor < len(ordered) and ordered[cursor].index + 1 < index:
            cursor += 1
        if open_trade is None and cursor < len(ordered) and ordered[cursor].index + 1 == index:
            opened = _open(bars, ordered[cursor].index, cash, costs, risk)
            cursor += 1
            if opened is not None:
                cash -= opened["debit"]
                open_trade = opened
        if open_trade is not None and index >= open_trade["entry_index"]:
            exit_price, reason = _exit_check(bar, open_trade, index, costs)
            if exit_price is not None:
                trades.append(_close(open_trade, bar, exit_price, reason, costs))
                cash += trades[-1].pnl + open_trade["debit"]
                open_trade = None
        if open_trade is not None and index >= open_trade["entry_index"]:
            in_market += 1
            marked = cash + open_trade["shares"] * D(bar.close)
        else:
            marked = cash
        equity.append((bar.start, q2(marked)))
    if open_trade is not None:
        last = bars[-1]
        exit_price = _worse(D(last.close), "sell", costs)
        trades.append(_close(open_trade, last, exit_price, "end_of_data", costs))
        cash += trades[-1].pnl + open_trade["debit"]
        equity[-1] = (last.start, q2(cash))
    gaps = _gaps(bars, timeframe)
    if gaps:
        notes.append(f"{gaps} missing weekday or intraday slots were left empty.")
    hold = _buy_hold(bars, costs, risk.capital)
    metrics = _metrics(trades, equity, D(risk.capital), in_market, len(bars), bars, timeframe)
    if metrics["sample_is_small"]:
        notes.append("The trade count is too small to treat the result as evidence of an edge.")
    return BacktestResult(trades, equity, metrics, notes, gaps, hold)


def _open(bars, signal_index, cash, costs: CostModel, risk: RiskModel):
    entry_index = signal_index + 1
    if entry_index >= len(bars) or not _complete(bars[entry_index]):
        return None
    raw = D(bars[entry_index].open)
    fill = _worse(raw, "buy", costs)
    risk_per_share = q8(fill * D(risk.stop_fraction))
    if risk_per_share <= 0 or fill <= 0:
        return None
    stop = q8(fill - risk_per_share)
    target = q8(fill + D(risk.target_r) * risk_per_share)
    budget = cash * D(risk.risk_fraction)
    shares = q8(budget / risk_per_share)
    commission = _commission(fill, shares, costs)
    affordable = q8((cash - commission) / fill) if fill else Decimal("0")
    if affordable < shares:
        shares = affordable
    if shares <= 0:
        return None
    debit = q8(shares * fill + _commission(fill, shares, costs))
    if debit > cash:
        return None
    return {
        "signal_index": signal_index,
        "entry_index": entry_index,
        "signal_time": bars[signal_index].start,
        "entry_time": bars[entry_index].start,
        "entry": fill,
        "stop": stop,
        "target": target,
        "shares": shares,
        "risk_per_share": risk_per_share,
        "debit": debit,
        "last_index": min(len(bars) - 1, entry_index + risk.max_hold_bars - 1),
    }


def _exit_check(bar: MarketBar, trade: dict, index: int, costs: CostModel):
    if not _complete(bar):
        return _worse(D(bar.close), "sell", costs), "missing_ohlc"
    opened = D(bar.open)
    if opened <= trade["stop"]:
        return _worse(opened, "sell", costs), "gap_stop"
    if opened >= trade["target"]:
        return _worse(opened, "sell", costs), "gap_target"
    stopped = D(bar.low) <= trade["stop"]
    targeted = D(bar.high) >= trade["target"]
    if stopped and targeted:
        return _worse(trade["stop"], "sell", costs), "same_candle_stop"
    if stopped:
        return _worse(trade["stop"], "sell", costs), "stop"
    if targeted:
        return _worse(trade["target"], "sell", costs), "target"
    if index >= trade["last_index"]:
        return _worse(D(bar.close), "sell", costs), "time_stop"
    return None, ""


def _close(trade: dict, bar: MarketBar, exit_price: Decimal, reason: str, costs: CostModel) -> SimTrade:
    proceeds = trade["shares"] * exit_price - _commission(exit_price, trade["shares"], costs)
    pnl = q8(proceeds - trade["debit"])
    risk_cash = trade["shares"] * trade["risk_per_share"]
    multiple = q8(pnl / risk_cash) if risk_cash else Decimal("0")
    return SimTrade(
        signal_time=trade["signal_time"],
        entry_time=trade["entry_time"],
        exit_time=bar.start,
        entry_price=q8(trade["entry"]),
        exit_price=q8(exit_price),
        stop_price=trade["stop"],
        target_price=trade["target"],
        shares=trade["shares"],
        pnl=pnl,
        r_multiple=multiple,
        reason=reason,
    )


def _vwap_signals(bars: list[MarketBar], last: int) -> list[Signal]:
    if any(bar.interval in {"1D", "1W"} for bar in bars):
        return []
    vwap = session_vwap_series(bars)
    found = []
    for index in range(1, last):
        if vwap[index] is None or vwap[index - 1] is None or not _complete(bars[index]):
            continue
        crossed = bars[index - 1].close <= vwap[index - 1] and bars[index].close > vwap[index]
        if crossed and bars[index].close > bars[index].open:
            found.append(Signal(index, "vwap"))
    return found


def _orb_signals(bars: list[MarketBar], last: int, orb_minutes: int, timeframe: str) -> list[Signal]:
    if timeframe == "1D":
        return []
    step = _BAR_MINUTES.get(timeframe, 5)
    needed = max(1, orb_minutes // step)
    by_day: dict = {}
    for index, bar in enumerate(bars):
        if bar.start.tzinfo is None or not in_regular_session(bar.start):
            continue
        by_day.setdefault(session_date(bar.start), []).append(index)
    found = []
    for indexes in by_day.values():
        opening = [index for index in indexes if (minutes_from_open(bars[index].start) or 0) < orb_minutes]
        if len(opening) < needed or any(not _complete(bars[index]) for index in opening):
            continue
        ceiling = max(D(bars[index].high) for index in opening)
        for index in indexes:
            if index <= opening[-1] or index >= last or not _complete(bars[index]):
                continue
            if bars[index].close > ceiling and bars[index].close > bars[index].open:
                found.append(Signal(index, "orb"))
                break
    return found


def _retest_signals(bars: list[MarketBar], last: int, timeframe: str, rules: SetupRules) -> list[Signal]:
    zone = None
    found = []
    start = max(rules.min_bars - 1, 20)
    for index in range(start, last):
        if not _complete(bars[index]):
            continue
        hits = detect_at(bars, index, symbol="BACKTEST", timeframe=timeframe, rules=rules)
        confirmed = next((hit for hit in hits if hit.setup_type == CONFIRMED_BREAKOUT), None)
        if confirmed is not None and confirmed.level_low is not None and confirmed.level_high is not None:
            zone = (confirmed.level_low, confirmed.level_high, index)
            continue
        if zone is None or index <= zone[2]:
            continue
        low, high, _born = zone
        bar = bars[index]
        if bar.close < low:
            zone = None
            continue
        if bar.low is not None and low <= bar.low <= high and bar.close > high and bar.close > bar.open:
            found.append(Signal(index, "breakout_retest"))
            zone = None
    return found


def _metrics(trades, equity, capital, in_market, total_bars, bars, timeframe) -> dict:
    pnls = [trade.pnl for trade in trades]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    net = sum(pnls, Decimal("0"))
    end = equity[-1][1] if equity else capital
    total_return = q8((end - capital) / capital) if capital else Decimal("0")
    days = max(1, (bars[-1].start - bars[0].start).days)
    cagr = None
    if days >= 30 and end > 0 and capital > 0:
        cagr = q8((end / capital) ** (Decimal("365.25") / Decimal(days)) - 1)
    returns = []
    for previous, current in zip(equity, equity[1:]):
        if previous[1] > 0:
            returns.append((current[1] - previous[1]) / previous[1])
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": _drawdown(equity),
        "sharpe": _ratio(returns, timeframe, downside=False),
        "sortino": _ratio(returns, timeframe, downside=True),
        "win_rate": None if not trades else q8(Decimal(len(wins)) / Decimal(len(trades))),
        "profit_factor": None if not wins or not losses else q8(sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0")))),
        "average_r": None if not trades else q8(sum((trade.r_multiple for trade in trades), Decimal("0")) / Decimal(len(trades))),
        "expectancy": None if not trades else q8(net / Decimal(len(trades))),
        "exposure": q8(Decimal(in_market) / Decimal(total_bars)) if total_bars else Decimal("0"),
        "trade_count": len(trades),
        "net_pnl": q2(net),
        "sample_is_small": len(trades) < 30,
    }


def _empty_metrics() -> dict:
    return {
        "total_return": Decimal("0"),
        "cagr": None,
        "max_drawdown": Decimal("0"),
        "sharpe": None,
        "sortino": None,
        "win_rate": None,
        "profit_factor": None,
        "average_r": None,
        "expectancy": None,
        "exposure": Decimal("0"),
        "trade_count": 0,
        "net_pnl": Decimal("0"),
        "sample_is_small": True,
    }


def _drawdown(equity: list[tuple[datetime, Decimal]]) -> Decimal:
    peak = Decimal("0")
    worst = Decimal("0")
    for _moment, value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value - peak) / peak)
    return q8(worst)


def _ratio(returns: list[Decimal], timeframe: str, *, downside: bool) -> Decimal | None:
    if len(returns) < 30:
        return None
    mean = sum(returns, Decimal("0")) / Decimal(len(returns))
    if downside:
        used = [value for value in returns if value < 0]
        if len(used) < 2:
            return None
        centre = Decimal("0")
    else:
        used = returns
        centre = mean
    variance = sum(((value - centre) ** 2 for value in used), Decimal("0")) / Decimal(len(used) - 1)
    if variance <= 0:
        return None
    annual = Decimal(_PERIODS.get(timeframe, 252)).sqrt()
    return q8(mean / variance.sqrt() * annual)


def _buy_hold(bars: list[MarketBar], costs: CostModel, capital: Decimal) -> Decimal | None:
    first = next((bar for bar in bars if _complete(bar)), None)
    last = bars[-1]
    if first is None or not _complete(last) or first is last:
        return None
    entry = _worse(D(first.open), "buy", costs)
    exit_price = _worse(D(last.close), "sell", costs)
    shares = q8(D(capital) / entry) if entry else Decimal("0")
    if shares <= 0:
        return None
    debit = shares * entry + _commission(entry, shares, costs)
    credit = shares * exit_price - _commission(exit_price, shares, costs)
    if debit <= 0:
        return None
    return q8((credit - debit) / D(capital))


def _gaps(bars: list[MarketBar], timeframe: str) -> int:
    if len(bars) < 2:
        return 0
    missing = 0
    if timeframe == "1D":
        for previous, current in zip(bars, bars[1:]):
            day = previous.start.date() + timedelta(days=1)
            while day < current.start.date():
                if day.weekday() < 5:
                    missing += 1
                day += timedelta(days=1)
        return missing
    step = timedelta(minutes=_BAR_MINUTES.get(timeframe, 5))
    for previous, current in zip(bars, bars[1:]):
        if previous.start.tzinfo is None or current.start.tzinfo is None:
            continue
        if session_date(previous.start) != session_date(current.start):
            continue
        if not in_regular_session(previous.start) or not in_regular_session(current.start):
            continue
        slots = int((current.start - previous.start) / step) - 1
        if slots > 0:
            missing += slots
    return missing


def _grid(strategy: str, base: RiskModel):
    stops = [Decimal("0.01"), Decimal("0.02")]
    targets = [Decimal("1.5"), Decimal("2")]
    extra = [15, 30] if strategy == "orb" else [base.orb_minutes]
    for stop, target, minutes in product(stops, targets, extra):
        yield replace(base, stop_fraction=stop, target_r=target, orb_minutes=minutes)


def _worse(price: Decimal, side: str, costs: CostModel) -> Decimal:
    penalty = (D(costs.slippage_bps) + D(costs.spread_bps) / 2) / _BPS
    if side == "buy":
        return q8(price * (1 + penalty))
    return q8(price * (1 - penalty))


def _commission(price: Decimal, shares: Decimal, costs: CostModel) -> Decimal:
    return q8(price * shares * D(costs.commission_bps) / _BPS)


def _complete(bar: MarketBar) -> bool:
    return bar.open is not None and bar.high is not None and bar.low is not None and bar.volume is not None


def _flat_equity(bars, capital: Decimal):
    return [(bar.start, q2(capital)) for bar in bars]


def _index_of(bars: list[MarketBar], moment: datetime) -> int:
    for index, bar in enumerate(bars):
        if bar.start == moment:
            return index
    return len(bars)
