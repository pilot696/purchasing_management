"""M2 确定性评分与硬闸门（Spec §6.3~§6.6）。

原则：分数由公式算、闸门由规则判；LLM 只写叙述（见 agents/supplier.py 的复核节点）。
本模块全部是纯函数，便于单测与评测。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

DIMENSIONS: tuple[str, ...] = ("price", "delivery", "quality", "service", "compliance")
DIMENSION_LABELS: dict[str, str] = {
    "price": "价格竞争力",
    "delivery": "交付履约",
    "quality": "质量",
    "service": "服务响应",
    "compliance": "合规资质",
}
VERDICT_APPROVE = "approve"
VERDICT_CONDITIONAL = "conditional"
VERDICT_REJECT = "reject"
VERDICT_FROZEN = "frozen"

DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "IT_HW": {"price": 0.25, "delivery": 0.30, "quality": 0.20, "service": 0.15, "compliance": 0.10},
    "SAAS": {"price": 0.20, "delivery": 0.15, "quality": 0.20, "service": 0.30, "compliance": 0.15},
    "ADMIN": {"price": 0.35, "delivery": 0.25, "quality": 0.20, "service": 0.15, "compliance": 0.05},
    "MKT_OUTSOURCE": {"price": 0.30, "delivery": 0.20, "quality": 0.20, "service": 0.20, "compliance": 0.10},
}


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def piecewise(x: float, points: list[tuple[float, float]]) -> float:
    """点按 x 升序；线性插值，端点外 clamp。"""
    if not points:
        return 0.0
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            ratio = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * ratio
    return points[-1][1]


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in weights.values())
    if total <= 0:
        return {key: 1.0 / len(weights) for key in weights} if weights else {}
    return {key: max(0.0, value) / total for key, value in weights.items()}


# --------------------------------------------------------------------------- 维度分


def price_score(price_index: float | None) -> float:
    """price_index = 本轮报价 / 同 spec_bucket p50；≤0.9 封顶 100。"""
    if price_index is None:
        return 60.0  # 无成交样本：中位数兜底（置信度会低）
    if price_index <= 0.9:
        return 100.0
    return round(clamp(100.0 - 120.0 * (price_index - 1.0)), 2)


def delivery_score(otd: float | None, avg_delay_days: float = 0.0, partial_ship_rate: float = 0.0) -> float:
    if otd is None:
        return 60.0
    score = clamp(otd * 100.0 - partial_ship_rate * 10.0)
    if avg_delay_days > 5:
        score *= 0.9
    return clamp(score)


def quality_score(qc_pass_rate: float | None, defect_rate: float | None = None) -> float:
    """质量分：以收货行 QC 通过率为唯一独立信号。

    旧公式 60*p + 40*(1-defects) 在 defects = 1-p 时恒等于 100p，
    所谓“40% 缺陷权重”并不存在；这里明确为 100*p，避免误导性公式。
    defect_rate 参数保留兼容，但不再二次惩罚（它以 qc_pass_rate 为来源）。
    """
    if qc_pass_rate is None and defect_rate is None:
        return 60.0
    pass_rate = qc_pass_rate if qc_pass_rate is not None else 1.0 - (defect_rate or 0.0)
    return clamp(100.0 * pass_rate)


def _first_response_points(hours: float) -> float:
    return piecewise(
        hours,
        [(4.0, 100.0), (8.0, 85.0), (24.0, 70.0), (48.0, 50.0), (72.0, 30.0)],
    )


def service_score(
    first_response_hours: float | None,
    resolve_hours: float | None = None,
    escalation_rate: float = 0.0,
) -> float:
    if first_response_hours is None:
        return 60.0
    score = _first_response_points(first_response_hours)
    if resolve_hours is not None and resolve_hours > 72:
        score -= 5.0
    score -= 20.0 * max(0.0, min(1.0, escalation_rate))
    return clamp(score)


def compliance_score(
    *,
    cert_expiring: bool = False,
    scope_partial: bool = False,
    missing_security_cert: bool = False,
    any_abnormal_flag: bool = False,
) -> float:
    if any_abnormal_flag:
        return 0.0
    score = 100.0
    if cert_expiring:
        score -= 20.0
    if scope_partial:
        score -= 15.0
    if missing_security_cert:
        score -= 30.0
    return clamp(score)


@dataclass(slots=True)
class DimensionScore:
    dimension: str
    score: float
    weight: float
    raw_metrics: dict[str, float | None] = field(default_factory=dict)
    sample_size: int = 0
    note: str | None = None


def weighted_total(scores: Iterable[DimensionScore]) -> float:
    return round(sum(score.score * score.weight for score in scores), 2)


def verdict_for(total: float, *, gate_reject: bool = False, frozen: bool = False, cold_start: bool = False) -> str:
    if frozen:
        return VERDICT_FROZEN
    if gate_reject:
        return VERDICT_REJECT
    if cold_start:
        return VERDICT_CONDITIONAL
    if total >= 80:
        return VERDICT_APPROVE
    if total >= 65:
        return VERDICT_CONDITIONAL
    return VERDICT_REJECT


def confidence(
    *,
    data_completeness: float,
    n_lines: int,
    months_since_last_order: float | None,
) -> dict[str, Any]:
    sample_adequacy = min(1.0, n_lines / 10.0)
    freshness = math.exp(-(months_since_last_order or 0.0) / 12.0)
    value = clamp(0.4 * data_completeness + 0.3 * sample_adequacy + 0.3 * freshness, 0.0, 1.0)
    level = "high" if value >= 0.8 else ("medium" if value >= 0.5 else "low")
    return {
        "value": round(value, 4),
        "level": level,
        "data_completeness": round(data_completeness, 4),
        "sample_adequacy": round(sample_adequacy, 4),
        "freshness": round(freshness, 4),
    }


def sensitivity_analysis(
    scores: list[DimensionScore],
    *,
    base_weights: dict[str, float],
    samples: int = 300,
    seed: int = 42,
) -> dict[str, Any]:
    """权重 ±20% 扰动 N 次，统计结论翻转率与最敏感维度。"""
    rng = random.Random(seed)
    base_total = weighted_total(scores)
    base_verdict = verdict_for(base_total)
    flips = 0
    sensitive_counts = {dimension: 0 for dimension in DIMENSIONS}
    score_map = {score.dimension: score.score for score in scores}
    for _ in range(samples):
        perturbed = {key: value * rng.uniform(0.8, 1.2) for key, value in base_weights.items()}
        normalized = normalize_weights(perturbed)
        total = round(sum(score_map[key] * normalized[key] for key in normalized), 2)
        verdict = verdict_for(total)
        if verdict != base_verdict:
            flips += 1
            # 哪一维的权重上调贡献最大，就记成"最敏感"
            deltas = {key: normalized[key] - base_weights.get(key, 0.0) for key in normalized}
            top = max(deltas, key=lambda key: deltas[key])
            sensitive_counts[top] += 1
    most_sensitive = max(sensitive_counts, key=lambda key: sensitive_counts[key]) if flips else "none"
    return {
        "flip_rate": round(flips / samples, 4),
        "most_sensitive": most_sensitive,
        "samples": samples,
        "perturbation": "weights * U(0.8, 1.2), 300 rounds",
        "seed": seed,
    }


# --------------------------------------------------------------------------- 硬闸门


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def evaluate_gates(
    *,
    risk_flags: dict[str, Any],
    status: str,
    certs: list[dict[str, Any]],
    target_category: str | None,
    category_scope: list[str],
    today: date | None = None,
) -> list[dict[str, Any]]:
    """6 条硬闸门 + 黑名单；返回命中列表（空 = 全部通过）。"""
    today = today or date.today()
    hits: list[dict[str, Any]] = []

    def add(code: str, message: str, evidence: list[str], *, veto: bool = True) -> None:
        hits.append({"code": code, "message": message, "evidence_refs": evidence, "veto": veto})

    business_license = next((cert for cert in certs if cert.get("cert_type") == "营业执照"), None)
    cert_ids = [str(cert.get("cert_no") or cert.get("id") or "") for cert in certs]
    license_expiry = _to_date(business_license.get("valid_to")) if business_license else None

    if status == "blacklisted" or risk_flags.get("dishonest_judgment"):
        add(
            "G3_DISHONEST",
            "供应商在内部黑名单中，禁止准入" if status == "blacklisted" else "失信被执行人/严重违法失信记录",
            cert_ids[:2],
        )
    if risk_flags.get("license_invalid") or (license_expiry is not None and license_expiry < today):
        add(
            "G1_LICENSE_INVALID",
            "营业执照失效或经营异常",
            [str(business_license.get("cert_no") or "")] if business_license else [],
        )
    if risk_flags.get("major_default"):
        add("G4_DEFAULT", "近 12 个月存在重大质量事故或单方违约", cert_ids[:2])
    if risk_flags.get("tax_abnormal"):
        add("G5_TAX_ABNORMAL", "税务异常或开票资质失效", cert_ids[:2])
    if risk_flags.get("scope_mismatch"):  # 该标记表示"本次标的物不在经营范围"，需人工核对证照原文
        # G2 处置原则：有证照原文引用才可否决；无引用则降级为人工确认（不直接否决）
        add(
            "G2_SCOPE_MISMATCH",
            "经营范围疑似不覆盖标的物（需人工核对证照原文）",
            [],
            veto=False,
        )
    needs_security = target_category == "SAAS" or "SAAS" in category_scope
    has_security = any(cert.get("cert_type") in {"ISO27001", "等保三级"} for cert in certs)
    if needs_security and (not has_security or risk_flags.get("security_cert_missing")):
        add(
            "G6_SECURITY_CERT",
            "涉及数据处理但缺少 ISO27001/等保三级（30 天内补交）",
            cert_ids[:2],
            veto=False,
        )
    return hits


def gate_verdict(hits: list[dict[str, Any]]) -> tuple[bool, bool, bool]:
    """返回 (是否有否决级命中, 是否冻结, 是否有非否决条件项)。"""
    veto = any(hit["veto"] for hit in hits)
    frozen = any(hit["code"] == "G3_DISHONEST" for hit in hits if hit["veto"])
    soft = any(not hit["veto"] for hit in hits)
    return veto, frozen, soft


