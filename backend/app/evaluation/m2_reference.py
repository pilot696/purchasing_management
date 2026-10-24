"""M2 评分的第二实现（独立公式代码）——用于差分校验，不是人工 gold。

审查意见："gold 改由人工标注或至少由不同实现生成"。
本模块只做到后者：用不同代码重写五维公式/阈值，对随机与真实指标做差分；
仍需明确：它不能证明业务权重/阈值本身正确，真正的质量证据需要专家标注。
"""

from __future__ import annotations

from typing import Any


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _interp(x: float, points: list[tuple[float, float]]) -> float:
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def reference_scores(metrics: dict[str, Any], weights: dict[str, float]) -> dict[str, float]:
    price = metrics["price"]
    delivery = metrics["delivery"]
    quality = metrics["quality"]
    service = metrics["service"]
    compliance = metrics["compliance"]

    index = price.get("price_index")
    price_score = 60.0 if index is None else (100.0 if index <= 0.9 else _clamp(100.0 - 120.0 * (index - 1.0)))
    price_score = round(price_score, 2)  # 与生产 price_score 的两位小数口径对齐

    otd = delivery.get("otd")
    if otd is None:
        delivery_score = 60.0
    else:
        delivery_score = _clamp(otd * 100.0 - (delivery.get("partial_ship_rate") or 0.0) * 10.0)
        if (delivery.get("avg_delay_days") or 0.0) > 5:
            delivery_score *= 0.9
        delivery_score = _clamp(delivery_score)

    pass_rate = quality.get("qc_pass_rate")
    quality_score = 60.0 if pass_rate is None else _clamp(100.0 * pass_rate)

    first = service.get("first_response_hours")
    if first is None:
        service_score = 60.0
    else:
        service_score = _interp(
            first, [(4.0, 100.0), (8.0, 85.0), (24.0, 70.0), (48.0, 50.0), (72.0, 30.0)]
        )
        if (service.get("resolve_hours") or 0.0) > 72:
            service_score -= 5.0
        service_score -= 20.0 * max(0.0, min(1.0, service.get("escalation_rate") or 0.0))
        service_score = _clamp(service_score)

    if compliance.get("abnormal_flag"):
        compliance_score = 0.0
    else:
        compliance_score = 100.0
        if compliance.get("cert_expiring"):
            compliance_score -= 20.0
        if compliance.get("scope_partial"):
            compliance_score -= 15.0
        if compliance.get("missing_security_cert"):
            compliance_score -= 30.0
        compliance_score = _clamp(compliance_score)

    scores = {
        "price": price_score,
        "delivery": delivery_score,
        "quality": quality_score,
        "service": service_score,
        "compliance": compliance_score,
    }
    return {name: round(scores[name], 6) for name in scores} | {
        "total": round(sum(scores[name] * weights.get(name, 0.0) for name in scores), 2)
    }


def reference_verdict(total: float, *, gate_reject: bool = False, frozen: bool = False, cold_start: bool = False) -> str:
    if frozen:
        return "frozen"
    if gate_reject:
        return "reject"
    if cold_start:
        return "conditional"
    return "approve" if total >= 80 else ("conditional" if total >= 65 else "reject")
