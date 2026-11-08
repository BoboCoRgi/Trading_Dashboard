"""Add market events and opportunity candidates.

Existing tables are left as they are. create_all only creates missing tables.
"""

from alembic import op

from db.base import Base
from db import models  # noqa: F401

revision = "0002_events_radar"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=[
        Base.metadata.tables["opportunity_outcome"],
        Base.metadata.tables["opportunity_candidate"],
        Base.metadata.tables["market_event"],
    ])
