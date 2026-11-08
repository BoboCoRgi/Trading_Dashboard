"""Nullable unknown GBP amounts, corporate actions, and live snapshot fields.

Genuine GBP zeros stay zero. A non-GBP cash row whose GBP value was stored as
0 because no rate was available becomes NULL. Fresh installs already create the
new columns from the models, so each change checks the current schema first.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_live_snapshot_cleanup"
down_revision = "0003_cash_movement_native"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(24, 8)


def _columns(inspector, table: str) -> dict:
    if table not in inspector.get_table_names():
        return {}
    return {column["name"]: column for column in inspector.get_columns(table)}


def _nullable(table: str, name: str, columns: dict) -> None:
    column = columns.get(name)
    if column is not None and not column["nullable"]:
        op.alter_column(table, name, existing_type=MONEY, nullable=True)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cash = _columns(inspector, "cash_movement")
    if "fx_conversion_status" not in cash:
        op.add_column(
            "cash_movement",
            sa.Column("fx_conversion_status", sa.String(length=16), nullable=True),
        )
    _nullable("cash_movement", "amount_gbp", cash)
    op.execute(
        """
        UPDATE cash_movement
        SET amount_gbp = NULL
        WHERE currency IS NOT NULL
          AND currency <> 'GBP'
          AND amount_gbp = 0
          AND note LIKE '%GBP amount unavailable%'
        """
    )
    op.execute(
        """
        UPDATE cash_movement
        SET fx_conversion_status = 'NOT_REQUIRED'
        WHERE fx_conversion_status IS NULL
          AND (currency = 'GBP' OR currency IS NULL)
        """
    )
    op.execute(
        """
        UPDATE cash_movement
        SET fx_conversion_status = 'MISSING'
        WHERE fx_conversion_status IS NULL
          AND amount_gbp IS NULL
        """
    )
    op.execute(
        """
        UPDATE cash_movement
        SET fx_conversion_status = 'AVAILABLE'
        WHERE fx_conversion_status IS NULL
          AND amount_gbp IS NOT NULL
          AND currency IS NOT NULL
          AND currency <> 'GBP'
        """
    )
    op.execute("UPDATE cash_movement SET fx_conversion_status = 'NOT_REQUIRED' WHERE fx_conversion_status IS NULL")
    op.alter_column("cash_movement", "fx_conversion_status", existing_type=sa.String(length=16), nullable=False)

    if "corporate_action" not in inspector.get_table_names():
        op.create_table(
            "corporate_action",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("account_id", sa.Uuid(), sa.ForeignKey("broker_account.id"), nullable=False),
            sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("instrument.id"), nullable=False),
            sa.Column("action_type", sa.String(length=64), nullable=False),
            sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("quantity_delta", MONEY, nullable=True),
            sa.Column("split_ratio_from", MONEY, nullable=True),
            sa.Column("split_ratio_to", MONEY, nullable=True),
            sa.Column("broker_event_id", sa.String(length=128), nullable=False),
            sa.Column("source_event_id", sa.Uuid(), sa.ForeignKey("raw_broker_event.id"), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("data_origin", sa.String(length=64), nullable=False),
            sa.UniqueConstraint("account_id", "broker_event_id", name="uq_corporate_action_broker_event"),
        )

    portfolio = _columns(inspector, "portfolio_snapshot")
    for name in (
        "daily_pnl_gbp",
        "daily_pnl_pct",
        "ath_portfolio_value_gbp",
        "drawdown_from_ath",
        "max_drawdown_to_date",
        "realised_pnl_gbp",
    ):
        _nullable("portfolio_snapshot", name, portfolio)
    indexes = {index["name"] for index in inspector.get_indexes("portfolio_snapshot")}
    if "uq_live_portfolio_snapshot_time" not in indexes:
        op.create_index(
            "uq_live_portfolio_snapshot_time",
            "portfolio_snapshot",
            ["snapshot_time"],
            unique=True,
            postgresql_where=sa.text("data_origin = 'LIVE'"),
        )

    position = _columns(inspector, "position_snapshot")
    for name in (
        "fx_rate_to_gbp",
        "market_value_gbp",
        "average_cost_gbp",
        "cost_basis_gbp",
        "unrealised_pnl_gbp",
        "unrealised_pnl_pct",
        "realised_pnl_gbp",
        "portfolio_weight",
    ):
        _nullable("position_snapshot", name, position)
    if "broker_average_price" not in position:
        op.add_column("position_snapshot", sa.Column("broker_average_price", MONEY, nullable=True))
    if "broker_average_currency" not in position:
        op.add_column("position_snapshot", sa.Column("broker_average_currency", sa.String(length=8), nullable=True))

    if "broker_sync_status" not in inspector.get_table_names():
        op.create_table(
            "broker_sync_status",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("broker", sa.String(length=64), nullable=False),
            sa.Column("connection_status", sa.String(length=32), nullable=False),
            sa.Column("last_successful_sync", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_snapshot_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("position_reconciliation_status", sa.String(length=16), nullable=True),
            sa.Column("cost_basis_reconciliation_status", sa.String(length=16), nullable=True),
            sa.Column("snapshot_integrity_status", sa.String(length=16), nullable=True),
            sa.Column("position_count", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("broker", name="uq_broker_sync_status_broker"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "broker_sync_status" in inspector.get_table_names():
        op.drop_table("broker_sync_status")
    position = _columns(inspector, "position_snapshot")
    if "broker_average_currency" in position:
        op.drop_column("position_snapshot", "broker_average_currency")
    if "broker_average_price" in position:
        op.drop_column("position_snapshot", "broker_average_price")
    indexes = {index["name"] for index in inspector.get_indexes("portfolio_snapshot")}
    if "uq_live_portfolio_snapshot_time" in indexes:
        op.drop_index("uq_live_portfolio_snapshot_time", table_name="portfolio_snapshot")
    if "corporate_action" in inspector.get_table_names():
        op.drop_table("corporate_action")
    cash = _columns(inspector, "cash_movement")
    if "fx_conversion_status" in cash:
        op.drop_column("cash_movement", "fx_conversion_status")
