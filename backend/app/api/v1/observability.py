"""可观测与评测聚合（Spec §9.4 页面 6）。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from app.api.deps import RunServiceDep
from app.schemas.catalog import EvalSnapshotResponse, ObservabilitySummary

router = APIRouter(tags=["observability"])

PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVALS_PATH = PROJECT_ROOT / "evals" / "latest.json"


@router.get("/observability/summary", response_model=ObservabilitySummary)
async def get_summary(service: RunServiceDep) -> ObservabilitySummary:
    return ObservabilitySummary(**await service.observability_summary())


@router.get("/metrics/overview", response_model=ObservabilitySummary)
async def get_metrics_overview(service: RunServiceDep) -> ObservabilitySummary:
    """Spec §8.3 的 /metrics/overview 兼容别名（与 /observability/summary 同源）。"""
    return ObservabilitySummary(**await service.observability_summary())


@router.get("/observability/evals", response_model=EvalSnapshotResponse)
async def get_evals() -> EvalSnapshotResponse:
    if not EVALS_PATH.exists():
        return EvalSnapshotResponse(available=False)
    try:
        data = json.loads(EVALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return EvalSnapshotResponse(available=False)
    return EvalSnapshotResponse(
        available=True,
        generated_at=data.get("generated_at"),
        llm_mode=data.get("llm_mode"),
        seed=data.get("seed"),
        data=data,
    )

