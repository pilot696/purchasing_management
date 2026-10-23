"""集成测试（需 PostgreSQL）：事件序号并发安全、回放、运行生命周期。

跑法：
    docker compose up -d db
    cd backend && ../.venv/Scripts/python -m pytest -m integration -q
无 DB 时由 `db_ready` fixture 自动 skip（ADR-002）。
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.base import Base
from app.db.session import db_ping, get_engine, get_session_factory
from app.models import tables  # noqa: F401  # 注册 metadata
from app.models.tables import AgentRun
from app.observability.runevents import EventBus

pytestmark = pytest.mark.integration


@pytest.fixture
async def schema():
    if not await db_ping(timeout_s=0.6):
        pytest.skip("PostgreSQL 不可用：先 `docker compose up -d db`")
    async with get_engine().begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
    return True


@pytest.fixture
async def run_id(schema) -> str:
    rid = uuid4()
    async with get_session_factory()() as session:
        session.add(AgentRun(id=rid, module="m1", subject_ref={"pr_id": "PR-IT-1"}, thread_id=str(rid)))
        await session.commit()
    return str(rid)


async def test_seq_allocation_is_monotonic_under_concurrency(run_id: str) -> None:
    bus = EventBus(get_session_factory())
    rid = run_id
    events = await asyncio.gather(*[bus.emit(rid, "node.start", {"node": f"n{i}"}) for i in range(10)])
    seqs = sorted(e.seq for e in events)
    assert seqs == list(range(1, 11)), f"seq 必须连续且唯一，实际 {seqs}"


async def test_replay_after_seq_returns_only_new_events(run_id: str) -> None:
    bus = EventBus(get_session_factory())
    for i in range(3):
        await bus.emit(run_id, "node.start", {"node": f"n{i}"})
    tail = await bus.replay(run_id, after_seq=1)
    assert [e.seq for e in tail] == [2, 3]
    assert all("seq" in e.payload for e in tail)  # payload 内也带 seq（§8.2）


async def test_emit_unknown_type_rejected(run_id: str) -> None:
    bus = EventBus(get_session_factory())
    with pytest.raises(ValueError):
        await bus.emit(run_id, "not-an-event", {})
