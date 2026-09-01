"""
SQLAlchemy ORM Models for TG Drive Cloud Source of Truth
=========================================================
Stores file and folder metadata in the shared database (PostgreSQL / SQLite).
Actual file contents remain securely stored in Telegram's distributed cloud infrastructure.

Phase 2 — Synchronization Engine:
  - ChangeLogModel: records every mutation (create/rename/move/trash/delete) with a
    monotonically-increasing version so clients can ask "what changed since version N?"
  - SyncVersionModel: single-row table holding the current global version counter.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    Column,
    String,
    BigInteger,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    JSON,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship, backref

Base = declarative_base()


def utc_now():
    return datetime.now(timezone.utc)


class FolderModel(Base):
    """
    Folder metadata model.
    Nested folders are structured using parent_folder_id foreign key references.
    The root folder has id='root' and parent_folder_id=None.
    """
    __tablename__ = "folders"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), default="admin", nullable=False, index=True)
    name = Column(String(255), nullable=False)
    parent_folder_id = Column(
        String(64),
        ForeignKey("folders.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    path = Column(Text, nullable=True)  # Human or ID path for fast queries
    trash = Column(Boolean, default=False, nullable=False, index=True)
    trashed_at = Column(DateTime(timezone=True), nullable=True)
    tags = Column(JSON, default=list, nullable=False)
    auth_hashes = Column(JSON, default=list, nullable=False)
    activity_history = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    subfolders = relationship(
        "FolderModel",
        backref=backref("parent", remote_side=[id]),
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    files = relationship(
        "FileModel",
        back_populates="folder",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_folders_parent_trash", "parent_folder_id", "trash"),
        Index("ix_folders_user_trash", "user_id", "trash"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "parent_folder_id": self.parent_folder_id,
            "path": self.path or "/",
            "type": "folder",
            "trash": bool(self.trash),
            "trashed_at": self.trashed_at.isoformat() if self.trashed_at else None,
            "tags": list(self.tags or []),
            "auth_hashes": list(self.auth_hashes or []),
            "activity_history": list(self.activity_history or []),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "contents": {},
        }


class FileModel(Base):
    """
    File metadata model.
    Contains stable file identifiers, Telegram storage pointers, MIME types, and checksums.
    """
    __tablename__ = "files"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), default="admin", nullable=False, index=True)
    name = Column(String(255), nullable=False)
    original_name = Column(String(255), nullable=False)
    mime_type = Column(String(128), nullable=True)
    extension = Column(String(128), nullable=True)
    size = Column(BigInteger, default=0, nullable=False)

    # Telegram Infrastructure Pointers
    telegram_message_id = Column(BigInteger, nullable=False, index=True)  # Primary file_id in storage channel
    telegram_file_id = Column(String(255), nullable=True)
    telegram_chat_id = Column(BigInteger, nullable=True)

    # Hierarchy
    folder_id = Column(
        String(64),
        ForeignKey("folders.id", ondelete="CASCADE"),
        nullable=False,
        default="root",
        index=True,
    )
    checksum = Column(String(64), nullable=True, index=True)  # SHA-256

    trash = Column(Boolean, default=False, nullable=False, index=True)
    trashed_at = Column(DateTime(timezone=True), nullable=True)
    tags = Column(JSON, default=list, nullable=False)
    metadata_extra = Column(JSON, default=dict, nullable=False)
    activity_history = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationship
    folder = relationship("FolderModel", back_populates="files")

    __table_args__ = (
        Index("ix_files_folder_trash", "folder_id", "trash"),
        Index("ix_files_user_trash", "user_id", "trash"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "original_name": self.original_name,
            "mime_type": self.mime_type,
            "extension": self.extension,
            "size": int(self.size or 0),
            "file_id": int(self.telegram_message_id or 0),  # Backwards-compatible file_id
            "telegram_message_id": int(self.telegram_message_id or 0),
            "telegram_file_id": self.telegram_file_id,
            "telegram_chat_id": self.telegram_chat_id,
            "folder_id": self.folder_id,
            "sha256": self.checksum,
            "checksum": self.checksum,
            "type": "file",
            "trash": bool(self.trash),
            "trashed_at": self.trashed_at.isoformat() if self.trashed_at else None,
            "tags": list(self.tags or []),
            "metadata_extra": dict(self.metadata_extra or {}),
            "activity_history": list(self.activity_history or []),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ---------------------------------------------------------------------------
# Phase 2 — Synchronization Engine Models
# ---------------------------------------------------------------------------


class SyncVersionModel(Base):
    """
    Single-row table holding the current global synchronization version counter.
    Every successful mutation increments this counter via an atomic UPDATE … RETURNING
    (or SELECT + UPDATE for SQLite).
    """
    __tablename__ = "sync_version"

    id = Column(String(32), primary_key=True, default="global")  # always "global"
    version = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": int(self.version),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ChangeLogModel(Base):
    """
    Immutable append-only log of every file/folder mutation.
    Clients poll GET /api/sync/changes?since=N to catch up on changes they missed.
    """
    __tablename__ = "sync_changelog"

    change_id = Column(BigInteger, primary_key=True, autoincrement=True)
    version = Column(BigInteger, nullable=False, index=True)  # monotonic version at time of change
    user_id = Column(String(64), nullable=False, default="admin", index=True)
    entity_id = Column(String(64), nullable=False, index=True)  # file or folder id
    entity_type = Column(String(16), nullable=False)  # "file" or "folder"
    operation = Column(String(32), nullable=False, index=True)
    # FILE_CREATED, FILE_UPDATED, FILE_RENAMED, FILE_MOVED, FILE_DELETED
    # FOLDER_CREATED, FOLDER_UPDATED, FOLDER_RENAMED, FOLDER_MOVED, FOLDER_DELETED
    old_name = Column(String(255), nullable=True)   # for RENAME tracking
    new_name = Column(String(255), nullable=True)   # for RENAME tracking
    old_folder_id = Column(String(64), nullable=True)  # for MOVE tracking
    new_folder_id = Column(String(64), nullable=True)  # for MOVE tracking
    extra = Column(JSON, default=dict, nullable=True)   # optional payload
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Indexes are managed by Alembic migration 0002_sync_tables
    # Do NOT define them here to avoid DuplicateTable errors from create_all()

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "change_id": self.change_id,
            "version": self.version,
            "user_id": self.user_id,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "operation": self.operation,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if self.old_name is not None:
            d["old_name"] = self.old_name
        if self.new_name is not None:
            d["new_name"] = self.new_name
        if self.old_folder_id is not None:
            d["old_folder_id"] = self.old_folder_id
        if self.new_folder_id is not None:
            d["new_folder_id"] = self.new_folder_id
        if self.extra:
            d["extra"] = self.extra
        return d
