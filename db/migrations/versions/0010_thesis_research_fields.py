"""Fields the research thesis editor stores on the existing thesis row."""

import sqlalchemy as sa
from alembic import op

revision = "0010_thesis_research_fields"
down_revision = "0009_t212_refresh_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "thesis" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("thesis")}
    if "assumptions" not in columns:
        op.add_column("thesis", sa.Column("assumptions", sa.Text(), nullable=False, server_default=""))
    if "risks" not in columns:
        op.add_column("thesis", sa.Column("risks", sa.Text(), nullable=False, server_default=""))
    if "conviction" not in columns:
        op.add_column("thesis", sa.Column("conviction", sa.Integer(), nullable=True))
    if "last_reviewed_at" not in columns:
        op.add_column("thesis", sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "thesis" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("thesis")}
    for name in ("last_reviewed_at", "conviction", "risks", "assumptions"):
        if name in columns:
            op.drop_column("thesis", name)
