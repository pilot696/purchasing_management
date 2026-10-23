"""运行与复核的请求/响应模型（Spec §8）。P0 只覆盖 runs / reviews。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Module = Literal["m1", "m2", "m3"]
Decision = Literal["accept", "reject", "edit"]
DemoRole = Literal["requester", "buyer", "finance", "admin"]


class StartRunRequest(BaseModel):
    module: Module
    subject: dict[str, Any] = Field(default_factory=dict, examples=[{"pr_id": "PR-2026-0142"}])
    options: dict[str, Any] = Field(default_factory=dict)


class StartRunResponse(BaseModel):
    run_id: str
    thread_id: str
    status: str
    sse_url: str


class ResumeRequest(BaseModel):
    review_id: UUID
    decision: Decision
    # None = 未提供编辑（按推荐全量应用）；[] = 明确不采纳任何补丁；[patch...] = 逐条采纳/改值
    edits: list[dict[str, Any]] | None = None
    note: str | None = None


class ReviewOut(BaseModel):
    id: UUID
    run_id: UUID
    kind: str
    status: str
    payload: dict[str, Any]
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "ReviewOut":
        return cls(
            id=row.id,
            run_id=row.run_id,
            kind=row.kind,
            status=row.status,
            payload=row.payload or {},
            created_at=row.created_at,
        )


class RunOut(BaseModel):
    id: UUID
    module: str
    status: str
    subject_ref: dict[str, Any]
    last_seq: int
    tokens_in: int
    tokens_out: int
    cost_cny: float
    error: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Any) -> "RunOut":
        return cls(
            id=row.id,
            module=row.module,
            status=row.status,
            subject_ref=row.subject_ref or {},
            last_seq=row.last_seq,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            cost_cny=float(row.cost_cny or 0),
            error=row.error,
        )


class RunSnapshot(BaseModel):
    run: RunOut
    review: ReviewOut | None = None
    artifact: dict[str, Any] | None = None


class RunListItem(BaseModel):
    id: UUID
    module: str
    status: str
    subject_ref: dict[str, Any] = Field(default_factory=dict)
    last_seq: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error: dict[str, Any] | None = None
    pending_review_id: UUID | None = None
    pending_review_kind: str | None = None


class RunListResponse(BaseModel):
    items: list[RunListItem]
    total: int
    limit: int
    offset: int


class ReviewInboxItem(BaseModel):
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


class TraceEventOut(BaseModel):
    seq: int
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class LlmCallOut(BaseModel):
    id: int
    step: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_cny: float
    latency_ms: int
    retry_no: int
    cache_hit: bool
    schema_repair: int
    ok: bool
    error_code: str | None = None
    created_at: datetime | None = None


class RunTraceResponse(BaseModel):
    run: RunListItem
    events: list[TraceEventOut]
    llm_calls: list[LlmCallOut]
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
