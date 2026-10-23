"""规则库 / 可观测聚合的响应模型（W4 演示闭环）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CategoryOut(BaseModel):
    code: str
    name: str
    version: int = 1
    requires_gr: bool = True
    required_slots: list[str] = Field(default_factory=list)


class RuleOut(BaseModel):
    id: UUID
    category_code: str
    category_name: str
    code: str
    type: str
    severity: str
    field_path: str | None = None
    expression: dict[str, Any] = Field(default_factory=dict)
    message_template: str
    policy_clause_ref: str | None = None
    enabled: bool = True
    version: int = 1


class ToleranceOut(BaseModel):
    id: UUID
    category_code: str | None = None
    diff_type: str
    abs_tol: float
    rel_tol: float
    action: str
    priority: int
    enabled: bool = True


class RuleCatalogResponse(BaseModel):
    categories: list[CategoryOut]
    rules: list[RuleOut]
    tolerances: list[ToleranceOut]


class ObservabilitySummary(BaseModel):
    runs_total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_module: dict[str, int] = Field(default_factory=dict)
    success_rate: float = 0.0
    pending_reviews: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    llm_calls: int = 0
    avg_llm_latency_ms: float | None = None
    cache_hit_rate: float | None = None


class EvalSnapshotResponse(BaseModel):
    available: bool
    generated_at: str | None = None
    llm_mode: str | None = None
    seed: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)

# --------------------------------------------------------------------------- 供应商目录 / 复核详情


class SupplierEvalOut(BaseModel):
    id: UUID
    run_id: UUID
    mode: str
    total: float | None = None
    verdict: str
    confidence: str
    gate_hits: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    weight_version: str | None = None
    created_at: datetime | None = None


class SupplierListItem(BaseModel):
    code: str
    name: str
    status: str
    category_scope: list[str] = Field(default_factory=list)
    onboarded_at: datetime | None = None
    risk_flags: dict[str, Any] = Field(default_factory=dict)
    latest_eval: SupplierEvalOut | None = None
    eval_count: int = 0


class SupplierListResponse(BaseModel):
    items: list[SupplierListItem]
    total: int
    limit: int
    offset: int


class SupplierDetail(SupplierListItem):
    evals: list[SupplierEvalOut] = Field(default_factory=list)


class ReviewDetail(BaseModel):
    id: UUID
    run_id: UUID
    kind: str
    status: str
    created_at: datetime | None = None
    run_module: str
    run_status: str
    subject_ref: dict[str, Any] = Field(default_factory=dict)
    verdict: str | None = None
    summary: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
