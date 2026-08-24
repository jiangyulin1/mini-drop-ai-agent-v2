"""Persist operator-controlled Agent collection dispatch mode."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0036_agent_collection_mode"
down_revision = "0035_investigation_tree"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Very old local databases may only contain the original task tables. The
    # release migration must remain expand-only for those databases.
    if "agents" not in inspect(op.get_bind()).get_table_names():
        return
    op.add_column("agents", sa.Column("collection_enabled", sa.Boolean(), nullable=True))
    op.execute("UPDATE agents SET collection_enabled = TRUE WHERE collection_enabled IS NULL")
    # SQLite cannot ALTER COLUMN in place; batch mode rebuilds the table while
    # preserving existing agent rows for the test/local deployment database.
    with op.batch_alter_table("agents") as batch:
        batch.alter_column("collection_enabled", nullable=False, server_default=sa.true())


def downgrade() -> None:
    op.drop_column("agents", "collection_enabled")
