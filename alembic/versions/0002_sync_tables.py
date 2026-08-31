"""add_sync_tables

Revision ID: 0002_sync_tables
Revises: 0001_initial_schema
Create Date: 2026-08-31 16:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_sync_tables"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table already exists in the database (idempotent helper)."""
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(conn)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    conn = op.get_bind()

    # 1. sync_version — single-row global version counter
    if not _table_exists(conn, "sync_version"):
        op.create_table(
            "sync_version",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        # Seed the single global row
        op.execute("INSERT INTO sync_version (id, version, updated_at) VALUES ('global', 0, NOW())")
    else:
        # Table exists — ensure seed row is present
        op.execute(
            "INSERT INTO sync_version (id, version, updated_at) VALUES ('global', 0, NOW()) "
            "ON CONFLICT (id) DO NOTHING"
        )

    # 2. sync_changelog — append-only mutation log
    if not _table_exists(conn, "sync_changelog"):
        op.create_table(
            "sync_changelog",
            sa.Column("change_id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("version", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False, server_default="admin"),
            sa.Column("entity_id", sa.String(length=64), nullable=False),
            sa.Column("entity_type", sa.String(length=16), nullable=False),
            sa.Column("operation", sa.String(length=32), nullable=False),
            sa.Column("old_name", sa.String(length=255), nullable=True),
            sa.Column("new_name", sa.String(length=255), nullable=True),
            sa.Column("old_folder_id", sa.String(length=64), nullable=True),
            sa.Column("new_folder_id", sa.String(length=64), nullable=True),
            sa.Column("extra", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("change_id"),
        )
        op.create_index("ix_sync_changelog_version", "sync_changelog", ["version"], unique=False)
        op.create_index("ix_sync_changelog_user_version", "sync_changelog", ["user_id", "version"], unique=False)
        op.create_index("ix_sync_changelog_operation", "sync_changelog", ["operation"], unique=False)
        op.create_index("ix_sync_changelog_entity_id", "sync_changelog", ["entity_id"], unique=False)


def downgrade() -> None:
    op.drop_table("sync_changelog")
    op.drop_table("sync_version")
