"""Saved backtests. Simulated trades stay out of the trading journal."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_backtest_run"
down_revision = "0012_trade_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "backtest_run" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "backtest_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("instrument.id"), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("strategy_version", sa.String(32), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(), nullable=False),
        sa.Column("data_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("trades", postgresql.JSONB(), nullable=False),
        sa.Column("comparison", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_origin", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backtest_run")
