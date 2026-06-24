from __future__ import annotations
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import get_settings

engine: AsyncEngine | None = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def _get_or_create_engine() -> AsyncEngine:
    global engine
    if engine is None:
        settings = get_settings()
        engine = create_async_engine(settings.database_url, future=True, pool_pre_ping=True)
    return engine


def _get_or_create_session_factory() -> async_sessionmaker[AsyncSession]:
    global AsyncSessionLocal
    if AsyncSessionLocal is None:
        AsyncSessionLocal = async_sessionmaker(_get_or_create_engine(), expire_on_commit=False)
    return AsyncSessionLocal


async def close_database() -> None:
    global engine, AsyncSessionLocal
    if engine is not None:
        await engine.dispose()
    engine = None
    AsyncSessionLocal = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return _get_or_create_session_factory()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session_factory = _get_or_create_session_factory()
    async with session_factory() as session:
        yield session
