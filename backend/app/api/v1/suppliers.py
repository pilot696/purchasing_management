"""供应商目录与评估历史（W4 演示闭环 / M2 看板）。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import RunServiceDep
from app.schemas.catalog import SupplierDetail, SupplierListResponse

router = APIRouter(tags=["suppliers"])


@router.get("/suppliers", response_model=SupplierListResponse)
async def list_suppliers(
    service: RunServiceDep,
    q: str | None = Query(default=None, description="按编码/名称模糊搜索"),
    status: str | None = Query(default=None, description="active|pending|frozen|blacklisted"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SupplierListResponse:
    return SupplierListResponse(**await service.list_suppliers(q=q, status=status, limit=limit, offset=offset))


@router.get("/suppliers/{code}", response_model=SupplierDetail)
async def get_supplier(code: str, service: RunServiceDep) -> SupplierDetail:
    return SupplierDetail(**await service.supplier_detail(code))
