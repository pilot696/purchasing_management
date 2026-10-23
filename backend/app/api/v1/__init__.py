"""v1 路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import documents, meta, runs

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(runs.router)
api_router.include_router(documents.router)
api_router.include_router(meta.router)
