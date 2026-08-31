"""
Database Repository for TG Drive
=================================
Provides full CRUD, search, hierarchy traversal, and legacy import operations
against the shared database.
"""

import mimetypes
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy import or_, and_, func, select
from sqlalchemy.orm import Session

from database.connection import get_db_session
from database.models import FolderModel, FileModel, ChangeLogModel, SyncVersionModel, utc_now
from utils.logger import Logger

logger = Logger(__name__)


class DatabaseRepository:
    """Repository handling all database operations for folders and files."""

    @staticmethod
    def get_folder(folder_id: str, session: Optional[Session] = None) -> Optional[FolderModel]:
        def _query(s: Session):
            return s.query(FolderModel).filter(FolderModel.id == folder_id).first()

        if session:
            return _query(session)
        with get_db_session() as s:
            f = _query(s)
            if f:
                s.expunge(f)
            return f

    @staticmethod
    def get_file(file_id: str, session: Optional[Session] = None) -> Optional[FileModel]:
        def _query(s: Session):
            return s.query(FileModel).filter(FileModel.id == file_id).first()

        if session:
            return _query(session)
        with get_db_session() as s:
            f = _query(s)
            if f:
                s.expunge(f)
            return f

    @staticmethod
    def get_folder_children(
        folder_id: str,
        include_trash: bool = False,
        session: Optional[Session] = None,
    ) -> Tuple[List[FolderModel], List[FileModel]]:
        def _query(s: Session):
            f_q = s.query(FolderModel).filter(FolderModel.parent_folder_id == folder_id)
            file_q = s.query(FileModel).filter(FileModel.folder_id == folder_id)
            if not include_trash:
                f_q = f_q.filter(FolderModel.trash.is_(False))
                file_q = file_q.filter(FileModel.trash.is_(False))
            folders = f_q.order_by(FolderModel.name.asc()).all()
            files = file_q.order_by(FileModel.name.asc()).all()
            return folders, files

        if session:
            return _query(session)
        with get_db_session() as s:
            folders, files = _query(s)
            for item in folders + files:
                s.expunge(item)
            return folders, files

    @staticmethod
    def create_folder(
        id: str,
        name: str,
        parent_folder_id: Optional[str] = "root",
        path: str = "/",
        user_id: str = "admin",
        session: Optional[Session] = None,
    ) -> FolderModel:
        def _op(s: Session):
            existing = s.query(FolderModel).filter(FolderModel.id == id).first()
            if existing:
                existing.name = name
                existing.parent_folder_id = parent_folder_id
                existing.path = path
                existing.updated_at = utc_now()
                return existing

            folder = FolderModel(
                id=id,
                name=name,
                parent_folder_id=parent_folder_id if id != "root" else None,
                path=path,
                user_id=user_id,
                trash=False,
                tags=[],
                auth_hashes=[],
                activity_history=[],
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            s.add(folder)
            s.flush()
            return folder

        if session:
            return _op(session)
        with get_db_session() as s:
            f = _op(s)
            s.expunge(f)
            return f

    @staticmethod
    def create_file(
        id: str,
        name: str,
        telegram_message_id: int,
        size: int,
        folder_id: str = "root",
        original_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        checksum: Optional[str] = None,
        user_id: str = "admin",
        metadata_extra: Optional[dict] = None,
        created_at: Optional[datetime] = None,
        session: Optional[Session] = None,
    ) -> FileModel:
        orig = original_name or name
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        mime = mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream"

        def _op(s: Session):
            existing = s.query(FileModel).filter(FileModel.id == id).first()
            if existing:
                existing.name = name
                existing.size = size
                existing.telegram_message_id = telegram_message_id
                existing.folder_id = folder_id
                existing.checksum = checksum or existing.checksum
                existing.updated_at = utc_now()
                return existing

            file_obj = FileModel(
                id=id,
                user_id=user_id,
                name=name,
                original_name=orig,
                mime_type=mime,
                extension=ext,
                size=size,
                telegram_message_id=telegram_message_id,
                folder_id=folder_id,
                checksum=checksum,
                trash=False,
                tags=[],
                metadata_extra=metadata_extra or {},
                activity_history=[],
                created_at=created_at or utc_now(),
                updated_at=utc_now(),
            )
            s.add(file_obj)
            s.flush()
            return file_obj

        if session:
            return _op(session)
        with get_db_session() as s:
            f = _op(s)
            s.expunge(f)
            return f

    @staticmethod
    def rename_item(item_id: str, new_name: str, session: Optional[Session] = None) -> bool:
        def _op(s: Session):
            folder = s.query(FolderModel).filter(FolderModel.id == item_id).first()
            if folder:
                folder.name = new_name
                folder.updated_at = utc_now()
                return True
            file_obj = s.query(FileModel).filter(FileModel.id == item_id).first()
            if file_obj:
                file_obj.name = new_name
                if "." in new_name:
                    file_obj.extension = "." + new_name.rsplit(".", 1)[-1].lower()
                file_obj.mime_type = mimetypes.guess_type(new_name)[0] or file_obj.mime_type
                file_obj.updated_at = utc_now()
                return True
            return False

        if session:
            return _op(session)
        with get_db_session() as s:
            return _op(s)

    @staticmethod
    def move_item(item_id: str, dest_folder_id: str, session: Optional[Session] = None) -> bool:
        def _op(s: Session):
            dest = s.query(FolderModel).filter(FolderModel.id == dest_folder_id).first()
            if not dest:
                return False

            folder = s.query(FolderModel).filter(FolderModel.id == item_id).first()
            if folder:
                folder.parent_folder_id = dest_folder_id
                folder.updated_at = utc_now()
                return True

            file_obj = s.query(FileModel).filter(FileModel.id == item_id).first()
            if file_obj:
                file_obj.folder_id = dest_folder_id
                file_obj.updated_at = utc_now()
                return True

            return False

        if session:
            return _op(session)
        with get_db_session() as s:
            return _op(s)

    @staticmethod
    def trash_item(item_id: str, trash: bool, session: Optional[Session] = None) -> bool:
        def _op(s: Session):
            now = utc_now() if trash else None
            folder = s.query(FolderModel).filter(FolderModel.id == item_id).first()
            if folder:
                folder.trash = trash
                folder.trashed_at = now
                folder.updated_at = utc_now()
                return True

            file_obj = s.query(FileModel).filter(FileModel.id == item_id).first()
            if file_obj:
                file_obj.trash = trash
                file_obj.trashed_at = now
                file_obj.updated_at = utc_now()
                return True
            return False

        if session:
            return _op(session)
        with get_db_session() as s:
            return _op(s)

    @staticmethod
    def update_tags(item_id: str, tags: List[str], session: Optional[Session] = None) -> bool:
        def _op(s: Session):
            folder = s.query(FolderModel).filter(FolderModel.id == item_id).first()
            if folder:
                folder.tags = list(tags)
                folder.updated_at = utc_now()
                return True
            file_obj = s.query(FileModel).filter(FileModel.id == item_id).first()
            if file_obj:
                file_obj.tags = list(tags)
                file_obj.updated_at = utc_now()
                return True
            return False

        if session:
            return _op(session)
        with get_db_session() as s:
            return _op(s)

    @classmethod
    def delete_item(cls, item_id: str, session: Optional[Session] = None) -> List[int]:
        """
        Permanently deletes an item from the database.
        If the item is a folder, recursively collects all Telegram message IDs from
        its contained files and deletes all child folders and files.
        Returns list of deleted Telegram message IDs.
        """
        def _op(s: Session):
            telegram_ids: List[int] = []

            # Check if file
            file_obj = s.query(FileModel).filter(FileModel.id == item_id).first()
            if file_obj:
                if file_obj.telegram_message_id:
                    telegram_ids.append(int(file_obj.telegram_message_id))
                s.delete(file_obj)
                return telegram_ids

            # Check if folder
            folder = s.query(FolderModel).filter(FolderModel.id == item_id).first()
            if folder:
                def _collect_and_delete_folder(fld: FolderModel):
                    # Child files
                    for child_file in s.query(FileModel).filter(FileModel.folder_id == fld.id).all():
                        if child_file.telegram_message_id:
                            telegram_ids.append(int(child_file.telegram_message_id))
                        s.delete(child_file)
                    # Subfolders
                    for sub in s.query(FolderModel).filter(FolderModel.parent_folder_id == fld.id).all():
                        _collect_and_delete_folder(sub)
                    s.delete(fld)

                _collect_and_delete_folder(folder)
                return telegram_ids

            return telegram_ids

        if session:
            return _op(session)
        with get_db_session() as s:
            return _op(s)

    @classmethod
    def bulk_delete_items(cls, item_ids: List[str], session: Optional[Session] = None) -> List[int]:
        all_deleted_msg_ids: List[int] = []
        if session:
            for iid in item_ids:
                all_deleted_msg_ids.extend(cls.delete_item(iid, session=session))
            return all_deleted_msg_ids

        with get_db_session() as s:
            for iid in item_ids:
                all_deleted_msg_ids.extend(cls.delete_item(iid, session=s))
            return all_deleted_msg_ids

    @staticmethod
    def get_all_trashed(session: Optional[Session] = None) -> Tuple[List[FolderModel], List[FileModel]]:
        def _query(s: Session):
            folders = s.query(FolderModel).filter(FolderModel.trash.is_(True)).all()
            files = s.query(FileModel).filter(FileModel.trash.is_(True)).all()
            return folders, files

        if session:
            return _query(session)
        with get_db_session() as s:
            folders, files = _query(s)
            for item in folders + files:
                s.expunge(item)
            return folders, files

    @staticmethod
    def get_recent_files(limit: int = 50, session: Optional[Session] = None) -> List[FileModel]:
        def _query(s: Session):
            return (
                s.query(FileModel)
                .filter(FileModel.trash.is_(False))
                .order_by(FileModel.created_at.desc())
                .limit(limit)
                .all()
            )

        if session:
            return _query(session)
        with get_db_session() as s:
            files = _query(s)
            for f in files:
                s.expunge(f)
            return files

    @staticmethod
    def search_items(query: str, session: Optional[Session] = None) -> Tuple[List[FolderModel], List[FileModel]]:
        term = f"%{query.strip()}%"

        def _query(s: Session):
            folders = (
                s.query(FolderModel)
                .filter(and_(FolderModel.name.ilike(term), FolderModel.trash.is_(False)))
                .limit(100)
                .all()
            )
            files = (
                s.query(FileModel)
                .filter(and_(FileModel.name.ilike(term), FileModel.trash.is_(False)))
                .limit(200)
                .all()
            )
            return folders, files

        if session:
            return _query(session)
        with get_db_session() as s:
            folders, files = _query(s)
            for item in folders + files:
                s.expunge(item)
            return folders, files

    @staticmethod
    def count_total_items(session: Optional[Session] = None) -> Tuple[int, int]:
        def _query(s: Session):
            folder_cnt = s.query(func.count(FolderModel.id)).scalar() or 0
            file_cnt = s.query(func.count(FileModel.id)).scalar() or 0
            return int(folder_cnt), int(file_cnt)

        if session:
            return _query(session)
        with get_db_session() as s:
            return _query(s)

    # -----------------------------------------------------------------------
    # Phase 2 — Synchronization Repository Methods
    # -----------------------------------------------------------------------

    @staticmethod
    def get_current_version(session: Optional[Session] = None) -> int:
        """Returns the current global sync version (monotonic counter)."""
        def _query(s: Session) -> int:
            row = s.query(SyncVersionModel).filter(SyncVersionModel.id == "global").first()
            if not row:
                # Seed the row if missing
                row = SyncVersionModel(id="global", version=0)
                s.add(row)
                s.flush()
            return int(row.version)

        if session:
            return _query(session)
        with get_db_session() as s:
            return _query(s)

    @staticmethod
    def increment_version(session: Optional[Session] = None) -> int:
        """
        Atomically increments the global sync version and returns the new value.
        Uses an atomic UPDATE to prevent lost increments under concurrency.
        """
        def _op(s: Session) -> int:
            row = s.query(SyncVersionModel).filter(SyncVersionModel.id == "global").first()
            if not row:
                row = SyncVersionModel(id="global", version=1)
                s.add(row)
                s.flush()
                return 1
            row.version = int(row.version) + 1
            row.updated_at = utc_now()
            s.flush()
            return int(row.version)

        if session:
            return _op(session)
        with get_db_session() as s:
            new_ver = _op(s)
            return new_ver

    @staticmethod
    def record_change(
        version: int,
        user_id: str,
        entity_id: str,
        entity_type: str,
        operation: str,
        old_name: Optional[str] = None,
        new_name: Optional[str] = None,
        old_folder_id: Optional[str] = None,
        new_folder_id: Optional[str] = None,
        extra: Optional[dict] = None,
        session: Optional[Session] = None,
    ) -> ChangeLogModel:
        """
        Records a change event in the changelog. This is append-only.
        """
        def _op(s: Session) -> ChangeLogModel:
            entry = ChangeLogModel(
                version=version,
                user_id=user_id,
                entity_id=entity_id,
                entity_type=entity_type,
                operation=operation,
                old_name=old_name,
                new_name=new_name,
                old_folder_id=old_folder_id,
                new_folder_id=new_folder_id,
                extra=extra or {},
                created_at=utc_now(),
            )
            s.add(entry)
            s.flush()
            s.expunge(entry)
            return entry

        if session:
            return _op(session)
        with get_db_session() as s:
            entry = _op(s)
            return entry

    @staticmethod
    def get_changes_since(
        user_id: str,
        since_version: int,
        limit: int = 500,
        session: Optional[Session] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns changelog entries with version > since_version for the given user.
        """
        def _query(s: Session) -> List[Dict[str, Any]]:
            rows = (
                s.query(ChangeLogModel)
                .filter(
                    ChangeLogModel.version > since_version,
                    ChangeLogModel.user_id == user_id,
                )
                .order_by(ChangeLogModel.version.asc())
                .limit(limit)
                .all()
            )
            return [r.to_dict() for r in rows]

        if session:
            return _query(session)
        with get_db_session() as s:
            return _query(s)

    @staticmethod
    def get_last_change(user_id: Optional[str] = None, session: Optional[Session] = None) -> Optional[Dict[str, Any]]:
        """Returns the most recent changelog entry, optionally filtered by user."""
        def _query(s: Session) -> Optional[Dict[str, Any]]:
            q = s.query(ChangeLogModel)
            if user_id:
                q = q.filter(ChangeLogModel.user_id == user_id)
            row = q.order_by(ChangeLogModel.version.desc()).first()
            return row.to_dict() if row else None

        if session:
            return _query(session)
        with get_db_session() as s:
            return _query(s)
