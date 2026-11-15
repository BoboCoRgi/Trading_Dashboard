"""Persist rule-based technical setups. Status is text so a later rule does not need a new enum."""

import sqlalchemy as sa
from alembic import op

revision = "0011_technical_setup"
down_revision = "0010_thesis_research_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "technical_setup" in inspector.get_table_names():
        return
    op.create_table(
        "technical_setup",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("instrument.id"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("setup_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candle_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level_low", sa.Numeric(24, 8), nullable=True),
        sa.Column("level_high", sa.Numeric(24, 8), nullable=True),
        sa.Column("close_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("rvol", sa.Numeric(24, 8), nullable=True),
        sa.Column("ema20", sa.Numeric(24, 8), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("invalidation", sa.Text(), nullable=False),
        sa.Column("structure_key", sa.String(64), nullable=False),
        sa.Column("data_status", sa.String(16), nullable=False),
        sa.Column("current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_origin", sa.String(64), nullable=False),
    )
    op.create_index(
        "uq_technical_setup_active",
        "technical_setup",
        ["instrument_id", "timeframe", "structure_key", "data_origin"],
        unique=True,
        postgresql_where=sa.text("status <> 'INVALIDATED'"),
    )


def downgrade() -> None:
    op.drop_index("uq_technical_setup_active", table_name="technical_setup")
    op.drop_table("technical_setup")
