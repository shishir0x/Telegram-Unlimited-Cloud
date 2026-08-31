"""initial_schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-31 14:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create folders table
    op.create_table(
        "folders",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False, server_default="admin"),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("parent_folder_id", sa.String(length=64), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("trash", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("auth_hashes", sa.JSON(), nullable=False),
        sa.Column("activity_history", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_folder_id"], ["folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_folders_id", "folders", ["id"], unique=False)
    op.create_index("ix_folders_user_id", "folders", ["user_id"], unique=False)
    op.create_index("ix_folders_parent_folder_id", "folders", ["parent_folder_id"], unique=False)
    op.create_index("ix_folders_trash", "folders", ["trash"], unique=False)
    op.create_index("ix_folders_parent_trash", "folders", ["parent_folder_id", "trash"], unique=False)
    op.create_index("ix_folders_user_trash", "folders", ["user_id", "trash"], unique=False)

    # 2. Create files table
    op.create_table(
        "files",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False, server_default="admin"),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("extension", sa.String(length=32), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("folder_id", sa.String(length=64), nullable=False, server_default="root"),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("trash", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("metadata_extra", sa.JSON(), nullable=False),
        sa.Column("activity_history", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_files_id", "files", ["id"], unique=False)
    op.create_index("ix_files_user_id", "files", ["user_id"], unique=False)
    op.create_index("ix_files_telegram_message_id", "files", ["telegram_message_id"], unique=False)
    op.create_index("ix_files_folder_id", "files", ["folder_id"], unique=False)
    op.create_index("ix_files_checksum", "files", ["checksum"], unique=False)
    op.create_index("ix_files_trash", "files", ["trash"], unique=False)
    op.create_index("ix_files_folder_trash", "files", ["folder_id", "trash"], unique=False)
    op.create_index("ix_files_user_trash", "files", ["user_id", "trash"], unique=False)


def downgrade() -> None:
    op.drop_table("files")
    op.drop_table("folders")
