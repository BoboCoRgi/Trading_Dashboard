"""Trade plans, execution legs and logical share allocations. None of these place orders."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_trade_plan"
down_revision = "0011_technical_setup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "trade_plan" not in inspector.get_table_names():
        op.create_table(
            "trade_plan",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("instrument.id"), nullable=False),
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("strategy", sa.String(64), nullable=False),
            sa.Column("timeframe", sa.String(8), nullable=False),
            sa.Column("direction", sa.String(8), nullable=False),
            sa.Column("entry_price", sa.Numeric(24, 8), nullable=True),
            sa.Column("entry_low", sa.Numeric(24, 8), nullable=True),
            sa.Column("entry_high", sa.Numeric(24, 8), nullable=True),
            sa.Column("stop_loss", sa.Numeric(24, 8), nullable=False),
            sa.Column("take_profit", sa.Numeric(24, 8), nullable=True),
            sa.Column("risk_amount", sa.Numeric(24, 8), nullable=True),
            sa.Column("risk_fraction", sa.Numeric(24, 8), nullable=True),
            sa.Column("capital_amount", sa.Numeric(24, 8), nullable=True),
            sa.Column("capital_currency", sa.String(8), nullable=False),
            sa.Column("shares", sa.Numeric(24, 8), nullable=False),
            sa.Column("risk_reward", sa.Numeric(24, 8), nullable=True),
            sa.Column("potential_profit", sa.Numeric(24, 8), nullable=True),
            sa.Column("potential_loss", sa.Numeric(24, 8), nullable=False),
            sa.Column("required_capital", sa.Numeric(24, 8), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("setup_id", sa.Uuid(), sa.ForeignKey("technical_setup.id"), nullable=True),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("data_origin", sa.String(64), nullable=False),
        )
    if "trade_plan_revision" not in inspector.get_table_names():
        op.create_table(
            "trade_plan_revision",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("trade_plan.id"), nullable=False),
            sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("snapshot", postgresql.JSONB(), nullable=False),
            sa.Column("data_origin", sa.String(64), nullable=False),
        )
    if "plan_fill" not in inspector.get_table_names():
        op.create_table(
            "plan_fill",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("trade_plan.id"), nullable=False),
            sa.Column("trade_id", sa.Uuid(), sa.ForeignKey("trade.id"), nullable=True),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("broker", sa.String(32), nullable=False),
            sa.Column("broker_execution_id", sa.String(128), nullable=False),
            sa.Column("action", sa.String(8), nullable=False),
            sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
            sa.Column("price", sa.Numeric(24, 8), nullable=False),
            sa.Column("currency", sa.String(8), nullable=False),
            sa.Column("fees_native", sa.Numeric(24, 8), nullable=False),
            sa.Column("fx_rate_to_gbp", sa.Numeric(24, 8), nullable=True),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("data_origin", sa.String(64), nullable=False),
        )
        op.create_index(
            "uq_plan_fill_trade",
            "plan_fill",
            ["trade_id"],
            unique=True,
            postgresql_where=sa.text("trade_id IS NOT NULL"),
        )
        op.create_index(
            "uq_plan_fill_execution",
            "plan_fill",
            ["broker_execution_id"],
            unique=True,
            postgresql_where=sa.text("broker_execution_id IS NOT NULL AND broker_execution_id <> ''"),
        )
    if "position_allocation" not in inspector.get_table_names():
        op.create_table(
            "position_allocation",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("instrument.id"), nullable=False),
            sa.Column("trading_quantity", sa.Numeric(24, 8), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("data_origin", sa.String(64), nullable=False),
            sa.UniqueConstraint("instrument_id", "data_origin", name="uq_position_allocation_instrument"),
        )


def downgrade() -> None:
    op.drop_table("position_allocation")
    op.drop_index("uq_plan_fill_execution", table_name="plan_fill")
    op.drop_index("uq_plan_fill_trade", table_name="plan_fill")
    op.drop_table("plan_fill")
    op.drop_table("trade_plan_revision")
    op.drop_table("trade_plan")
