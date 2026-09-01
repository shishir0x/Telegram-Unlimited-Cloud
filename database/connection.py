"""
Database Connection & Session Management
=========================================
Provides synchronous and asynchronous database engines, session factories,
health verification, and schema initialization for TG Drive.
"""

import os
from contextlib import contextmanager
from typing import Generator, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import config
from utils.logger import Logger

logger = Logger(__name__)

# Connection Engine Arguments
engine_kwargs = {
    "pool_pre_ping": True,
}

if config.IS_REMOTE_DB:
    # Supabase Transaction Pooler (port 6543 / pooler.supabase.com) uses server-side PgBouncer.
    # NullPool avoids client-side stale connection retention and socket termination errors.
    from sqlalchemy.pool import NullPool
    engine_kwargs["poolclass"] = NullPool
    engine_kwargs["connect_args"] = {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
else:
    # SQLite local engine settings
    engine_kwargs["connect_args"] = {"check_same_thread": False}

# Synchronous Engine & Session
sync_engine = create_engine(config.SYNC_DATABASE_URL, **engine_kwargs)
SyncSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

# Asynchronous Engine & Session
async_engine_kwargs = {"pool_pre_ping": True}
if not config.IS_REMOTE_DB:
    async_engine_kwargs["connect_args"] = {"check_same_thread": False}

try:
    async_engine = create_async_engine(config.ASYNC_DATABASE_URL, **async_engine_kwargs)
    AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)
except Exception as e:
    logger.warning(f"Async database engine creation note: {e}")
    async_engine = None
    AsyncSessionLocal = None


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Context manager for scoped database sessions with automatic rollback on exception."""
    session: Session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            session.close()
        except Exception:
            pass


def get_db():
    """FastAPI Dependency providing a database session per request."""
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_sync_tables():
    """
    Creates sync tables if they don't exist and seeds the global version row.
    Handles the case where Alembic migration already ran (indexes exist) by
    falling back to individual table creation.
    """
    from database.models import Base, SyncVersionModel
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(sync_engine)
    existing_tables = set(inspector.get_table_names())

    # Create only missing tables (avoids DuplicateTable index errors from create_all)
    tables_to_create = []
    for table_name, table_obj in Base.metadata.tables.items():
        if table_name not in existing_tables:
            tables_to_create.append(table_obj)

    if tables_to_create:
        try:
            # Use metadata.create_all but only for missing tables
            for table in tables_to_create:
                try:
                    table.create(bind=sync_engine, checkfirst=True)
                except Exception as tbl_err:
                    logger.debug(f"Table {table.name} creation note: {tbl_err}")
        except Exception as e:
            logger.debug(f"Sync table creation note: {e}")

    # Ensure global version row exists
    try:
        with get_db_session() as session:
            row = session.query(SyncVersionModel).filter(SyncVersionModel.id == "global").first()
            if not row:
                row = SyncVersionModel(id="global", version=0)
                session.add(row)
                logger.info("Seeded global sync version row (version=0).")
    except Exception as e:
        logger.debug(f"Sync version seed note: {e}")


_db_initialized = False


def init_db(force: bool = False) -> bool:
    """
    Initializes database schema and verifies connectivity.
    Ensures root folder ('/') exists in the database. Idempotent.
    """
    global _db_initialized
    if _db_initialized and not force:
        return True

    from database.models import Base, FolderModel

    try:
        # Check which tables already exist and only create missing ones.
        # Using checkfirst=True avoids DuplicateTable errors when Alembic
        # migrations have already created the schema.
        try:
            from sqlalchemy import inspect as sa_inspect
            inspector = sa_inspect(sync_engine)
            existing = set(inspector.get_table_names())
            for table_name, table_obj in Base.metadata.tables.items():
                if table_name not in existing:
                    try:
                        table_obj.create(bind=sync_engine, checkfirst=True)
                    except Exception as tbl_err:
                        logger.debug(f"Table '{table_name}' creation note: {tbl_err}")
        except Exception:
            # Fallback: try create_all with checkfirst (default True)
            try:
                Base.metadata.create_all(bind=sync_engine, checkfirst=True)
            except Exception as ca_err:
                logger.debug(f"create_all note (non-fatal): {ca_err}")

        # Ensure the sync version seed row exists
        _ensure_sync_tables()

        logger.info(f"Database schema initialized ({'PostgreSQL' if config.IS_REMOTE_DB else 'SQLite'}).")

        # Ensure root folder exists
        with get_db_session() as session:
            root = session.query(FolderModel).filter_by(id="root").first()
            if not root:
                root = FolderModel(
                    id="root",
                    name="/",
                    parent_folder_id=None,
                    path="/",
                    trash=False,
                    user_id="admin",
                )
                session.add(root)
                logger.info("Initialized root folder in database.")

        _db_initialized = True
        return True
    except Exception as e:
        logger.error(f"Failed to initialize database schema: {e}")
        return False


def test_database_connection() -> tuple[bool, str]:
    """Tests connectivity to the configured database and returns (is_connected, message)."""
    try:
        with sync_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_type = "PostgreSQL" if config.IS_REMOTE_DB else "SQLite"
        return True, f"Successfully connected to {db_type} database."
    except Exception as e:
        return False, f"Database connection error: {str(e)}"
