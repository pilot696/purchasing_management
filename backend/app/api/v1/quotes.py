"""横向比价接口：同一 PR 下多供应商报价按资金占用成本调整后排序。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import SessionDep
from app.core.errors import AppError
from app.models import tables as t
from app.seed.quotes import adjusted_total, payment_days, quote_total

router = APIRouter(tags=["quotes"])


@router.get("/quotes/comparables")
async def comparables(
    session: SessionDep,
    pr_no: str = Query(description="PR 单号，例如 PR-2026-0001"),
    annual_rate: float = Query(default=0.08, ge=0, le=1, description="资金年化成本率，用于账期折算"),
) -> dict[str, Any]:
    pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == pr_no))
    if pr is None:
        raise AppError("PR 不存在", details={"pr_no": pr_no})
    quotes = (
        await session.scalars(select(t.Quote).where(t.Quote.pr_id == pr.id).order_by(t.Quote.id))
    ).all()
    quote_ids = [quote.id for quote in quotes]
    lines_by_quote: dict[Any, list[t.QuoteLine]] = {quote_id: [] for quote_id in quote_ids}
    if quote_ids:
        for line in (
            await session.scalars(select(t.QuoteLine).where(t.QuoteLine.quote_id.in_(quote_ids)))
        ).all():
            lines_by_quote.setdefault(line.quote_id, []).append(line)
    supplier_by_id = {
        supplier.id: supplier
        for supplier in (
            await session.scalars(
                select(t.Supplier).where(t.Supplier.id.in_({quote.supplier_id for quote in quotes}))
            )
        ).all()
    }
    items = (
        await session.scalars(select(t.PrItem).where(t.PrItem.pr_id == pr.id).order_by(t.PrItem.line_no))
    ).all()

    rate = Decimal(str(annual_rate))
    rows: list[dict[str, Any]] = []
    for quote in quotes:
        total = quote_total(lines_by_quote.get(quote.id, []))
        days = payment_days(quote.payment_terms)
        adjusted = adjusted_total(total, quote.payment_terms, rate)
        supplier = supplier_by_id.get(quote.supplier_id)
        rows.append(
            {
                "quote_id": str(quote.id),
                "supplier_code": supplier.code if supplier else None,
                "supplier_name": supplier.name if supplier else None,
                "supplier_status": supplier.status if supplier else None,
                "lead_time_days": quote.lead_time_days,
                "payment_terms": quote.payment_terms,
                "payment_days": days,
                "total_amount": float(total),
                "adjusted_amount": float(adjusted),
                "valid_until": quote.valid_until.isoformat() if quote.valid_until else None,
            }
        )
    if not rows:
        return {
            "pr_no": pr.pr_no,
            "title": pr.title,
            "annual_rate": annual_rate,
            "items": [],
            "quotes": [],
            "recommended_supplier": None,
        }

    min_total = min(row["total_amount"] for row in rows)
    min_adjusted = min(row["adjusted_amount"] for row in rows)
    for row in rows:
        row["price_index"] = round(row["total_amount"] / min_total, 4) if min_total else 0
        row["adjusted_index"] = round(row["adjusted_amount"] / min_adjusted, 4) if min_adjusted else 0
    rows.sort(key=lambda row: (row["adjusted_amount"], row["total_amount"]))
    eligible_rows = [row for row in rows if row["supplier_status"] not in {"blacklisted", "frozen"}]
    recommended = (eligible_rows or rows)[0]
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        row["recommended"] = row is recommended
    return {
        "pr_no": pr.pr_no,
        "title": pr.title,
        "category_code": pr.category_code,
        "annual_rate": annual_rate,
        "items": [
            {
                "line_no": item.line_no,
                "item_name": item.item_name_raw,
                "qty": float(item.qty),
                "unit": item.unit,
            }
            for item in items
        ],
        "quotes": rows,
        "recommended_supplier": recommended["supplier_code"],
    }
