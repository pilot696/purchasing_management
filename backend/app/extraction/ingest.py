"""上传文档抽取结果 → 业务建档（当前支持发票）。

为什么单独一层：
- 上传/抽取只负责"文档 + 结构化字段"，建档要处理主数据匹配、唯一约束、幂等；
- 端点层负责抽取/落盘/事件，这里只做纯 DB 映射，便于集成测试。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import tables as t
from app.schemas.extraction import InvoiceExtract


class InvoiceConflict(AppError):
    status_code = 409
    code = "INVOICE_CONFLICT"
    default_message = "发票已存在"


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _tax_rate(value: Any) -> Decimal | None:
    if value is None:
        return None
    number = _dec(value)
    if number > 1:
        number = number / Decimal("100")
    return number.quantize(Decimal("0.0001"))


async def ingest_invoice(
    session: AsyncSession,
    *,
    doc: t.Doc,
    payload: InvoiceExtract,
    invoice_no_override: str | None = None,
) -> dict[str, Any]:
    """把一张发票抽取结果写入 invoice / invoice_line，并把 doc 反向关联。

    幂等：
    - doc 已关联发票 → 直接返回已有发票（already_linked=True）；
    - 发票号已存在且未关联文档 → 复用该发票并补关联（created=False）；
    - 发票号已存在且已关联其他文档 → 409 INVOICE_CONFLICT。
    """
    if doc.linked_kind == "invoice" and doc.linked_id:
        existing_linked = await session.get(t.Invoice, doc.linked_id)
        if existing_linked is not None:
            return _invoice_result(existing_linked, created=False, already_linked=True, lines=0, warnings=[])

    invoice_no = (invoice_no_override or payload.invoice_no or "").strip()
    if not invoice_no:
        raise AppError("抽取结果缺少发票号", details={"doc_id": str(doc.id)})
    if len(invoice_no) > 32:
        raise AppError("发票号超过 32 字符", details={"invoice_no": invoice_no})

    warnings: list[str] = []
    if invoice_no_override and invoice_no_override.strip() != payload.invoice_no.strip():
        warnings.append(f"发票号使用人工指定值 {invoice_no}（抽取值为 {payload.invoice_no}）")

    existing = await session.scalar(select(t.Invoice).where(t.Invoice.invoice_no == invoice_no).limit(1))
    if existing is not None:
        if existing.doc_id is not None and existing.doc_id != doc.id:
            raise InvoiceConflict(
                "发票号已存在且已关联其他文档",
                details={"invoice_no": invoice_no, "invoice_id": str(existing.id)},
            )
        existing.doc_id = doc.id
        doc.linked_kind = "invoice"
        doc.linked_id = existing.id
        await session.commit()
        return _invoice_result(existing, created=False, already_linked=False, lines=0, warnings=warnings)

    supplier_id: UUID | None = None
    if payload.seller_name or payload.seller_tax_id:
        conditions = []
        if payload.seller_name:
            conditions.append(t.Supplier.name == payload.seller_name)
        if payload.seller_tax_id:
            conditions.append(t.Supplier.unified_social_credit_code == payload.seller_tax_id)
        supplier = await session.scalar(select(t.Supplier).where(or_(*conditions)).limit(1))
        if supplier is not None:
            supplier_id = supplier.id
        else:
            warnings.append(f"未匹配到供应商主数据：{payload.seller_name or payload.seller_tax_id}")

    lines = list(payload.lines or [])
    computed_excl = sum((_dec(line.amount_tax_excl) for line in lines), Decimal("0"))
    computed_incl = sum((_dec(line.amount_tax_incl) for line in lines), Decimal("0"))
    amount_excl = _dec(payload.amount_tax_excl) if payload.amount_tax_excl is not None else computed_excl
    amount_incl = _dec(payload.amount_tax_incl) if payload.amount_tax_incl is not None else computed_incl
    if amount_incl == 0 and amount_excl > 0:
        tax = _dec(payload.tax_amount)
        amount_incl = amount_excl + tax
    tax_amount = _dec(payload.tax_amount) if payload.tax_amount is not None else (amount_incl - amount_excl)
    if amount_incl <= 0:
        warnings.append("抽取金额为 0，请人工核对")

    invoice = t.Invoice(
        invoice_no=invoice_no,
        invoice_code=payload.invoice_code,
        supplier_id=supplier_id,
        invoice_date=payload.invoice_date,
        amount_tax_excl=amount_excl,
        tax_amount=tax_amount,
        amount_tax_incl=amount_incl,
        doc_id=doc.id,
        status="new",
        remark=payload.remark,
    )
    session.add(invoice)
    await session.flush()

    for index, line in enumerate(lines, start=1):
        item_desc = (line.item_desc or "").strip()[:200]
        if not item_desc:
            continue
        session.add(
            t.InvoiceLine(
                invoice_id=invoice.id,
                line_no=index,
                item_desc=item_desc,
                spec_bucket=line.spec_bucket,
                qty=_dec(line.qty),
                unit_price_tax_excl=line.unit_price_tax_excl,
                tax_rate=_tax_rate(line.tax_rate),
                amount_tax_excl=line.amount_tax_excl,
                amount_tax_incl=line.amount_tax_incl,
            )
        )

    doc.linked_kind = "invoice"
    doc.linked_id = invoice.id
    if payload.po_no:
        warnings.append(f"发票备注关联 PO {payload.po_no}，M3 匹配时请填写该 PO 号")

    try:
        await session.commit()
    except IntegrityError:
        # 并发建档同一发票号：唯一约束兜底，返回 409 而不是 500
        await session.rollback()
        existing = await session.scalar(select(t.Invoice).where(t.Invoice.invoice_no == invoice_no).limit(1))
        raise InvoiceConflict(
            "并发建档冲突：发票号已存在",
            details={"invoice_no": invoice_no, "invoice_id": str(existing.id) if existing else None},
        ) from None
    await session.refresh(invoice)
    return _invoice_result(
        invoice,
        created=True,
        already_linked=False,
        lines=len(lines),
        warnings=warnings,
    )


def _invoice_result(
    invoice: t.Invoice,
    *,
    created: bool,
    already_linked: bool,
    lines: int,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "created": created,
        "already_linked": already_linked,
        "invoice": {
            "id": str(invoice.id),
            "invoice_no": invoice.invoice_no,
            "invoice_code": invoice.invoice_code,
            "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            "amount_tax_excl": float(invoice.amount_tax_excl),
            "tax_amount": float(invoice.tax_amount),
            "amount_tax_incl": float(invoice.amount_tax_incl),
            "supplier_id": str(invoice.supplier_id) if invoice.supplier_id else None,
            "doc_id": str(invoice.doc_id) if invoice.doc_id else None,
            "status": invoice.status,
        },
        "lines": lines,
        "warnings": warnings,
    }
