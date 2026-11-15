"""Refresh timestamps for the Trading 212 background worker.

Fresh installs already create the columns from the models. This revision
adds them to a database migrated before the worker existed. Rows are not deleted.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_t212_refresh_status"
down_revision = "0008_price_bar_cache"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("last_current_refresh_at", sa.DateTime(timezone=True)),
    ("last_history_refresh_at", sa.DateTime(timezone=True)),
    ("last_refresh_error", sa.Text()),
    ("next_current_refresh_at", sa.DateTime(timezone=True)),
    ("next_history_refresh_at", sa.DateTime(timezone=True)),
    ("worker_heartbeat_at", sa.DateTime(timezone=True)),
    ("rate_limit_limit", sa.Integer()),
    ("rate_limit_remaining", sa.Integer()),
    ("rate_limit_reset_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "broker_sync_status" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("broker_sync_status")}
    for name, column_type in _COLUMNS:
        if name not in present:
            op.add_column("broker_sync_status", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "broker_sync_status" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("broker_sync_status")}
    for name, _column_type in reversed(_COLUMNS):
        if name in present:
            op.drop_column("broker_sync_status", name)
