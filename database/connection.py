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
    engine_kwargs.update({
        "pool_recycle": 300,
        "pool_size": 10,
        "max_overflow": 20,
    })
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
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI Dependency providing a database session per request."""
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> bool:
    """
    Initializes database schema and verifies connectivity.
    Ensures root folder ('/') exists in the database.
    """
    from database.models import Base, FolderModel

    try:
        Base.metadata.create_all(bind=sync_engine)
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
