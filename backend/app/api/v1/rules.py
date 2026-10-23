"""规则库与容差策略（只读，Spec §9.4 页面 5 / §5.2 规则表）。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import RunServiceDep
from app.schemas.catalog import RuleCatalogResponse

router = APIRouter(tags=["rules"])


@router.get("/rules", response_model=RuleCatalogResponse)
async def get_rules(
    service: RunServiceDep,
    category: str | None = Query(default=None, description="品类编码，如 IT_HW / SAAS"),
) -> RuleCatalogResponse:
    return RuleCatalogResponse(**await service.rule_catalog(category_code=category))
