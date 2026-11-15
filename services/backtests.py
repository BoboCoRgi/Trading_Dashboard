"""Run a backtest on cached candles and store the configuration with the result.

The run does not call Twelve Data and does not write to the trading journal.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.backtest import (
    VERSION,
    CostModel,
    RiskModel,
    assumptions_text,
    compare_variants,
    run_backtest,
    walk_forward,
)
from analytics.market_stats import MarketBar
from analytics.setups import SetupRules
from analytics.trading import Execution, position_result
from core.enums import DataOrigin
from core.money import D, q2
from db.models import BacktestRun, Instrument, PlanFill, TradePlan
from services.market_data import SqlBarCache

_INTERVAL = {"5m": "5min", "15m": "15min", "1D": "1D"}
_LIMIT_DAYS = {"5m": 10, "15m": 30, "1D": 365 * 3}


def run_saved(
    session: Session,
    *,
    origin: DataOrigin,
    instrument_id: str,
    strategy: str,
    timeframe: str,
    days: int,
    capital: str,
    risk_fraction: str,
    stop_fraction: str,
    target_r: str,
    commission_bps: str,
    slippage_bps: str,
    spread_bps: str,
    compare: bool,
) -> dict:
    instrument = session.get(Instrument, UUID(str(instrument_id)))
    if instrument is None:
        raise ValueError("unknown symbol")
    if timeframe not in _INTERVAL:
        raise ValueError("timeframe must be 5m, 15m or 1D")
    now = datetime.now(timezone.utc)
    span = min(int(days), _LIMIT_DAYS[timeframe])
    cached = SqlBarCache(session, origin).load(str(instrument.id), _INTERVAL[timeframe], now - timedelta(days=span), now)
    bars = [
        MarketBar(
            start=bar.timestamp,
            interval=bar.interval,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            currency=bar.currency,
            source=bar.source,
        )
        for bar in cached
        if (bar.source or "").strip().lower() not in {"", "mock", "seed"}
    ]
    bars.sort(key=lambda bar: bar.start)
    costs = CostModel(D(commission_bps), D(slippage_bps), D(spread_bps))
    risk = RiskModel(
        capital=D(capital),
        risk_fraction=D(risk_fraction),
        stop_fraction=D(stop_fraction),
        target_r=D(target_r),
        max_hold_bars=20 if timeframe == "1D" else 12,
    )
    rules = SetupRules()
    result = run_backtest(bars, strategy, timeframe=timeframe, costs=costs, risk=risk, rules=rules, now=now)
    variants = compare_variants(bars, strategy, timeframe=timeframe, costs=costs, risk=risk, now=now) if compare and bars else []
    folds = walk_forward(bars, strategy, timeframe=timeframe, costs=costs, risk=risk, now=now) if compare and bars else []
    report = _public(instrument, strategy, timeframe, bars, result, variants, folds, costs, risk, now)
    report["journal"] = _journal(session, instrument.symbol, origin)
    session.add(
        BacktestRun(
            id=uuid4(),
            instrument_id=instrument.id,
            symbol=instrument.symbol,
            strategy=strategy,
            timeframe=timeframe,
            strategy_version=VERSION,
            params=report["params"],
            assumptions=report["assumptions"],
            data_start=None if not bars else bars[0].start,
            data_end=None if not bars else bars[-1].start,
            bar_count=len(bars),
            source=bars[-1].source if bars else "",
            metrics=report["metrics"],
            trades=report["trades"],
            comparison={"variants": variants_public(variants), "walk_forward": folds_public(folds)},
            note=" ".join(result.notes),
            created_at=now,
            data_origin=origin,
        )
    )
    return report


def variants_public(rows: list[dict]) -> list[dict]:
    return [
        {
            "stop": row["stop_fraction"],
            "target_r": row["target_r"],
            "in_sample_trades": row["in_sample_trades"],
            "out_of_sample_trades": row["out_of_sample_trades"],
            "in_sample_pnl": str(row["in_sample_pnl"]),
            "out_of_sample_pnl": str(row["out_of_sample_pnl"]),
            "overfit_risk": row["overfit_risk"],
        }
        for row in rows
    ]


def folds_public(rows: list[dict]) -> list[dict]:
    return [
        {
            "train_through": f"{row['train_end_pct']}%",
            "test_through": f"{row['test_end_pct']}%",
            "train_trades": row["train_trades"],
            "test_trades": row["test_trades"],
            "train_pnl": str(row["train_pnl"]),
            "test_pnl": str(row["test_pnl"]),
        }
        for row in rows
    ]


def _public(instrument, strategy, timeframe, bars, result, variants, folds, costs, risk, now) -> dict:
    peak = Decimal("0")
    curve = []
    for moment, value in result.equity:
        peak = max(peak, value)
        drawdown = Decimal("0") if peak <= 0 else (value - peak) / peak
        curve.append({"time": moment.isoformat(), "equity": str(q2(value)), "drawdown": str(q2(drawdown * 100))})
    metrics = _metrics_text(result.metrics)
    return {
        "symbol": instrument.symbol,
        "strategy": strategy,
        "timeframe": timeframe,
        "version": VERSION,
        "calculated_at": now.isoformat(),
        "bars": len(bars),
        "source": bars[-1].source if bars else "",
        "data_start": None if not bars else bars[0].start.isoformat(),
        "data_end": None if not bars else bars[-1].start.isoformat(),
        "notes": result.notes,
        "metrics": metrics,
        "buy_hold_return": _pct(result.buy_hold_return),
        "equity": curve[:: max(1, len(curve) // 400)],
        "trades": [
            {
                "signal": trade.signal_time.isoformat(),
                "entry": trade.entry_time.isoformat(),
                "exit": trade.exit_time.isoformat(),
                "entry_price": str(q2(trade.entry_price)),
                "exit_price": str(q2(trade.exit_price)),
                "pnl": str(q2(trade.pnl)),
                "r": str(q2(trade.r_multiple)),
                "reason": trade.reason,
            }
            for trade in result.trades
        ],
        "variants": variants_public(variants),
        "walk_forward": folds_public(folds),
        "params": {
            "capital": str(risk.capital),
            "risk_fraction": str(risk.risk_fraction),
            "stop_fraction": str(risk.stop_fraction),
            "target_r": str(risk.target_r),
            "max_hold_bars": risk.max_hold_bars,
        },
        "assumptions": {
            "text": assumptions_text(),
            "commission_bps": str(costs.commission_bps),
            "slippage_bps": str(costs.slippage_bps),
            "spread_bps": str(costs.spread_bps),
            "version": VERSION,
        },
    }


def _journal(session: Session, symbol: str, origin: DataOrigin) -> dict:
    plans = session.scalars(
        select(TradePlan).where(
            TradePlan.symbol == symbol,
            TradePlan.data_origin == origin,
            TradePlan.status == "CLOSED",
        )
    ).all()
    multiples = []
    for plan in plans:
        fills = session.scalars(select(PlanFill).where(PlanFill.plan_id == plan.id)).all()
        result = position_result(
            [
                Execution(
                    action=row.action,
                    quantity=D(row.quantity),
                    price=D(row.price),
                    fees_native=D(row.fees_native),
                    currency=row.currency,
                    fx_rate_to_gbp=None if row.fx_rate_to_gbp is None else D(row.fx_rate_to_gbp),
                    executed_at=row.executed_at,
                )
                for row in fills
            ],
            plan.direction,
        )
        if result.native_complete and result.realised_native is not None and D(plan.potential_loss) > 0:
            multiples.append(result.realised_native / D(plan.potential_loss))
    average = None if not multiples else q2(sum(multiples, Decimal("0")) / Decimal(len(multiples)))
    return {
        "closed_plans": len(plans),
        "average_r": None if average is None else str(average),
        "note": "Journal results are real or manual fills. Backtest trades are simulated and are not added to the journal.",
    }


def _metrics_text(metrics: dict) -> dict:
    percents = {"total_return", "cagr", "max_drawdown", "win_rate", "exposure"}
    ratios = {"sharpe", "sortino", "profit_factor", "average_r"}
    money = {"expectancy", "net_pnl"}
    shown = {}
    for key, value in metrics.items():
        if key in percents:
            shown[key] = _pct(value)
        elif key in ratios or key in money:
            shown[key] = _num(value)
        elif isinstance(value, bool):
            shown[key] = "yes" if value else "no"
        else:
            shown[key] = "—" if value is None else str(value)
    return shown


def _pct(value) -> str:
    if value is None:
        return "—"
    return f"{q2(D(value) * 100):.2f}%"


def _num(value) -> str:
    if value is None:
        return "—"
    return f"{q2(D(value)):.2f}"
