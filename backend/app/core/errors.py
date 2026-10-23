"""统一错误模型：所有业务异常都映射到 §4.8 的错误码表，响应体形如
{"error": {"code": ..., "message": ..., "details": {...}}, "request_id": "..."}.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """业务异常基类。code 与 docs/PROJECT_SPEC.md §4.8 的错误码一一对应。"""

    status_code: int = 400
    code: str = "APP_ERROR"
    default_message: str = "请求处理失败"

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.details = details or {}


class RunNotFound(AppError):
    status_code = 404
    code = "RUN_NOT_FOUND"
    default_message = "运行不存在"


class RunStateConflict(AppError):
    status_code = 409
    code = "RUN_STATE_CONFLICT"
    default_message = "运行状态不允许该操作"


class ReviewAlreadyDecided(AppError):
    status_code = 409
    code = "REVIEW_ALREADY_DECIDED"
    default_message = "该复核任务已处理"


class RoleForbidden(AppError):
    status_code = 403
    code = "ROLE_FORBIDDEN"
    default_message = "当前角色无权执行该操作"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    default_message = "依赖服务不可用"


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            "request_id": request_id,
        },
    )
