"""Add native currency fields on cash movements.

Existing mock rows are backfilled as GBP. amount_gbp stays required.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_cash_movement_native"
down_revision = "0002_events_radar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("cash_movement")}
    if "currency" not in columns:
        op.add_column("cash_movement", sa.Column("currency", sa.String(length=8), nullable=True))
    if "amount_native" not in columns:
        op.add_column("cash_movement", sa.Column("amount_native", sa.Numeric(24, 8), nullable=True))
    if "instrument_id" not in columns:
        op.add_column(
            "cash_movement",
            sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("instrument.id"), nullable=True),
        )
    op.execute("UPDATE cash_movement SET currency = 'GBP' WHERE currency IS NULL")
    op.execute("UPDATE cash_movement SET amount_native = amount_gbp WHERE amount_native IS NULL")
    indexes = {index["name"] for index in inspector.get_indexes("cash_movement")}
    if "uq_cash_movement_source_event" not in indexes:
        op.create_index(
            "uq_cash_movement_source_event",
            "cash_movement",
            ["source_event_id"],
            unique=True,
            postgresql_where=sa.text("source_event_id IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("cash_movement")}
    if "uq_cash_movement_source_event" in indexes:
        op.drop_index("uq_cash_movement_source_event", table_name="cash_movement")
    columns = {column["name"] for column in inspector.get_columns("cash_movement")}
    if "instrument_id" in columns:
        op.drop_column("cash_movement", "instrument_id")
    if "amount_native" in columns:
        op.drop_column("cash_movement", "amount_native")
    if "currency" in columns:
        op.drop_column("cash_movement", "currency")
