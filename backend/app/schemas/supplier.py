"""M2 供应商评估输出 Schema（Spec §6.9）与 LLM 复核者 Schema（§6.7）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Dimension = Literal["price", "delivery", "quality", "service", "compliance"]
Verdict = Literal["approve", "conditional", "reject", "frozen"]


class DimensionScoreModel(BaseModel):
    dimension: Dimension
    score: float
    weight: float
    raw_metrics: dict[str, float | None] = Field(default_factory=dict)
    sample_size: int = 0
    note: str | None = None


class GateHitModel(BaseModel):
    code: str
    message: str
    evidence_refs: list[str] = Field(default_factory=list)
    veto: bool = True


class RiskItem(BaseModel):
    dimension: Dimension
    claim: str = Field(max_length=80)
    evidence_refs: list[str]
    severity_hint: Literal["high", "medium", "low"]


class LlmRiskReview(BaseModel):
    risk_summary: str
    top_risks: list[RiskItem] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    insufficient_evidence: list[str] = Field(default_factory=list)
    reviewer_memo: str


class SupplierEvalResult(BaseModel):
    supplier_id: str
    supplier_code: str | None = None
    supplier_name: str | None = None
    mode: Literal["onboard", "review"]
    category_code: str | None = None
    dimensions: list[DimensionScoreModel] = Field(default_factory=list)
    total: float = 0.0
    verdict: Verdict = "reject"
    gate_hits: list[GateHitModel] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    confidence_detail: dict[str, Any] = Field(default_factory=dict)
    conditions: list[str] = Field(default_factory=list)
    llm_review: LlmRiskReview | None = None
    llm_status: str = "unavailable"
    sensitivity: dict = Field(default_factory=dict)
    weight_version: str = "v1"
    evidence_pack_ids: list[str] = Field(default_factory=list)
    cold_start: bool = False
    generated_at: datetime | None = None


