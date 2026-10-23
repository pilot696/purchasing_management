"""FastAPI 装配（Spec §2.2）：REST + SSE + 健康检查 + 静态前端托管。"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.agents.matching import MatchingDeps, build_matching_graph
from app.agents.precheck import PrecheckDeps, build_precheck_graph
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler
from app.core.loop import ensure_windows_selector_loop
from app.db.session import db_ping, dispose_engine, get_session_factory
from app.llm.ledger import LedgerRecorder
from app.llm.router import get_router
from app.observability.runevents import EventBus
from app.services.runs import RunService

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = BACKEND_ROOT.parent / "frontend" / "dist"

# 兜底：若进程在 import 阶段尚未进入事件循环（脚本/测试），提前切换 Windows 事件循环策略
ensure_windows_selector_loop()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    session_factory = get_session_factory()
    app.state.settings = settings
    app.state.bus = EventBus(session_factory)
    # M3 图：load → normalize → align → reconcile → compose →（HITL）→ finalize
    app.state.matching_graph = build_matching_graph(
        MatchingDeps(bus=app.state.bus, session_factory=session_factory)
    )
    # M1 图：load → retrieve → rules → semantic → compose →（HITL）→ finalize
    app.state.precheck_graph = build_precheck_graph(
        PrecheckDeps(bus=app.state.bus, session_factory=session_factory)
    )
    app.state.runs = RunService(
        app.state.bus,
        session_factory,
        step_delay_ms=settings.demo_step_delay_ms,
        matching_graph=app.state.matching_graph,
        precheck_graph=app.state.precheck_graph,
    )
    # 网关记账：每次 LLM 调用写 llm_call（P0 用 FakeLLM 也会留痕）
    get_router().recorder = LedgerRecorder(session_factory)
    try:
        yield
    finally:
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Procurement AI Agent Workbench",
        version="0.1.0-p0",
        description="间接采购智能管理与风控（PR 预检 / 供应商评估 / 三单匹配）",
        lifespan=lifespan,
    )
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        """DB 不可用/语句失败统一转 503，避免把驱动异常细节泄漏给前端（§4.8）。"""
        request_id = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": "数据库不可用或执行失败（检查 docker compose 是否已启动）",
                    "details": {"type": type(exc).__name__},
                },
                "request_id": request_id,
            },
        )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, Any]:
        """存活探测：DB 不可用也返回 200，但如实标注 db=false（前端显示降级横幅）。"""
        settings = get_settings()
        db_ok = await db_ping()
        return {
            "status": "ok" if db_ok else "degraded",
            "db": db_ok,
            "env": settings.app_env,
            "version": app.version,
        }

    @app.get("/readyz", tags=["meta"])
    async def readyz() -> JSONResponse:
        """就绪探测：DB 不可用返回 503（容器编排用）。"""
        db_ok = await db_ping()
        code = 200 if db_ok else 503
        return JSONResponse(status_code=code, content={"ready": db_ok, "db": db_ok})

    app.include_router(api_router)

    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

    return app


app = create_app()
