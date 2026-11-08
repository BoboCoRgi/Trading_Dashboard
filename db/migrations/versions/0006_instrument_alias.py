"""Ticker aliases for one canonical instrument.

Fresh installs already create the table from the models. This revision adds it
to a database that was migrated before the table existed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_instrument_alias"
down_revision = "0005_market_bars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "instrument_alias" in set(inspector.get_table_names()):
        return
    op.create_table(
        "instrument_alias",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("instrument.id"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alias_type", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.UniqueConstraint("instrument_id", "symbol", "alias_type", name="uq_instrument_alias"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "instrument_alias" in set(inspector.get_table_names()):
        op.drop_table("instrument_alias")
