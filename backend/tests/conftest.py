"""测试基座：强制离线（FakeLLM）、把 DB 指向必然失败的地址，保证单测零外部依赖。"""

from __future__ import annotations

import os

os.environ.setdefault("FORCE_FAKE_LLM", "1")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://app:app@127.0.0.1:1/procurement")
os.environ.setdefault("DB_CONNECT_TIMEOUT_S", "0.6")
os.environ.setdefault("DEMO_STEP_DELAY_MS", "1")
os.environ.setdefault("SSE_POLL_INTERVAL_MS", "20")

import pytest  # noqa: E402

from app.core.loop import ensure_windows_selector_loop  # noqa: E402

# pytest-asyncio 会在创建循环前读取策略；必须在任何 async fixture 之前切换（GOTCHAS G-009）
ensure_windows_selector_loop(force=True)

from app.db.session import db_ping  # noqa: E402


@pytest.fixture
async def db_ready() -> bool:
    """集成测试用：DB 不可达时 skip，而不是红一片（ADR-002）。"""
    ok = await db_ping(timeout_s=0.6)
    if not ok:
        pytest.skip("PostgreSQL 不可用：先 `docker compose up -d db` 再跑 `pytest -m integration`")
    return True
