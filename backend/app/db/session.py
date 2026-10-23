"""异步数据库会话与健康探测（连接懒加载：import 本模块不会连库）。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={"connect_timeout": int(settings.db_connect_timeout_s) or 2},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def db_ping(timeout_s: float | None = None) -> bool:
    """健康检查用：1~2 秒内能 SELECT 1 才算 DB 可用，避免 /healthz 卡死。"""
    settings = get_settings()
    timeout = timeout_s if timeout_s is not None else settings.db_connect_timeout_s
    try:
        async with asyncio.timeout(timeout):
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - 健康检查只关心可用性
        return False


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@lru_cache(maxsize=1)
def _noop() -> None:  # 占位，保持模块导入无副作用
    return None
