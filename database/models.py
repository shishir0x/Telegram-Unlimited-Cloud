"""
SQLAlchemy ORM Models for TG Drive Cloud Source of Truth
=========================================================
Stores file and folder metadata in the shared database (PostgreSQL / SQLite).
Actual file contents remain securely stored in Telegram's distributed cloud infrastructure.
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
    )
    files = relationship(
        "FileModel",
        back_populates="folder",
        cascade="all, delete-orphan",
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
