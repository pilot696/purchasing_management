"""生成同一 PR 下多供应商报价（M2 横向比价演示数据）。

数据是确定性的（seed=42 时可按 PR/供应商顺序复现）；只依赖已有 PR/PrItem/Supplier。
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import tables as t

FACTORS = (Decimal("0.92"), Decimal("1.00"), Decimal("1.08"))
PAYMENT_TERMS = ("预付30%", "月结30天", "月结45天", "月结60天")
LEAD_TIMES = (7, 10, 14)


def payment_days(terms: str | None) -> int:
    if not terms:
        return 0
    if "预付" in terms:
        return 0
    matched = re.search(r"(\d+)\s*天", terms)
    return int(matched.group(1)) if matched else 30


async def generate_quotes(session: AsyncSession) -> dict[str, int]:
    """重建全部 quote/quote_line（幂等）。返回写入计数。"""
    await session.execute(delete(t.QuoteLine))
    await session.execute(delete(t.Quote))
    await session.flush()

    suppliers = (await session.scalars(select(t.Supplier).order_by(t.Supplier.code))).all()
    if not suppliers:
        return {"quotes": 0, "quote_lines": 0}
    prs = (await session.scalars(select(t.Pr).order_by(t.Pr.pr_no))).all()
    items_by_pr: dict[uuid.UUID, list[t.PrItem]] = {}
    for item in (await session.scalars(select(t.PrItem).order_by(t.PrItem.line_no))).all():
        items_by_pr.setdefault(item.pr_id, []).append(item)

    quote_count = 0
    line_count = 0
    for pr_index, pr in enumerate(prs):
        items = items_by_pr.get(pr.id, [])
        if not items:
            continue
        eligible = [
            s
            for s in suppliers
            if s.status not in {"blacklisted", "frozen"}
            and (not s.category_scope or pr.category_code in s.category_scope)
        ]
        if len(eligible) < 3:
            eligible = [s for s in suppliers if s.status not in {"blacklisted", "frozen"}]
        if not eligible:
            eligible = suppliers
        # 每个 PR 取 3 家（按编码轮转，保证不同 PR 看到不同组合）
        picked = [eligible[(pr_index + offset) % len(eligible)] for offset in range(min(3, len(eligible)))]
        for offset, supplier in enumerate(picked):
            factor = FACTORS[(pr_index + offset) % len(FACTORS)]
            lead_time = LEAD_TIMES[(pr_index + offset) % len(LEAD_TIMES)]
            terms = PAYMENT_TERMS[(pr_index + offset) % len(PAYMENT_TERMS)]
            quote_id = uuid.uuid4()
            session.add(
                t.Quote(
                    id=quote_id,
                    pr_id=pr.id,
                    supplier_id=supplier.id,
                    valid_until=date.today() + timedelta(days=30),
                    currency="CNY",
                    lead_time_days=lead_time,
                    payment_terms=terms,
                )
            )
            for line in items:
                unit_price = Decimal(line.unit_price_est or 0)
                if unit_price <= 0 and line.amount_est and line.qty:
                    unit_price = (Decimal(line.amount_est) / Decimal(line.qty)).quantize(Decimal("0.0001"))
                session.add(
                    t.QuoteLine(
                        id=uuid.uuid4(),
                        quote_id=quote_id,
                        line_no=line.line_no,
                        item_name_raw=line.item_name_raw,
                        spec_bucket=line.spec_bucket,
                        unit_price_tax_excl=(unit_price * factor).quantize(Decimal("0.0001")),
                        tax_rate=Decimal("0.13"),
                        qty=line.qty,
                    )
                )
                line_count += 1
            quote_count += 1
    await session.flush()
    return {"quotes": quote_count, "quote_lines": line_count}


def quote_total(lines: list[t.QuoteLine]) -> Decimal:
    total = Decimal("0")
    for line in lines:
        total += Decimal(line.qty) * Decimal(line.unit_price_tax_excl) * (Decimal("1") + Decimal(line.tax_rate))
    return total.quantize(Decimal("0.0001"))


def adjusted_total(total: Decimal, terms: str | None, annual_rate: Decimal) -> Decimal:
    days = payment_days(terms)
    return (total * (Decimal("1") + annual_rate * Decimal(days) / Decimal(365))).quantize(Decimal("0.0001"))
