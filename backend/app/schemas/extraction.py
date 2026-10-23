"""抽取相关 Pydantic 模型（Spec §7.3）。字段与 `extraction_record.validated_json` 同构。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

DocType = Literal["invoice", "po", "contract"]
RouteKind = Literal["pdf_native", "hybrid", "scanned", "unsupported"]


class DocLine(BaseModel):
    line_no: int | None = None
    item_desc: str
    spec_text: str | None = None
    spec_bucket: str | None = None
    qty: Decimal
    unit: str | None = None
    unit_price_tax_excl: Decimal | None = None
    amount_tax_excl: Decimal | None = None
    tax_rate: Decimal | None = None
    amount_tax_incl: Decimal | None = None
    page_no: int | None = None
    bbox: list[float] | None = None  # [x0,y0,x1,y1] 归一化


class PaymentTerms(BaseModel):
    days: int | None = None
    mode: str | None = None
    milestones: list[str] = Field(default_factory=list)


class DeliveryTerms(BaseModel):
    lead_time_days: int | None = None
    penalty_clause: str | None = None


class InvoiceExtract(BaseModel):
    invoice_no: str
    invoice_code: str | None = None
    invoice_date: date | None = None
    seller_name: str | None = None
    seller_tax_id: str | None = None
    buyer_name: str | None = None
    amount_tax_excl: Decimal | None = None
    tax_amount: Decimal | None = None
    amount_tax_incl: Decimal | None = None
    tax_rates: list[Decimal] = Field(default_factory=list)
    lines: list[DocLine] = Field(default_factory=list)
    remark: str | None = None
    po_no: str | None = None


class PoExtract(BaseModel):
    po_no: str
    supplier_name: str | None = None
    po_date: date | None = None
    currency: str = "CNY"
    total_amount_tax_incl: Decimal | None = None
    lines: list[DocLine] = Field(default_factory=list)


class ContractExtract(BaseModel):
    contract_no: str
    contract_type: Literal["framework", "one_off"] = "one_off"
    supplier_name: str | None = None
    buyer_name: str | None = None
    total_amount: Decimal | None = None
    currency: str = "CNY"
    effective_from: date | None = None
    effective_to: date | None = None
    payment_terms: PaymentTerms | None = None
    delivery_terms: DeliveryTerms | None = None
    lines: list[DocLine] = Field(default_factory=list)
    po_no: str | None = None


class DocRoute(BaseModel):
    kind: RouteKind
    template: str | None = None
    confidence: float = 0.0
    text_ratio: float = 0.0
    has_text_layer: bool = False
    page_count: int = 1


class Annotation(BaseModel):
    """PDF 抽屉高亮用：字段 → 归一化 bbox。"""

    field_name: str
    page_no: int = 1
    x0: float
    y0: float
    x1: float
    y1: float
    text_raw: str | None = None


class ValidationIssue(BaseModel):
    code: str
    message: str
    field: str | None = None
    severity: Literal["high", "medium", "low"] = "medium"


class ExtractionResult(BaseModel):
    doc_type: DocType
    route: DocRoute
    extract: dict[str, Any] | None = None
    extractor: str = "pdf_native"
    repair_attempts: int = 0
    structured_errors: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    needs_review_fields: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.extract is not None and not any(i.severity == "high" for i in self.issues)
