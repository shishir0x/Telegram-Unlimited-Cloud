"""enable_rls_on_public_tables

Revision ID: 0003_enable_rls
Revises: 0002_sync_tables
Create Date: 2026-09-16 09:05:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_enable_rls"
down_revision: Union[str, None] = "0002_sync_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # Check if we are running on PostgreSQL
    if conn.dialect.name == "postgresql":
        tables = ["folders", "files", "sync_version", "sync_changelog"]
        for tbl in tables:
            try:
                op.execute(f"ALTER TABLE public.{tbl} ENABLE ROW LEVEL SECURITY;")
            except Exception:
                pass


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        tables = ["folders", "files", "sync_version", "sync_changelog"]
        for tbl in tables:
            try:
                op.execute(f"ALTER TABLE public.{tbl} DISABLE ROW LEVEL SECURITY;")
            except Exception:
                pass
