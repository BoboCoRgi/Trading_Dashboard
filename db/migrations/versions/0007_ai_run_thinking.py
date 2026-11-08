"""Record which Gemini thinking level produced an analysis."""

import sqlalchemy as sa
from alembic import op

revision = "0007_ai_run_thinking"
down_revision = "0006_instrument_alias"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("ai_run")}
    if "thinking_level" not in columns:
        op.add_column("ai_run", sa.Column("thinking_level", sa.String(length=16), nullable=True))
    if "status" not in columns:
        op.add_column(
            "ai_run",
            sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
        )
    if "error_detail" not in columns:
        op.add_column("ai_run", sa.Column("error_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("ai_run")}
    if "error_detail" in columns:
        op.drop_column("ai_run", "error_detail")
    if "status" in columns:
        op.drop_column("ai_run", "status")
    if "thinking_level" in columns:
        op.drop_column("ai_run", "thinking_level")
