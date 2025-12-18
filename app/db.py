from __future__ import annotations

import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.settings import settings


def _ensure_sqlite_dir(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    # Default url: sqlite+aiosqlite:///./data/app.db
    if "///" not in url:
        return
    path_part = url.split("///", 1)[1]
    # Strip query string if any
    path_part = path_part.split("?", 1)[0]
    # Relative path is relative to process cwd
    db_path = os.path.abspath(path_part)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def db_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def ensure_schema_migrations() -> None:
    """
    Minimal runtime migrations (no Alembic).
    Adds columns if they are missing, so upgrades work on existing DB.
    """
    async with engine.begin() as conn:
        driver = engine.url.get_backend_name()

        if driver.startswith("sqlite"):
            cols = (await conn.exec_driver_sql("PRAGMA table_info(schedules)")).all()
            existing = {row[1] for row in cols}  # name at index 1
            if "times_hhmm" not in existing:
                await conn.exec_driver_sql("ALTER TABLE schedules ADD COLUMN times_hhmm TEXT")
        else:
            # Postgres / others
            cols = (
                await conn.exec_driver_sql(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='schedules'"
                )
            ).all()
            existing = {row[0] for row in cols}
            if "times_hhmm" not in existing:
                await conn.exec_driver_sql("ALTER TABLE schedules ADD COLUMN times_hhmm TEXT")
