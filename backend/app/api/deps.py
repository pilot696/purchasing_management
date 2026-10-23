"""FastAPI 依赖：事件总线 / 运行服务 / 演示角色（Spec §8.1：X-Demo-Role 非真实认证）。

注意：应用状态采用**懒初始化**，这样既兼容 uvicorn 的 lifespan，也兼容
`httpx.ASGITransport`（它默认不执行 lifespan），单测无需额外启动管理。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, Request

from app.core.config import get_settings
from app.core.errors import RoleForbidden
from app.db.session import get_session_factory
from app.observability.runevents import EventBus
from app.services.runs import RunService

VALID_ROLES = frozenset({"requester", "buyer", "finance", "admin"})

# 模块 -> 允许执行复核决策的角色（admin 永远可）
MODULE_REVIEW_ROLES: dict[str, frozenset[str]] = {
    "m1": frozenset({"buyer"}),
    "m2": frozenset({"buyer"}),
    "m3": frozenset({"finance"}),
}


def ensure_app_state(app: Any) -> Any:
    """幂等装配 app.state.bus / app.state.runs（lifespan 未执行时的兜底）。"""
    if getattr(app.state, "bus", None) is None:
        session_factory = get_session_factory()
        app.state.bus = EventBus(session_factory)
        app.state.runs = RunService(
            app.state.bus,
            session_factory,
            step_delay_ms=get_settings().demo_step_delay_ms,
        )
    return app.state


def get_event_bus(request: Request) -> EventBus:
    return ensure_app_state(request.app).bus


def get_run_service(request: Request) -> RunService:
    return ensure_app_state(request.app).runs


async def get_demo_role(x_demo_role: Annotated[str | None, Header(alias="X-Demo-Role")] = None) -> str:
    role = (x_demo_role or "buyer").strip().lower()
    if role not in VALID_ROLES:
        raise RoleForbidden(f"未知角色: {role}", details={"allowed": sorted(VALID_ROLES)})
    return role


def require_module_role(role: str, module: str) -> None:
    if role == "admin":
        return
    allowed = MODULE_REVIEW_ROLES.get(module, frozenset())
    if role not in allowed:
        raise RoleForbidden(
            f"角色 {role} 无权复核 {module} 的运行",
            details={"module": module, "allowed": sorted(allowed)},
        )


BusDep = Annotated[EventBus, Depends(get_event_bus)]
RunServiceDep = Annotated[RunService, Depends(get_run_service)]
RoleDep = Annotated[str, Depends(get_demo_role)]
