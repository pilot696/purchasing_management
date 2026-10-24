"""账期/付款条件的资金占用折算（M2 价格评分用）。

约定：
- 基准账期 30 天；年化资金成本默认 8%（`M2_FINANCING_ANNUAL_RATE` 可配，0=关闭）；
- 供应商价格指数先按自身平均账期折算，再进入 price_score；
- 账期数据优先取合同 payment_terms.days，缺失时回退报价 payment_terms 文本。
"""

from __future__ import annotations

import re
from typing import Any


def parse_payment_days(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        days = value.get("days")
        try:
            return int(days) if days is not None else None
        except (TypeError, ValueError):
            return None
    text = str(value)
    if "预付" in text:
        return 0
    matched = re.search(r"(\d+)\s*天", text)
    return int(matched.group(1)) if matched else None


def financing_adjustment(
    price_index: float | None,
    payment_days: float | None,
    *,
    annual_rate: float = 0.08,
    baseline_days: float = 30.0,
) -> tuple[float | None, float]:
    """把账期差折算进价格指数：越长账期对买方越有利，指数越低。

    返回 (调整后指数, 调整因子)。无账期数据或参数为 0 时不调整。
    """
    if price_index is None or payment_days is None or annual_rate <= 0:
        return price_index, 1.0
    # 账期越长，未来付款的现值越低：用基准账期现值 / 本供应商账期现值折算
    factor = (1 + annual_rate * baseline_days / 365) / (1 + annual_rate * payment_days / 365)
    return round(price_index * factor, 6), round(factor, 6)
