"""全部业务表 ORM 定义 — 对应 docs/PROJECT_SPEC.md §3（v1.1）。

约定：
- 金额 `Numeric(18,4)`、税率 `Numeric(5,4)`、时间 `timestamptz`；
- `requires_gr`（§3.2）、`audit_log`（§3.7）、`supplier_eval`（§3.7）为 v1.1 新增；
- `agent_run.last_seq` 为实现 `run_event.seq` 并发安全分配新增的列（ADR-003）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JSONV = JSON().with_variant(JSONB(), "postgresql")
EMBED_DIM = 1024


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --------------------------------------------------------------------------- 主数据


class Department(Base):
    __tablename__ = "department"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_center: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    company_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created_at()


class Budget(Base):
    __tablename__ = "budget"
    __table_args__ = (UniqueConstraint("department_id", "fiscal_year", "category_code"),)
    id: Mapped[uuid.UUID] = _pk()
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("department.id"), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_total: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    amount_committed: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    amount_used: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # requester|buyer|finance|admin
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("department.id"))
    created_at: Mapped[datetime] = _created_at()


class CategoryTemplate(Base):
    __tablename__ = "category_template"
    id: Mapped[uuid.UUID] = _pk()
    category_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    required_slots: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    unit_policy: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    budget_subject: Mapped[str | None] = mapped_column(String(64))
    acceptance_points: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    # v1.1：实物类=true（发票上限取 min(ordered, received)），SaaS/服务=false（直接按 ordered）
    requires_gr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Rule(Base):
    __tablename__ = "rule"
    __table_args__ = (UniqueConstraint("category_template_id", "code", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    category_template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("category_template.id"))
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    field_path: Mapped[str | None] = mapped_column(String(128))
    expression: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    message_template: Mapped[str] = mapped_column(Text, nullable=False)
    policy_clause_ref: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class TolerancePolicy(Base):
    __tablename__ = "tolerance_policy"
    id: Mapped[uuid.UUID] = _pk()
    category_code: Mapped[str | None] = mapped_column(String(32))
    diff_type: Mapped[str] = mapped_column(String(24), nullable=False)
    abs_tol: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    rel_tol: Mapped[Decimal] = mapped_column(Numeric(8, 6), default=0)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Supplier(Base):
    __tablename__ = "supplier"
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    unified_social_credit_code: Mapped[str | None] = mapped_column(String(32))
    category_scope: Mapped[list[str]] = mapped_column(JSONV, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # active|pending|frozen|blacklisted
    risk_flags: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplierCert(Base):
    __tablename__ = "supplier_cert"
    id: Mapped[uuid.UUID] = _pk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"), nullable=False)
    cert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    cert_no: Mapped[str | None] = mapped_column(String(64))
    issuer: Mapped[str | None] = mapped_column(String(128))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doc.id"))


class InventoryItem(Base):
    __tablename__ = "inventory_item"
    id: Mapped[uuid.UUID] = _pk()
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    spec_bucket: Mapped[str] = mapped_column(String(96), nullable=False)
    sku_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sku_catalog.id"))
    qty_on_hand: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    qty_reserved: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    warehouse: Mapped[str | None] = mapped_column(String(64))


class ServiceTicket(Base):
    __tablename__ = "service_ticket"
    id: Mapped[uuid.UUID] = _pk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"), nullable=False)
    po_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("po.id"))
    severity: Mapped[str] = mapped_column(String(8), default="medium")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    reopened: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)


class SupplierWeight(Base):
    __tablename__ = "supplier_weight"
    __table_args__ = (UniqueConstraint("category_code", "dimension", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    version: Mapped[str] = mapped_column(String(16), default="v1")
    effective_from: Mapped[date | None] = mapped_column(Date)


# --------------------------------------------------------------------------- 检索语料


class SkuCatalog(Base):
    __tablename__ = "sku_catalog"
    __table_args__ = (Index("ix_sku_catalog_category_bucket", "category_code", "spec_bucket"),)
    id: Mapped[uuid.UUID] = _pk()
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    sku_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(64))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    spec_bucket: Mapped[str] = mapped_column(String(96), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), default="台")
    ref_price_p50: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    aliases: Mapped[list[str]] = mapped_column(JSONV, default=list)
    lex_tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))


class PoLineHistory(Base):
    __tablename__ = "po_line_history"
    __table_args__ = (
        Index("ix_po_line_history_cat_bucket_date", "category_code", "spec_bucket", "order_date"),
    )
    id: Mapped[uuid.UUID] = _pk()
    po_no: Mapped[str] = mapped_column(String(32), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name_raw: Mapped[str] = mapped_column(String(200), nullable=False)
    spec_text: Mapped[str | None] = mapped_column(String(200))
    spec_bucket: Mapped[str] = mapped_column(String(96), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"), nullable=False)
    unit_price_tax_excl: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=0.13)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    lex_tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))


class PolicyChunk(Base):
    __tablename__ = "policy_chunk"
    __table_args__ = (Index("ix_policy_chunk_category", "category_code"),)
    id: Mapped[uuid.UUID] = _pk()
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    clause_no: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_doc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doc.id"))
    lex_tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))


# --------------------------------------------------------------------------- 业务单据


class Pr(Base):
    __tablename__ = "pr"
    id: Mapped[uuid.UUID] = _pk()
    pr_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("department.id"), nullable=False)
    category_code: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    needed_by: Mapped[date | None] = mapped_column(Date)
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    status: Mapped[str] = mapped_column(String(16), default="submitted")
    created_at: Mapped[datetime] = _created_at()


class PrItem(Base):
    __tablename__ = "pr_item"
    __table_args__ = (UniqueConstraint("pr_id", "line_no"),)
    id: Mapped[uuid.UUID] = _pk()
    pr_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pr.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name_raw: Mapped[str] = mapped_column(String(200), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), default="台")
    unit_price_est: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    amount_est: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    sku_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sku_catalog.id"))
    spec_bucket: Mapped[str | None] = mapped_column(String(96))


class Quote(Base):
    __tablename__ = "quote"
    id: Mapped[uuid.UUID] = _pk()
    pr_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pr.id"), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"), nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    payment_terms: Mapped[str | None] = mapped_column(String(64))


class QuoteLine(Base):
    __tablename__ = "quote_line"
    __table_args__ = (UniqueConstraint("quote_id", "line_no"),)
    id: Mapped[uuid.UUID] = _pk()
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quote.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name_raw: Mapped[str] = mapped_column(String(200), nullable=False)
    spec_bucket: Mapped[str | None] = mapped_column(String(96))
    unit_price_tax_excl: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=0.13)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class Contract(Base):
    __tablename__ = "contract"
    id: Mapped[uuid.UUID] = _pk()
    contract_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(16), default="one_off")  # framework|one_off
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    payment_terms: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    delivery_terms: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doc.id"))


class ContractLine(Base):
    __tablename__ = "contract_line"
    __table_args__ = (UniqueConstraint("contract_id", "line_no"),)
    id: Mapped[uuid.UUID] = _pk()
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contract.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_desc: Mapped[str] = mapped_column(String(200), nullable=False)
    spec_bucket: Mapped[str | None] = mapped_column(String(96))
    unit_price_tax_excl: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount_tax_excl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=0.13)


class Po(Base):
    __tablename__ = "po"
    id: Mapped[uuid.UUID] = _pk()
    po_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contract.id"))
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("department.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    total_amount_tax_incl: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|partial|received|closed
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PoLine(Base):
    __tablename__ = "po_line"
    __table_args__ = (UniqueConstraint("po_id", "line_no"),)
    id: Mapped[uuid.UUID] = _pk()
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("po.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    sku_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sku_catalog.id"))
    item_desc: Mapped[str] = mapped_column(String(200), nullable=False)
    spec_bucket: Mapped[str | None] = mapped_column(String(96))
    qty_ordered: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    qty_received: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    qty_invoiced: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    unit_price_tax_excl: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=0.13)
    amount_tax_excl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    amount_tax_incl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    promised_date: Mapped[date | None] = mapped_column(Date)


class GoodsReceipt(Base):
    __tablename__ = "goods_receipt"
    id: Mapped[uuid.UUID] = _pk()
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("po.id"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doc.id"))


class ReceiptLine(Base):
    __tablename__ = "receipt_line"
    id: Mapped[uuid.UUID] = _pk()
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("goods_receipt.id"), nullable=False)
    po_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("po_line.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    qc_result: Mapped[str] = mapped_column(String(16), default="pass")  # pass|fail|partial
    defect_note: Mapped[str | None] = mapped_column(Text)


class Invoice(Base):
    __tablename__ = "invoice"
    __table_args__ = (UniqueConstraint("invoice_no", "invoice_code"),)
    id: Mapped[uuid.UUID] = _pk()
    invoice_no: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_code: Mapped[str | None] = mapped_column(String(16))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("supplier.id"))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    amount_tax_excl: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    amount_tax_incl: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doc.id"))
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|matched|hold|paid


class InvoiceLine(Base):
    __tablename__ = "invoice_line"
    __table_args__ = (UniqueConstraint("invoice_id", "line_no"),)
    id: Mapped[uuid.UUID] = _pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoice.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_desc: Mapped[str] = mapped_column(String(200), nullable=False)
    spec_bucket: Mapped[str | None] = mapped_column(String(96))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_price_tax_excl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    amount_tax_excl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    amount_tax_incl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))


# --------------------------------------------------------------------------- 文档与抽取


class Doc(Base):
    __tablename__ = "doc"
    id: Mapped[uuid.UUID] = _pk()
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    mime: Mapped[str] = mapped_column(String(64), default="application/pdf")
    page_count: Mapped[int | None] = mapped_column(Integer)
    has_text_layer: Mapped[bool | None] = mapped_column(Boolean)
    text_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    template_hit: Mapped[str | None] = mapped_column(String(64))
    extractor_used: Mapped[str | None] = mapped_column(String(32))
    linked_kind: Mapped[str | None] = mapped_column(String(32))
    linked_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _created_at()


class DocAnnotation(Base):
    __tablename__ = "doc_annotation"
    __table_args__ = (Index("ix_doc_annotation_doc_line", "doc_id", "line_ref"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doc.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    line_ref: Mapped[str | None] = mapped_column(String(64))
    page_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    x0: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    y0: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    x1: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    y1: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    text_raw: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = _created_at()


class ExtractionRecord(Base):
    __tablename__ = "extraction_record"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doc.id"), nullable=False)
    extractor: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSONV)
    validated_json: Mapped[dict[str, Any] | None] = mapped_column(JSONV)
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|needs_review|failed
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONV)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------- 匹配与核销


class MatchRun(Base):
    __tablename__ = "match_run"
    id: Mapped[uuid.UUID] = _pk()
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("po.id"), nullable=False)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contract.id"))
    invoice_ids: Mapped[list[str]] = mapped_column(JSONV, default=list)
    status: Mapped[str] = mapped_column(String(16), default="running")
    overall: Mapped[str | None] = mapped_column(String(24))
    tolerance_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    tail_diff: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    created_at: Mapped[datetime] = _created_at()


class MatchLine(Base):
    __tablename__ = "match_line"
    __table_args__ = (UniqueConstraint("run_id", "po_line_id", "invoice_line_id"),)
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_run.id"), nullable=False)
    contract_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contract_line.id"))
    po_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("po_line.id"), nullable=False)
    invoice_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoice_line.id"))
    method: Mapped[str] = mapped_column(String(24), default="unmatched")
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=0)
    consumed_qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    remaining_qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(16), default="unmatched")
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)


class MatchDiff(Base):
    __tablename__ = "match_diff"
    __table_args__ = (Index("ix_match_diff_run_type", "run_id", "diff_type"),)
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_run.id"), nullable=False)
    diff_type: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("po_line.id"))
    invoice_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoice_line.id"))
    expected: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    actual: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    delta: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    rule_ref: Mapped[str] = mapped_column(String(32), nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TailDiffPool(Base):
    __tablename__ = "tail_diff_pool"
    id: Mapped[uuid.UUID] = _pk()
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("po.id"), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --------------------------------------------------------------------------- Agent 运行与可观测


class AgentRun(Base):
    __tablename__ = "agent_run"
    id: Mapped[uuid.UUID] = _pk()
    module: Mapped[str] = mapped_column(String(8), nullable=False)  # m1|m2|m3
    subject_ref: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    graph_version: Mapped[str | None] = mapped_column(String(32))
    # ADR-003：并发安全的 run_event.seq 分配（UPDATE ... RETURNING）
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_cny: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONV)


class RunEvent(Base):
    __tablename__ = "run_event"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_run.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    created_at: Mapped[datetime] = _created_at()


class LlmCall(Base):
    __tablename__ = "llm_call"
    __table_args__ = (Index("ix_llm_call_run_step", "run_id", "step"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_run.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_cny: Mapped[Decimal] = mapped_column(Numeric(10, 5), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    retry_no: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    schema_repair: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created_at()


class ReviewTask(Base):
    __tablename__ = "review_task"
    __table_args__ = (Index("ix_review_task_status", "status", "created_at"),)
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # apply_patch|submit_verdict|release_or_hold
    payload: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Artifact(Base):
    __tablename__ = "artifact"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False)
    module: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    created_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_run_created", "run_id", "created_at"),
        Index("ix_audit_log_target", "target_table", "target_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_run.id"))
    review_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_task.id"))
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)
    target_table: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    patch_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONV)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONV)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONV)
    source: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class SupplierEval(Base):
    __tablename__ = "supplier_eval"
    __table_args__ = (Index("ix_supplier_eval_supplier_created", "supplier_id", "created_at"),)
    id: Mapped[uuid.UUID] = _pk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier.id"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_run.id"), nullable=False)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifact.id"))
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    total: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    gate_hits: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONV)
    dimensions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONV)
    weight_version: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created_at()


# --------------------------------------------------------------------------- 评测


class EvalRun(Base):
    __tablename__ = "eval_run"
    id: Mapped[uuid.UUID] = _pk()
    suite: Mapped[str] = mapped_column(String(32), nullable=False)
    git_sha: Mapped[str | None] = mapped_column(String(64))
    model_config: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    seed: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)


class EvalCaseResult(Base):
    __tablename__ = "eval_case_result"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_run.id"), nullable=False)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONV, default=dict)
    output_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifact.id"))
    error: Mapped[str | None] = mapped_column(Text)
