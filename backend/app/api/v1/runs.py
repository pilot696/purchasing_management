"""运行与 SSE 事件流接口（Spec §8.2/§8.3）。"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import BusDep, RoleDep, RunServiceDep, require_module_role
from app.core.config import get_settings
from app.schemas.runs import (
    ResumeRequest,
    ReviewOut,
    RunSnapshot,
    StartRunRequest,
    StartRunResponse,
)
from app.services.runs import TERMINAL_STATUS

router = APIRouter(tags=["runs"])


@router.post("/runs", response_model=StartRunResponse, status_code=202)
async def start_run(body: StartRunRequest, service: RunServiceDep, role: RoleDep) -> StartRunResponse:
    # P0：演示阶段任何角色都可发起；复核动作才做角色校验（§8.1）
    result = await service.start(body.module, body.subject)
    return StartRunResponse(**result)


@router.get("/runs/{run_id}", response_model=RunSnapshot)
async def get_run(run_id: UUID, service: RunServiceDep) -> RunSnapshot:
    snap = await service.snapshot(run_id)
    return RunSnapshot(**snap)


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: UUID,
    request: Request,
    bus: BusDep,
    service: RunServiceDep,
    after_seq: int = Query(default=0, ge=0, description="断线续传：只回放该序号之后的事件"),
) -> StreamingResponse:
    await service.run_status(run_id)  # 不存在直接 404
    settings = get_settings()

    async def gen():
        # 原生 EventSource 的 retry 语义：断线 3s 后重连（真实客户端用 fetch + eventsource-parser）
        yield "retry: 3000\n\n"
        seq = after_seq
        last_heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            try:
                events = await bus.replay(run_id, seq)
            except Exception:  # noqa: BLE001 - 事件回放失败不能静默
                yield 'event: error\ndata: {"code":"SERVICE_UNAVAILABLE","message":"事件回放失败","retryable":true}\n\n'
                return
            for event in events:
                seq = event.seq
                yield event.to_sse()

            status = await service.run_status(run_id)
            if status in TERMINAL_STATUS and not events:
                return

            if not events:
                waited = time.monotonic() - last_heartbeat
                if waited >= settings.sse_heartbeat_s:
                    last_heartbeat = time.monotonic()
                    yield f'event: heartbeat\ndata: {{"ts":{int(time.time())}}}\n\n'
                await asyncio.sleep(settings.sse_poll_interval_ms / 1000)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉反向代理缓冲，否则 SSE 会被攒成一坨
        },
    )


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: UUID,
    body: ResumeRequest,
    service: RunServiceDep,
    role: RoleDep,
) -> dict[str, Any]:
    snap = await service.snapshot(run_id)  # 404 + 取 module 做角色校验
    require_module_role(role, snap["run"].module)
    await service.resume(
        run_id,
        review_id=body.review_id,
        decision=body.decision,
        edits=body.edits,
        note=body.note,
    )
    return {"run_id": str(run_id), "status": "running"}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: UUID, service: RunServiceDep) -> dict[str, str]:
    await service.cancel(run_id)
    return {"run_id": str(run_id), "status": "cancelled"}


@router.get("/reviews", response_model=list[ReviewOut])
async def list_reviews(service: RunServiceDep) -> list[ReviewOut]:
    rows = await service.list_pending_reviews()
    return [ReviewOut.from_row(r) for r in rows]


@router.post("/reviews/{review_id}/decision")
async def decide_review(
    review_id: UUID,
    body: ResumeRequest,
    service: RunServiceDep,
    role: RoleDep,
) -> dict[str, Any]:
    run_id, module = await service.review_context(review_id)
    require_module_role(role, module)
    await service.resume(
        run_id,
        review_id=review_id,
        decision=body.decision,
        edits=body.edits,
        note=body.note,
    )
    return {"run_id": str(run_id), "status": "running"}
