"""Store trade plans, logical allocations and execution legs.

Broker positions are only read. Nothing here submits an order.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.trading import (
    Execution,
    Score,
    position_result,
    size_position,
    split_allocation,
    trading_performance,
)
from core.enums import DataOrigin
from core.money import D, q2, q8
from db.models import Instrument, PlanFill, PositionAllocation, TechnicalSetup, Trade, TradePlan, TradePlanRevision
from services.holdings import holdings_report
from services.research import load_universe

_PLAN_FIELDS = (
    "strategy",
    "timeframe",
    "direction",
    "entry_price",
    "entry_low",
    "entry_high",
    "stop_loss",
    "take_profit",
    "risk_amount",
    "risk_fraction",
    "capital_amount",
    "capital_currency",
    "shares",
    "risk_reward",
    "potential_profit",
    "potential_loss",
    "required_capital",
    "status",
    "setup_id",
    "notes",
)


def load_board(session: Session, provider) -> dict:
    origin = provider.origin
    holdings = _holdings(session, provider)
    plans = session.scalars(
        select(TradePlan).where(TradePlan.data_origin == origin).order_by(TradePlan.updated_at.desc())
    ).all()
    fills = _fills_for(session, [row.id for row in plans])
    marks = {row["instrument_id"]: row for row in holdings}
    public_plans = [_plan_public(row, fills.get(row.id, []), marks.get(str(row.instrument_id))) for row in plans]
    scores = [_score(row) for row in public_plans]
    return {
        "origin": origin.value,
        "plans": public_plans,
        "holdings": holdings,
        "instruments": _instruments(session, provider, holdings),
        "setups": _setups(session, origin),
        "trades": _unlinked_trades(session, origin),
        "performance": _performance_public(trading_performance(scores)),
    }


def chart_plans(session: Session, instrument_id: str, origin: DataOrigin) -> list[dict]:
    rows = session.scalars(
        select(TradePlan).where(
            TradePlan.instrument_id == UUID(str(instrument_id)),
            TradePlan.data_origin == origin,
            TradePlan.status.in_(("PLANNED", "ACTIVE")),
        )
    ).all()
    charts = []
    for row in rows:
        levels = []
        for name, value in (
            ("Plan entry", row.entry_price or row.entry_high or row.entry_low),
            ("Plan stop", row.stop_loss),
            ("Plan target", row.take_profit),
        ):
            if value is not None:
                levels.append({"name": name, "price": str(value), "plan_id": str(row.id)})
        charts.append(
            {
                "id": str(row.id),
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "status": row.status,
                "direction": row.direction,
                "levels": levels,
            }
        )
    return charts


def create_plan(session: Session, origin: DataOrigin, payload: dict) -> TradePlan:
    instrument = session.get(Instrument, UUID(str(payload["instrument_id"])))
    if instrument is None:
        raise ValueError("unknown instrument")
    sized = _sized(payload)
    now = datetime.now(timezone.utc)
    plan = TradePlan(
        id=uuid4(),
        instrument_id=instrument.id,
        symbol=instrument.symbol,
        strategy=payload["strategy"].strip(),
        timeframe=payload["timeframe"].strip(),
        direction=payload["direction"],
        entry_price=_optional(payload.get("entry_price")),
        entry_low=_optional(payload.get("entry_low")),
        entry_high=_optional(payload.get("entry_high")),
        stop_loss=D(payload["stop_loss"]),
        take_profit=_optional(payload.get("take_profit")),
        risk_amount=_optional(payload.get("risk_amount")),
        risk_fraction=_optional(payload.get("risk_fraction")),
        capital_amount=_optional(payload.get("capital_amount")),
        capital_currency=(payload.get("capital_currency") or "USD").strip() or "USD",
        shares=sized.shares,
        risk_reward=sized.risk_reward,
        potential_profit=sized.potential_profit,
        potential_loss=sized.potential_loss,
        required_capital=sized.required_capital,
        status=payload.get("status") or "DRAFT",
        setup_id=_uuid(payload.get("setup_id")),
        notes=(payload.get("notes") or "").strip(),
        created_at=now,
        updated_at=now,
        data_origin=origin,
    )
    if plan.status not in {"DRAFT", "PLANNED", "ACTIVE", "CLOSED", "CANCELLED"}:
        raise ValueError("unknown plan status")
    if not plan.strategy or not plan.timeframe:
        raise ValueError("strategy and timeframe are required")
    _check_setup(session, plan)
    session.add(plan)
    return plan


def update_plan(session: Session, plan_id: str, payload: dict) -> TradePlan:
    plan = session.get(TradePlan, UUID(str(plan_id)))
    if plan is None:
        raise ValueError("unknown plan")
    _archive(session, plan)
    sized = _sized(payload)
    plan.strategy = payload["strategy"].strip()
    plan.timeframe = payload["timeframe"].strip()
    plan.direction = payload["direction"]
    plan.entry_price = _optional(payload.get("entry_price"))
    plan.entry_low = _optional(payload.get("entry_low"))
    plan.entry_high = _optional(payload.get("entry_high"))
    plan.stop_loss = D(payload["stop_loss"])
    plan.take_profit = _optional(payload.get("take_profit"))
    plan.risk_amount = _optional(payload.get("risk_amount"))
    plan.risk_fraction = _optional(payload.get("risk_fraction"))
    plan.capital_amount = _optional(payload.get("capital_amount"))
    plan.capital_currency = (payload.get("capital_currency") or plan.capital_currency).strip()
    plan.shares = sized.shares
    plan.risk_reward = sized.risk_reward
    plan.potential_profit = sized.potential_profit
    plan.potential_loss = sized.potential_loss
    plan.required_capital = sized.required_capital
    plan.status = payload.get("status") or plan.status
    plan.setup_id = _uuid(payload.get("setup_id"))
    plan.notes = (payload.get("notes") or "").strip()
    plan.updated_at = datetime.now(timezone.utc)
    if not plan.strategy or not plan.timeframe:
        raise ValueError("strategy and timeframe are required")
    _check_setup(session, plan)
    return plan


def add_manual_fill(session: Session, plan_id: str, payload: dict) -> PlanFill:
    plan = session.get(TradePlan, UUID(str(plan_id)))
    if plan is None:
        raise ValueError("unknown plan")
    quantity = D(payload["quantity"])
    price = D(payload["price"])
    if quantity <= 0 or price <= 0:
        raise ValueError("quantity and price must be positive")
    fill = PlanFill(
        id=uuid4(),
        plan_id=plan.id,
        trade_id=None,
        source="MANUAL",
        broker="",
        broker_execution_id="",
        action=payload["action"],
        quantity=quantity,
        price=price,
        currency=(payload.get("currency") or "USD").strip(),
        fees_native=D(payload.get("fees") or "0"),
        fx_rate_to_gbp=_optional(payload.get("fx_rate_to_gbp")),
        executed_at=_time(payload.get("executed_at")),
        notes=(payload.get("notes") or "Manual entry. No broker execution was imported.").strip(),
        data_origin=plan.data_origin,
    )
    if fill.action not in {"BUY", "SELL"}:
        raise ValueError("action must be BUY or SELL")
    session.add(fill)
    return fill


def link_trade(session: Session, plan_id: str, trade_id: str) -> PlanFill:
    plan = session.get(TradePlan, UUID(str(plan_id)))
    trade = session.get(Trade, UUID(str(trade_id)))
    if plan is None or trade is None:
        raise ValueError("unknown plan or fill")
    if trade.instrument_id != plan.instrument_id or trade.data_origin != plan.data_origin:
        raise ValueError("that fill belongs to a different instrument")
    existing = session.scalar(select(PlanFill).where(PlanFill.trade_id == trade.id))
    if existing is not None:
        return existing
    if trade.broker_trade_id:
        duplicate = session.scalar(select(PlanFill).where(PlanFill.broker_execution_id == trade.broker_trade_id))
        if duplicate is not None:
            return duplicate
    fill = PlanFill(
        id=uuid4(),
        plan_id=plan.id,
        trade_id=trade.id,
        source="BROKER",
        broker=trade.account.broker.value if trade.account is not None else "",
        broker_execution_id=trade.broker_trade_id or "",
        action=trade.action.value,
        quantity=D(trade.quantity),
        price=D(trade.fill_price),
        currency=trade.trade_currency,
        fees_native=D(trade.fees_native) + D(trade.fx_fee_native),
        fx_rate_to_gbp=D(trade.fx_rate_to_gbp) if trade.fx_rate_to_gbp is not None else None,
        executed_at=trade.executed_at,
        notes="Copied from a stored broker fill. The broker position was not changed.",
        data_origin=plan.data_origin,
    )
    session.add(fill)
    return fill


def save_allocation(session: Session, origin: DataOrigin, instrument_id: str, trading_quantity: Decimal, broker_quantity: Decimal) -> PositionAllocation:
    split_allocation(broker_quantity, trading_quantity)
    identity = UUID(str(instrument_id))
    row = session.scalar(
        select(PositionAllocation).where(
            PositionAllocation.instrument_id == identity,
            PositionAllocation.data_origin == origin,
        )
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = PositionAllocation(
            id=uuid4(),
            instrument_id=identity,
            trading_quantity=D(trading_quantity),
            updated_at=now,
            data_origin=origin,
        )
        session.add(row)
        return row
    row.trading_quantity = D(trading_quantity)
    row.updated_at = now
    return row


def _check_setup(session: Session, plan: TradePlan) -> None:
    if plan.setup_id is None:
        return
    setup = session.get(TechnicalSetup, plan.setup_id)
    if setup is None or setup.instrument_id != plan.instrument_id:
        raise ValueError("the technical signal is for a different symbol")


def _sized(payload: dict):
    return size_position(
        direction=payload["direction"],
        entry=_optional(payload.get("entry_price")),
        entry_low=_optional(payload.get("entry_low")),
        entry_high=_optional(payload.get("entry_high")),
        stop=D(payload["stop_loss"]),
        target=_optional(payload.get("take_profit")),
        risk_amount=_optional(payload.get("risk_amount")),
        risk_fraction=_optional(payload.get("risk_fraction")),
        capital=_optional(payload.get("capital_amount")),
    )


def _archive(session: Session, plan: TradePlan) -> None:
    session.add(
        TradePlanRevision(
            id=uuid4(),
            plan_id=plan.id,
            changed_at=datetime.now(timezone.utc),
            snapshot={name: _json(getattr(plan, name)) for name in _PLAN_FIELDS},
            data_origin=plan.data_origin,
        )
    )


def _holdings(session: Session, provider) -> list[dict]:
    report = holdings_report(session, provider)
    if not report.get("ready"):
        return []
    stored = {
        str(row.instrument_id): D(row.trading_quantity)
        for row in session.scalars(select(PositionAllocation).where(PositionAllocation.data_origin == provider.origin))
    }
    rows = []
    for lot in report["combined"]:
        trading = stored.get(lot.instrument_id, Decimal("0"))
        valid = trading <= D(lot.quantity)
        rows.append(
            {
                "instrument_id": lot.instrument_id,
                "symbol": lot.symbol,
                "broker_quantity": _qty(lot.quantity),
                "trading_quantity": _qty(trading),
                "core_quantity": _qty(D(lot.quantity) - trading) if valid else None,
                "valid": valid,
                "price": None if lot.price is None else _money(lot.price),
                "currency": lot.price_currency or "",
            }
        )
    return rows


def _instruments(session, provider, holdings: list[dict]) -> list[dict]:
    rows = {row["instrument_id"]: row["symbol"] for row in holdings}
    try:
        for item in load_universe(session, provider):
            if item.get("instrument_id") and item.get("symbol"):
                rows[str(item["instrument_id"])] = item["symbol"]
    except Exception:
        pass
    return [{"id": key, "symbol": value} for key, value in sorted(rows.items(), key=lambda item: item[1])]


def _setups(session, origin: DataOrigin) -> list[dict]:
    rows = session.scalars(
        select(TechnicalSetup).where(
            TechnicalSetup.data_origin == origin,
            TechnicalSetup.current.is_(True),
        )
    ).all()
    return [
        {"id": str(row.id), "instrument_id": str(row.instrument_id), "label": f"{row.symbol} {row.timeframe} {row.setup_type}"}
        for row in rows
    ]


def _unlinked_trades(session, origin: DataOrigin) -> list[dict]:
    linked = {row for row in session.scalars(select(PlanFill.trade_id).where(PlanFill.trade_id.is_not(None))) if row}
    trades = session.scalars(
        select(Trade).where(Trade.data_origin == origin).order_by(Trade.executed_at.desc()).limit(200)
    ).all()
    public = []
    for trade in trades:
        if trade.id in linked:
            continue
        instrument = session.get(Instrument, trade.instrument_id)
        symbol = instrument.symbol if instrument else ""
        public.append(
            {
                "id": str(trade.id),
                "instrument_id": str(trade.instrument_id),
                "label": f"{trade.executed_at.date().isoformat()} {symbol} {trade.action.value} {_qty(trade.quantity)} @ {_money(trade.fill_price)} {trade.trade_currency}",
            }
        )
    return public


def _fills_for(session, plan_ids: list[UUID]) -> dict[UUID, list[PlanFill]]:
    if not plan_ids:
        return {}
    grouped: dict[UUID, list[PlanFill]] = {}
    rows = session.scalars(select(PlanFill).where(PlanFill.plan_id.in_(plan_ids)).order_by(PlanFill.executed_at)).all()
    for row in rows:
        grouped.setdefault(row.plan_id, []).append(row)
    return grouped


def _plan_public(plan: TradePlan, fills: list[PlanFill], mark: dict | None) -> dict:
    executions = [
        Execution(
            action=row.action,
            quantity=D(row.quantity),
            price=D(row.price),
            fees_native=D(row.fees_native),
            currency=row.currency,
            fx_rate_to_gbp=None if row.fx_rate_to_gbp is None else D(row.fx_rate_to_gbp),
            executed_at=row.executed_at,
            execution_id=row.broker_execution_id,
        )
        for row in fills
    ]
    mark_price = None
    mark_fx = None
    if mark and mark.get("price") and mark.get("currency") and executions and executions[0].currency == mark["currency"]:
        mark_price = D(mark["price"])
    result = position_result(executions, plan.direction, mark=mark_price, mark_fx=mark_fx)
    opened = result.opened_at
    closed = result.closed_at
    duration = None
    if opened is not None and (closed is not None or plan.status == "CLOSED"):
        end = closed or plan.updated_at
        duration = str(end - opened).split(".")[0]
    r_multiple = None
    if result.native_complete and result.realised_native is not None and D(plan.potential_loss) > 0 and plan.status == "CLOSED":
        r_multiple = str((D(result.realised_native) / D(plan.potential_loss)).quantize(Decimal("0.0001")))
    return {
        "id": str(plan.id),
        "instrument_id": str(plan.instrument_id),
        "symbol": plan.symbol,
        "strategy": plan.strategy,
        "timeframe": plan.timeframe,
        "direction": plan.direction,
        "entry_price": _plain(plan.entry_price),
        "entry_low": _plain(plan.entry_low),
        "entry_high": _plain(plan.entry_high),
        "stop_loss": _plain(plan.stop_loss),
        "take_profit": _plain(plan.take_profit),
        "risk_amount": _plain(plan.risk_amount),
        "risk_fraction": _plain(plan.risk_fraction),
        "capital_amount": _plain(plan.capital_amount),
        "capital_currency": plan.capital_currency,
        "shares": _qty(plan.shares),
        "risk_reward": _plain(plan.risk_reward),
        "potential_profit": _money(plan.potential_profit),
        "potential_loss": _money(plan.potential_loss),
        "required_capital": _money(plan.required_capital),
        "status": plan.status,
        "setup_id": None if plan.setup_id is None else str(plan.setup_id),
        "notes": plan.notes,
        "realised": _money(result.realised_native) if result.native_complete else None,
        "realised_gbp": _money(result.realised_gbp) if result.gbp_complete else None,
        "unrealised": _money(result.unrealised_native),
        "currency": result.currency or plan.capital_currency,
        "open_quantity": _qty(result.open_quantity),
        "r_multiple": r_multiple,
        "exit_at": result.closed_at,
        "duration": duration,
        "pnl_note": result.note,
        "fills": [
            {
                "source": row.source,
                "action": row.action,
                "quantity": _qty(row.quantity),
                "price": _money(row.price),
                "currency": row.currency,
                "fees": _money(row.fees_native),
                "executed_at": row.executed_at.strftime("%Y-%m-%d %H:%M UTC"),
                "notes": row.notes,
            }
            for row in fills
        ],
    }


def _score(plan: dict) -> Score:
    closed = plan["status"] == "CLOSED"
    gbp = None if plan["realised_gbp"] is None else D(plan["realised_gbp"])
    planned = None if plan["risk_reward"] is None else D(plan["risk_reward"])
    actual = None if plan["r_multiple"] is None else D(plan["r_multiple"])
    return Score(plan["strategy"], plan["timeframe"], closed, gbp, planned, actual, plan.get("exit_at"))


def _performance_public(result) -> dict:
    def pack(name, row):
        return [
            name,
            str(row["closed"]),
            "—" if row["win_rate"] is None else f"{q2(row['win_rate'] * 100):.2f}%",
            "—" if row["net_realised_gbp"] is None else _money(row["net_realised_gbp"]),
            "—" if row["average_r"] is None else str(row["average_r"]),
        ]

    return {
        "closed": result.closed_count,
        "open": result.open_count,
        "win_rate": None if result.win_rate is None else f"{q2(result.win_rate * 100):.2f}%",
        "average_win": _money(result.average_win),
        "average_loss": _money(result.average_loss),
        "average_r": None if result.average_r is None else str(result.average_r),
        "profit_factor": None if result.profit_factor is None else str(result.profit_factor),
        "net": _money(result.net_realised_gbp),
        "planned_rr": None if result.average_planned_rr is None else str(result.average_planned_rr),
        "actual_r": None if result.average_actual_r is None else str(result.average_actual_r),
        "drawdown": _money(result.max_drawdown_gbp),
        "sample_is_small": result.sample_is_small,
        "note": result.note,
        "groups": [pack(name, row) for name, row in {**result.by_strategy, **{f"{key} timeframe": value for key, value in result.by_timeframe.items()}}.items()],
    }


def _optional(value):
    if value is None or value == "":
        return None
    return D(value)


def _uuid(value):
    if not value:
        return None
    return UUID(str(value))


def _time(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = (value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    return value


def _money(value) -> str | None:
    if value is None:
        return None
    return f"{q2(D(value)):.2f}"


def _plain(value) -> str | None:
    if value is None:
        return None
    return _qty(value)


def _qty(value) -> str:
    text = f"{q8(D(value)):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
