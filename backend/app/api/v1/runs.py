"""运行与 SSE 事件流接口（Spec §8.2/§8.3）。"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import BusDep, RoleDep, RunServiceDep, require_module_role
from app.core.config import get_settings
from app.core.idempotency import idempotency_store
from app.core.ratelimit import run_rate_limiter
from app.schemas.catalog import ReviewDetail
from app.schemas.runs import (
    ResumeRequest,
    ReviewInboxItem,
    RunListResponse,
    RunSnapshot,
    RunTraceResponse,
    StartRunRequest,
    StartRunResponse,
)
from app.services.runs import TERMINAL_STATUS

router = APIRouter(tags=["runs"])


@router.post("/runs", response_model=StartRunResponse, status_code=202)
async def start_run(
    body: StartRunRequest,
    service: RunServiceDep,
    role: RoleDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StartRunResponse:
    # P0：演示阶段任何角色都可发起；复核动作才做角色校验（§8.1）
    payload = body.model_dump(mode="json")
    if idempotency_key:
        cached = idempotency_store.lookup("runs:start", idempotency_key, payload)
        if cached is not None:
            return StartRunResponse(**cached)
    run_rate_limiter.check(f"runs:start:{role}", limit=get_settings().demo_rate_limit_runs)
    result = await service.start(body.module, body.subject)
    if idempotency_key:
        idempotency_store.remember("runs:start", idempotency_key, payload, result)
    return StartRunResponse(**result)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    service: RunServiceDep,
    module: str | None = Query(default=None, pattern="^(m1|m2|m3)$"),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> RunListResponse:
    data = await service.list_runs(limit=limit, offset=offset, module=module, status=status)
    return RunListResponse(**data)


@router.get("/runs/{run_id}", response_model=RunSnapshot)
async def get_run(run_id: UUID, service: RunServiceDep) -> RunSnapshot:
    snap = await service.snapshot(run_id)
    return RunSnapshot(**snap)


@router.get("/runs/{run_id}/trace", response_model=RunTraceResponse)
async def get_run_trace(run_id: UUID, service: RunServiceDep) -> RunTraceResponse:
    return RunTraceResponse(**await service.trace(run_id))


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
        terminal_grace_checks = 0
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
                # 竞态：run 状态先落库，尾部 node.end / run.end 事件随后才写入。
                # 直接 return 会丢 run.end（客户端等不到完成信号）→ 给两次 50ms 宽限再关闭。
                if terminal_grace_checks < 2:
                    terminal_grace_checks += 1
                    await asyncio.sleep(0.05)
                    continue
                return
            terminal_grace_checks = 0

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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    scope = f"runs:{run_id}:resume"
    payload = body.model_dump(mode="json")
    if idempotency_key:
        cached = idempotency_store.lookup(scope, idempotency_key, payload)
        if cached is not None:
            return cached
    snap = await service.snapshot(run_id)  # 404 + 取 module 做角色校验
    require_module_role(role, snap["run"].module)
    await service.resume(
        run_id,
        review_id=body.review_id,
        decision=body.decision,
        edits=body.edits,
        note=body.note,
    )
    response = {"run_id": str(run_id), "status": "running"}
    if idempotency_key:
        idempotency_store.remember(scope, idempotency_key, payload, response)
    return response


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: UUID, service: RunServiceDep) -> dict[str, str]:
    await service.cancel(run_id)
    return {"run_id": str(run_id), "status": "cancelled"}


@router.get("/reviews", response_model=list[ReviewInboxItem])
async def list_reviews(service: RunServiceDep) -> list[ReviewInboxItem]:
    return [ReviewInboxItem(**item) for item in await service.list_review_inbox()]


@router.get("/reviews/{review_id}", response_model=ReviewDetail)
async def get_review(review_id: UUID, service: RunServiceDep) -> ReviewDetail:
    return ReviewDetail(**await service.review_detail(review_id))


@router.post("/reviews/{review_id}/decision")
async def decide_review(
    review_id: UUID,
    body: ResumeRequest,
    service: RunServiceDep,
    role: RoleDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    scope = f"reviews:{review_id}:decision"
    payload = body.model_dump(mode="json")
    if idempotency_key:
        cached = idempotency_store.lookup(scope, idempotency_key, payload)
        if cached is not None:
            return cached
    run_id, module = await service.review_context(review_id)
    require_module_role(role, module)
    await service.resume(
        run_id,
        review_id=review_id,
        decision=body.decision,
        edits=body.edits,
        note=body.note,
    )
    response = {"run_id": str(run_id), "status": "running"}
    if idempotency_key:
        idempotency_store.remember(scope, idempotency_key, payload, response)
    return response
