"""run_event 事件总线（Spec §3.7 / §8.2）。

职责：
- 原子分配 `seq`（UPDATE agent_run.last_seq ... RETURNING，避免并发节点撞唯一键）；
- 落 `run_event` 并立即提交，保证 SSE 轮询端能马上读到；
- 提供 replay / latest_seq / SSE 序列化。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import RunNotFound
from app.models.tables import RunEvent

# Spec §8.2 事件清单（新增事件类型必须同步更新这里与前端 reducer）
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "run.start",
        "node.start",
        "node.end",
        "tool.call",
        "tool.result",
        "finding",
        "delta",
        "interrupt",
        "run.end",
        "error",
        "heartbeat",
    }
)


@dataclass(slots=True, frozen=True)
class Event:
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime | None = None

    def to_sse(self) -> str:
        data = json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))
        return f"id: {self.seq}\nevent: {self.type}\ndata: {data}\n\n"


def parse_event_row(row: RunEvent) -> Event:
    return Event(seq=row.seq, type=row.event_type, payload=row.payload or {}, created_at=row.created_at)


class EventBus:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def emit(self, run_id: UUID, event_type: str, payload: dict[str, Any] | None = None) -> Event:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {event_type}")
        async with self._sf() as session:
            seq: int | None = await session.scalar(
                text("UPDATE agent_run SET last_seq = last_seq + 1 WHERE id = :rid RETURNING last_seq"),
                {"rid": run_id},
            )
            if seq is None:
                raise RunNotFound(details={"run_id": str(run_id)})
            # Spec §8.2：payload 内也带 seq，前端可做幂等与乱序保护
            body = dict(payload or {})
            body["seq"] = seq
            session.add(RunEvent(run_id=run_id, seq=seq, event_type=event_type, payload=body))
            await session.commit()
        return Event(seq=seq, type=event_type, payload=body)

    async def replay(self, run_id: UUID, after_seq: int = 0, limit: int = 1000) -> list[Event]:
        async with self._sf() as session:
            rows = (
                await session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
                    .order_by(RunEvent.seq)
                    .limit(limit)
                )
            ).all()
        return [parse_event_row(r) for r in rows]

    async def latest_seq(self, run_id: UUID) -> int:
        async with self._sf() as session:
            value = await session.scalar(
                text("SELECT COALESCE(MAX(seq), 0) FROM run_event WHERE run_id = :rid"), {"rid": run_id}
            )
        return int(value or 0)
