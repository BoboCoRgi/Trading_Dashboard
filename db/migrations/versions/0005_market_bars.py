"""Price bars and explicit benchmark mappings.

Fresh installs already create these tables from the models. This revision
adds them to a database that was migrated before the tables existed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_market_bars"
down_revision = "0004_live_snapshot_cleanup"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(24, 8)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "market_price_bar" not in tables:
        op.create_table(
            "market_price_bar",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("instrument.id"), nullable=False),
            sa.Column("bar_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("interval_code", sa.String(8), nullable=False),
            sa.Column("open", MONEY, nullable=True),
            sa.Column("high", MONEY, nullable=True),
            sa.Column("low", MONEY, nullable=True),
            sa.Column("close", MONEY, nullable=False),
            sa.Column("volume", MONEY, nullable=True),
            sa.Column("currency", sa.String(8), nullable=False),
            sa.Column("source", sa.String(64), nullable=False),
            sa.Column("data_origin", sa.String(64), nullable=False),
            sa.UniqueConstraint(
                "instrument_id",
                "interval_code",
                "bar_start",
                "data_origin",
                name="uq_market_price_bar",
            ),
        )
    if "benchmark_mapping" not in tables:
        op.create_table(
            "benchmark_mapping",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("instrument.id"), nullable=False),
            sa.Column("market_benchmark_symbol", sa.String(32), nullable=True),
            sa.Column("sector_benchmark_symbol", sa.String(32), nullable=True),
            sa.Column("definition", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("data_origin", sa.String(64), nullable=False),
            sa.UniqueConstraint("instrument_id", "data_origin", name="uq_benchmark_mapping_instrument"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "benchmark_mapping" in tables:
        op.drop_table("benchmark_mapping")
    if "market_price_bar" in tables:
        op.drop_table("market_price_bar")
