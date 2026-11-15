"""Cache columns and source-aware uniqueness for market price bars.

Fresh installs already create the columns from the models. This revision
adds them to a database migrated before the chart cache existed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_price_bar_cache"
down_revision = "0007_ai_run_thinking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "market_price_bar" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("market_price_bar")}
    if "is_delayed" not in columns:
        op.add_column("market_price_bar", sa.Column("is_delayed", sa.Boolean(), nullable=True))
    if "fetched_at" not in columns:
        op.add_column("market_price_bar", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))
    constraints = inspector.get_unique_constraints("market_price_bar")
    current = next((item for item in constraints if item.get("name") == "uq_market_price_bar"), None)
    expected = ["instrument_id", "interval_code", "bar_start", "data_origin", "source"]
    if current is not None and list(current.get("column_names") or []) == expected:
        return
    if current is not None:
        op.drop_constraint("uq_market_price_bar", "market_price_bar", type_="unique")
    op.create_unique_constraint("uq_market_price_bar", "market_price_bar", expected)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "market_price_bar" not in inspector.get_table_names():
        return
    constraints = inspector.get_unique_constraints("market_price_bar")
    current = next((item for item in constraints if item.get("name") == "uq_market_price_bar"), None)
    previous = ["instrument_id", "interval_code", "bar_start", "data_origin"]
    if current is not None and list(current.get("column_names") or []) != previous:
        op.drop_constraint("uq_market_price_bar", "market_price_bar", type_="unique")
        op.create_unique_constraint("uq_market_price_bar", "market_price_bar", previous)
    columns = {column["name"] for column in inspector.get_columns("market_price_bar")}
    if "fetched_at" in columns:
        op.drop_column("market_price_bar", "fetched_at")
    if "is_delayed" in columns:
        op.drop_column("market_price_bar", "is_delayed")
