"""M2 供应商准入/复评图（Spec §6）：6 条硬闸门 → 五维确定性评分 → 敏感性 → LLM 复核者 → HITL → finalize。

关键边界：
- 闸门命中直接 `reject/frozen`，**跳过评分与 LLM**（省 token、避免模型美化否决项）；
- 分数公式在 `app/scoring/supplier.py`（纯函数、可单测），LLM 复核者输出 schema **没有分数字段**；
- 决策前不写 `supplier_eval` / `supplier.status`；finalize 单事务提交并留 `audit_log`。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from statistics import median
from time import perf_counter
from typing import Any, Awaitable, Callable, TypedDict
from uuid import UUID

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.router import get_router
from app.llm.types import LLMResult, Message
from app.models import tables as t
from app.observability.runevents import EventBus
from app.schemas.supplier import (
    DimensionScoreModel,
    GateHitModel,
    LlmRiskReview,
    SupplierEvalResult,
)
from app.scoring.supplier import (
    DEFAULT_WEIGHTS,
    DIMENSION_LABELS,
    DIMENSIONS,
    DimensionScore,
    clamp,
    compliance_score,
    confidence,
    delivery_score,
    evaluate_gates,
    gate_verdict,
    normalize_weights,
    price_score,
    quality_score,
    sensitivity_analysis,
    service_score,
    verdict_for,
    weighted_total,
)

GRAPH_VERSION = "m2-supplier-v1"
COLD_START_CONDITIONS = [
    "设置 3 个月试用期，期间每笔订单验收后复盘",
    "首单 100% 抽检，连续 3 单合格后转为常规抽检",
]

LLM_REVIEW_PROMPT = """【角色】你是采购风控复核人。材料是供应商主体信息、资质证照要点、量化指标表、历史交付与工单摘要、本轮报价对比。
请像一位严格的评审人一样，只找问题、不写表扬。

【规则】
1. 只依据 <evidence_pack> 中的材料；引用必须指向其中的 id（po_no / ticket_id / cert_no / gate code）。
2. 不要给分、不要建议结论（系统已用确定性模型算好分数与闸门结果，你无权修改）。
3. 输出 3~6 条最有价值的风险点，每条含 dimension、claim（≤80字）、evidence_refs、severity_hint。
4. 输出 0~3 条"有条件准入"的具体条件（可执行、可验收），不要与系统条件重复。
5. 若证据不足以支撑某个判断，写进 insufficient_evidence，不要猜测。

<evidence_pack>
{evidence_pack}
</evidence_pack>

【输出 JSON Schema（必须严格匹配字段名，不得输出 schema 之外的键）】
{schema}
"""


class SupplierState(TypedDict, total=False):
    run_id: str
    subject: dict[str, Any]
    supplier_id: str
    supplier_code: str
    mode: str
    target_category: str | None
    context: dict[str, Any]
    gates: list[dict[str, Any]]
    gate_veto: bool
    gate_frozen: bool
    metrics: dict[str, Any]
    evidence_pack: dict[str, Any]
    dimensions: list[dict[str, Any]]
    total: float
    weight_version: str
    sensitivity: dict[str, Any]
    llm_review: dict[str, Any] | None
    llm_status: str
    llm_error: str | None
    report: dict[str, Any]
    decision: str | None
    reviewer_note: str | None
    review_id: str | None
    finalized: bool
    artifact_id: str | None


@dataclass(slots=True)
class SupplierDeps:
    bus: EventBus
    session_factory: async_sessionmaker[AsyncSession]
    # 允许测试注入假 LLM：callable(messages, schema) -> LLMResult
    llm_complete: Callable[[list[Message], Any], Awaitable[LLMResult]] | None = None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


# --------------------------------------------------------------------------- 数据加载


async def load_supplier_context(
    session: AsyncSession,
    supplier_code: str,
    *,
    mode: str = "review",
    category_code: str | None = None,
) -> dict[str, Any]:
    supplier = await session.scalar(select(t.Supplier).where(t.Supplier.code == supplier_code))
    if supplier is None:
        raise ValueError(f"供应商不存在：{supplier_code}")
    certs = (await session.scalars(select(t.SupplierCert).where(t.SupplierCert.supplier_id == supplier.id))).all()
    tickets = (
        await session.scalars(select(t.ServiceTicket).where(t.ServiceTicket.supplier_id == supplier.id))
    ).all()
    history = (
        await session.scalars(select(t.PoLineHistory).where(t.PoLineHistory.supplier_id == supplier.id))
    ).all()
    pos = (await session.scalars(select(t.Po).where(t.Po.supplier_id == supplier.id))).all()
    po_lines = (
        await session.scalars(select(t.PoLine).where(t.PoLine.po_id.in_([po.id for po in pos])))
    ).all() if pos else []
    receipts = (
        await session.scalars(select(t.GoodsReceipt).where(t.GoodsReceipt.po_id.in_([po.id for po in pos])))
    ).all() if pos else []
    receipt_lines = (
        await session.scalars(
            select(t.ReceiptLine).where(t.ReceiptLine.receipt_id.in_([receipt.id for receipt in receipts]))
        )
    ).all() if receipts else []

    target_category = category_code or (supplier.category_scope[0] if supplier.category_scope else None)
    weight_rows = (
        await session.scalars(
            select(t.SupplierWeight).where(
                t.SupplierWeight.category_code == target_category,
                t.SupplierWeight.version == "v1",
            )
        )
    ).all()
    weights = normalize_weights(
        {row.dimension: _as_float(row.weight) or 0.0 for row in weight_rows}
    ) if weight_rows else dict(DEFAULT_WEIGHTS.get(target_category or "", DEFAULT_WEIGHTS["IT_HW"]))
    cold_start = supplier.onboarded_at is None or supplier.status == "pending" or not history

    return {
        "supplier": {
            "supplier_id": str(supplier.id),
            "code": supplier.code,
            "name": supplier.name,
            "status": supplier.status,
            "category_scope": list(supplier.category_scope or []),
            "risk_flags": dict(supplier.risk_flags or {}),
            "onboarded_at": supplier.onboarded_at.date().isoformat() if supplier.onboarded_at else None,
            "phone": None,
        },
        "mode": mode,
        "target_category": target_category,
        "category_scope": list(supplier.category_scope or []),
        "certs": [
            {
                "cert_no": cert.cert_no,
                "cert_type": cert.cert_type,
                "issuer": cert.issuer,
                "valid_to": _as_date(cert.valid_to).isoformat() if _as_date(cert.valid_to) else None,
                "doc_id": str(cert.doc_id) if cert.doc_id else None,
            }
            for cert in certs
        ],
        "history": [
            {
                "po_no": row.po_no,
                "spec_bucket": row.spec_bucket,
                "unit_price_tax_excl": _as_float(row.unit_price_tax_excl),
                "qty": _as_float(row.qty),
                "order_date": _as_date(row.order_date).isoformat() if row.order_date else None,
            }
            for row in history
        ],
        "tickets": [
            {
                "ticket_id": str(ticket.id),
                "severity": ticket.severity,
                "opened_at": ticket.opened_at.isoformat() if ticket.opened_at else None,
                "first_response_hours": (
                    round((ticket.first_response_at - ticket.opened_at).total_seconds() / 3600, 2)
                    if ticket.first_response_at and ticket.opened_at
                    else None
                ),
                "resolve_hours": (
                    round((ticket.resolved_at - ticket.opened_at).total_seconds() / 3600, 2)
                    if ticket.resolved_at and ticket.opened_at
                    else None
                ),
                "escalated": bool(ticket.escalated),
                "reopened": bool(ticket.reopened),
            }
            for ticket in tickets
        ],
        "po_info": [
            {
                "po_id": str(po.id),
                "po_no": po.po_no,
                "status": po.status,
                "promised_dates": [
                    _as_date(line.promised_date).isoformat()
                    for line in po_lines
                    if line.po_id == po.id and line.promised_date
                ],
            }
            for po in pos
        ],
        "receipt_info": [
            {
                "receipt_id": str(receipt.id),
                "po_id": str(receipt.po_id),
                "received_at": _as_date(receipt.received_at).isoformat() if receipt.received_at else None,
                "lines": [
                    {
                        "po_line_id": str(line.po_line_id),
                        "qc_result": line.qc_result,
                        "qty": _as_float(line.qty),
                    }
                    for line in receipt_lines
                    if line.receipt_id == receipt.id
                ],
            }
            for receipt in receipts
        ],
        "weights": weights,
        "cold_start": cold_start,
    }


# --------------------------------------------------------------------------- 指标聚合（真实 SQL/ORM）


async def aggregate_metrics(session: AsyncSession, context: dict[str, Any]) -> dict[str, Any]:
    target_category = context["target_category"]
    buckets = {row["spec_bucket"] for row in context["history"] if row["spec_bucket"]}
    p50_by_bucket: dict[str, float] = {}
    if buckets:
        rows = (
            await session.scalars(
                select(t.PoLineHistory).where(t.PoLineHistory.spec_bucket.in_(buckets))
            )
        ).all()
        grouped: dict[str, list[float]] = {}
        for row in rows:
            price = _as_float(row.unit_price_tax_excl)
            if price is not None:
                grouped.setdefault(row.spec_bucket, []).append(price)
        p50_by_bucket = {bucket: median(values) for bucket, values in grouped.items() if values}

    weighted_index = 0.0
    weighted_qty = 0.0
    for row in context["history"]:
        price = row["unit_price_tax_excl"]
        qty = row["qty"] or 0
        reference = p50_by_bucket.get(row["spec_bucket"])
        if price is None or reference in (None, 0):
            continue
        weighted_index += (price / reference) * qty
        weighted_qty += qty
    price_index = (weighted_index / weighted_qty) if weighted_qty else None

    # 交付：PO 行承诺交期 vs 最早收货日期
    po_lines = (
        await session.scalars(
            select(t.PoLine).where(t.PoLine.po_id.in_([UUID(po["po_id"]) for po in context["po_info"]]))
        )
    ).all() if context["po_info"] else []
    promised = {str(line.id): _as_date(line.promised_date) for line in po_lines}
    earliest_receipt: dict[str, date] = {}
    for receipt in context["receipt_info"]:
        received = date.fromisoformat(receipt["received_at"]) if receipt["received_at"] else None
        if received is None:
            continue
        for line in receipt["lines"]:
            current = earliest_receipt.get(line["po_line_id"])
            if current is None or received < current:
                earliest_receipt[line["po_line_id"]] = received
    on_time = total_received = 0
    delays: list[float] = []
    for line_id, promised_date in promised.items():
        received = earliest_receipt.get(line_id)
        if received is None or promised_date is None:
            continue
        total_received += 1
        delay = (received - promised_date).days
        if delay <= 0:
            on_time += 1
        else:
            delays.append(float(delay))
    otd = (on_time / total_received) if total_received else None
    avg_delay = (sum(delays) / len(delays)) if delays else 0.0
    partial_rate = 0.0
    if context["po_info"]:
        partial_count = sum(1 for po in context["po_info"] if po["status"] == "partial")
        partial_rate = partial_count / len(context["po_info"])

    # 质量：收货行 QC 结果
    qc_total = 0
    qc_pass = 0
    for receipt in context["receipt_info"]:
        for line in receipt["lines"]:
            qc_total += 1
            if line["qc_result"] in {"pass", "partial"}:
                qc_pass += 1
    qc_pass_rate = (qc_pass / qc_total) if qc_total else None
    defect_rate = (1 - qc_pass_rate) if qc_pass_rate is not None else None

    # 服务：工单响应/解决/升级
    first_responses = [ticket["first_response_hours"] for ticket in context["tickets"] if ticket["first_response_hours"] is not None]
    resolves = [ticket["resolve_hours"] for ticket in context["tickets"] if ticket["resolve_hours"] is not None]
    escalations = sum(1 for ticket in context["tickets"] if ticket["escalated"])
    first_response_avg = (sum(first_responses) / len(first_responses)) if first_responses else None
    resolve_avg = (sum(resolves) / len(resolves)) if resolves else None
    escalation_rate = (escalations / len(context["tickets"])) if context["tickets"] else 0.0

    # 合规：证照临期 / 范围覆盖 / 安全资质 / 异常标记
    today = date.today()
    cert_expiring = False
    for cert in context["certs"]:
        valid_to = date.fromisoformat(cert["valid_to"]) if cert.get("valid_to") else None
        if valid_to and today <= valid_to <= date.fromordinal(today.toordinal() + 30):
            cert_expiring = True
    scope_partial = bool(target_category and target_category not in context["category_scope"])
    needs_security = target_category == "SAAS" or "SAAS" in context["category_scope"]
    has_security = any(cert["cert_type"] in {"ISO27001", "等保三级"} for cert in context["certs"])
    missing_security = needs_security and not has_security
    abnormal_flags = any(
        context["supplier"]["risk_flags"].get(flag)
        for flag in ("license_invalid", "dishonest_judgment", "tax_abnormal")
    )

    data_completeness_values = {
        "price_index": price_index,
        "otd": otd,
        "qc_pass_rate": qc_pass_rate,
        "first_response_avg": first_response_avg,
    }
    completeness = sum(1 for value in data_completeness_values.values() if value is not None) / len(
        data_completeness_values
    )
    last_order = max(
        (date.fromisoformat(row["order_date"]) for row in context["history"] if row["order_date"]),
        default=None,
    )
    months_since_last = ((today - last_order).days / 30.0) if last_order else None
    confidence_info = confidence(
        data_completeness=completeness,
        n_lines=len(context["history"]),
        months_since_last_order=months_since_last,
    )
    if context["cold_start"]:
        # 冷启动没有历史数据：置信度强制 low（Spec §6.5），不允许"没数据的 85 分"
        confidence_info = {
            **confidence_info,
            "value": min(float(confidence_info["value"]), 0.49),
            "level": "low",
        }

    metrics = {
        "price": {
            "price_index": round(price_index, 4) if price_index is not None else None,
            "sample_size": len(context["history"]),
            "p50_reference": round(median(p50_by_bucket.values()), 2) if p50_by_bucket else None,
        },
        "delivery": {
            "otd": round(otd, 4) if otd is not None else None,
            "avg_delay_days": round(avg_delay, 2),
            "partial_ship_rate": round(partial_rate, 4),
            "sample_size": total_received,
        },
        "quality": {
            "qc_pass_rate": round(qc_pass_rate, 4) if qc_pass_rate is not None else None,
            "defect_rate": round(defect_rate, 4) if defect_rate is not None else None,
            "sample_size": qc_total,
        },
        "service": {
            "first_response_hours": round(first_response_avg, 2) if first_response_avg is not None else None,
            "resolve_hours": round(resolve_avg, 2) if resolve_avg is not None else None,
            "escalation_rate": round(escalation_rate, 4),
            "sample_size": len(context["tickets"]),
        },
        "compliance": {
            "cert_expiring": cert_expiring,
            "scope_partial": scope_partial,
            "missing_security_cert": missing_security,
            "abnormal_flag": abnormal_flags,
            "sample_size": len(context["certs"]),
        },
        "confidence": confidence_info,
    }
    return metrics


# --------------------------------------------------------------------------- 评分 / 复核


def build_dimensions(metrics: dict[str, Any], weights: dict[str, float]) -> list[DimensionScore]:
    price_metrics = metrics["price"]
    delivery_metrics = metrics["delivery"]
    quality_metrics = metrics["quality"]
    service_metrics = metrics["service"]
    compliance_metrics = metrics["compliance"]

    scores = [
        DimensionScore(
            dimension="price",
            score=price_score(price_metrics["price_index"]),
            weight=weights.get("price", 0.0),
            raw_metrics=price_metrics,
            sample_size=int(price_metrics.get("sample_size") or 0),
        ),
        DimensionScore(
            dimension="delivery",
            score=delivery_score(
                delivery_metrics["otd"],
                delivery_metrics.get("avg_delay_days") or 0.0,
                delivery_metrics.get("partial_ship_rate") or 0.0,
            ),
            weight=weights.get("delivery", 0.0),
            raw_metrics=delivery_metrics,
            sample_size=int(delivery_metrics.get("sample_size") or 0),
        ),
        DimensionScore(
            dimension="quality",
            score=quality_score(quality_metrics["qc_pass_rate"], quality_metrics["defect_rate"]),
            weight=weights.get("quality", 0.0),
            raw_metrics=quality_metrics,
            sample_size=int(quality_metrics.get("sample_size") or 0),
        ),
        DimensionScore(
            dimension="service",
            score=service_score(
                service_metrics["first_response_hours"],
                service_metrics.get("resolve_hours"),
                service_metrics.get("escalation_rate") or 0.0,
            ),
            weight=weights.get("service", 0.0),
            raw_metrics=service_metrics,
            sample_size=int(service_metrics.get("sample_size") or 0),
        ),
        DimensionScore(
            dimension="compliance",
            score=compliance_score(
                cert_expiring=bool(compliance_metrics["cert_expiring"]),
                scope_partial=bool(compliance_metrics["scope_partial"]),
                missing_security_cert=bool(compliance_metrics["missing_security_cert"]),
                any_abnormal_flag=bool(compliance_metrics["abnormal_flag"]),
            ),
            weight=weights.get("compliance", 0.0),
            raw_metrics=compliance_metrics,
            sample_size=int(compliance_metrics.get("sample_size") or 0),
        ),
    ]
    return scores


def _evidence_ids(context: dict[str, Any], gates: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    ids.extend([row["po_no"] for row in context["history"][:10]])
    ids.extend([ticket["ticket_id"] for ticket in context["tickets"][:10]])
    ids.extend([cert["cert_no"] for cert in context["certs"] if cert.get("cert_no")][:10])
    ids.extend([gate["code"] for gate in gates])
    return sorted(set(filter(None, ids)))


def build_evidence_pack(context: dict[str, Any], metrics: dict[str, Any], gates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "supplier": context["supplier"],
        "mode": context["mode"],
        "target_category": context["target_category"],
        "gates": gates,
        "metrics": {
            dimension: {key: value for key, value in metrics[dimension].items() if key != "sample_size"}
            for dimension in DIMENSIONS
        },
        "certs": context["certs"][:6],
        "recent_pos": [row["po_no"] for row in context["history"][:5]],
        "recent_tickets": [
            {
                "ticket_id": ticket["ticket_id"],
                "severity": ticket["severity"],
                "first_response_hours": ticket["first_response_hours"],
                "resolve_hours": ticket["resolve_hours"],
                "escalated": ticket["escalated"],
            }
            for ticket in context["tickets"][:5]
        ],
        "ids": _evidence_ids(context, gates),
    }


def validate_llm_review(review: LlmRiskReview, pack_ids: set[str]) -> tuple[LlmRiskReview, int]:
    """后置幻觉校验：evidence_refs 不在证据包内的 risk 条目丢弃，返回 (清洗后 review, 被丢弃条数)。"""
    dropped = 0
    valid_risks = []
    for risk in review.top_risks:
        refs = [ref for ref in risk.evidence_refs if ref in pack_ids]
        if not refs:
            dropped += 1
            continue
        valid_risks.append(risk.model_copy(update={"evidence_refs": refs}))
    clean = review.model_copy(update={"top_risks": valid_risks})
    return clean, dropped


async def call_llm_review(
    deps: SupplierDeps,
    context: dict[str, Any],
    evidence_pack: dict[str, Any],
    run_id: UUID | None = None,
) -> tuple[dict[str, Any] | None, str, str | None, int]:
    schema_json = json.dumps(LlmRiskReview.model_json_schema(), ensure_ascii=False)
    prompt = LLM_REVIEW_PROMPT.format(
        evidence_pack=json.dumps(evidence_pack, ensure_ascii=False, default=str),
        schema=schema_json,
    )
    messages = [Message(role="system", content=prompt), Message(role="user", content="请按 schema 输出复核结果。")]
    last_error: Exception | None = None
    # 结构化输出失败时给模型一次自我修复机会（计入同一次 step 的两次调用，便于记账）
    for _attempt in range(2):
        try:
            if deps.llm_complete is not None:
                result = await deps.llm_complete(messages, LlmRiskReview)
            else:
                result = await get_router().complete(
                    role="reason",
                    messages=messages,
                    schema=LlmRiskReview,
                    temperature=0.0,
                    step="m2.llm_risk_review",
                    run_id=run_id,
                )
            if result.text and _output_finish_reason(result) == "length":
                raise ValueError("OUTPUT_TRUNCATED：输出被 max_tokens 截断")
            payload = _normalize_llm_payload(_load_json_object(result.text))
            review = LlmRiskReview.model_validate(payload)
            clean, dropped = validate_llm_review(review, set(evidence_pack["ids"]))
            return clean.model_dump(mode="json"), "ok", None, dropped
        except Exception as exc:  # noqa: BLE001 - LLM 失败必须降级而非伪造
            last_error = exc
            messages = [
                *messages,
                Message(
                    role="user",
                    content=(
                        f"上一次输出不符合要求：{exc}。请更精简（top_risks ≤3 条、conditions ≤2 条、"
                        "reviewer_memo ≤80 字），严格输出一个完整合法的 JSON 对象，不要 markdown/解释。"
                    ),
                ),
            ]
    return None, "unavailable", f"{type(last_error).__name__}: {last_error}", 0


def _output_finish_reason(result: Any) -> str | None:
    try:
        choices = (result.raw or {}).get("choices") or []
        return str(choices[0].get("finish_reason")) if choices else None
    except (AttributeError, IndexError, TypeError):
        return None


def _load_json_object(text: str) -> Any:
    """从模型输出中提取 JSON 对象：容忍 ```json 围栏、前后缀说明文字。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


def _normalize_llm_payload(payload: Any) -> Any:
    """模型常见越界：claim 超 80 字、条件/风险条数超限、memo 超 120 字。
    这些是"表达超出字数"，不是事实错误；截断/裁剪后继续做引用校验，避免整次降级。"""
    if not isinstance(payload, dict):
        return payload
    risks = payload.get("top_risks")
    if isinstance(risks, list):
        trimmed: list[Any] = []
        for risk in risks[:6]:
            if isinstance(risk, dict) and isinstance(risk.get("claim"), str):
                risk["claim"] = risk["claim"][:80]
            trimmed.append(risk)
        payload["top_risks"] = trimmed
    conditions = payload.get("conditions")
    if isinstance(conditions, list):
        payload["conditions"] = [str(condition)[:120] for condition in conditions[:3]]
    memo = payload.get("reviewer_memo")
    if isinstance(memo, str):
        payload["reviewer_memo"] = memo[:120]
    return payload


# --------------------------------------------------------------------------- 图节点


async def _load(state: SupplierState, deps: SupplierDeps) -> dict[str, Any]:
    subject = state["subject"]
    supplier_code = str(subject.get("supplier_code") or "")
    if not supplier_code:
        raise ValueError("M2 运行需要 subject.supplier_code")
    async with deps.session_factory() as session:
        context = await load_supplier_context(
            session,
            supplier_code,
            mode=str(subject.get("mode") or "review"),
            category_code=subject.get("category_code"),
        )
    return {"supplier_id": context["supplier"]["supplier_id"], "supplier_code": supplier_code, "context": context}


def _gate(state: SupplierState, deps: SupplierDeps) -> dict[str, Any]:
    context = state["context"]
    hits = evaluate_gates(
        risk_flags=context["supplier"]["risk_flags"],
        status=context["supplier"]["status"],
        certs=context["certs"],
        target_category=context["target_category"],
        category_scope=context["category_scope"],
    )
    veto, frozen, _soft = gate_verdict(hits)
    return {"gates": hits, "gate_veto": veto, "gate_frozen": frozen}


async def _aggregate(state: SupplierState, deps: SupplierDeps) -> dict[str, Any]:
    async with deps.session_factory() as session:
        metrics = await aggregate_metrics(session, state["context"])
    evidence_pack = build_evidence_pack(state["context"], metrics, state.get("gates", []))
    return {"metrics": metrics, "evidence_pack": evidence_pack}


def _score(state: SupplierState, deps: SupplierDeps) -> dict[str, Any]:
    context = state["context"]
    metrics = state["metrics"]
    scores = build_dimensions(metrics, context["weights"])
    return {
        "dimensions": [
            {
                "dimension": score.dimension,
                "score": round(score.score, 2),
                "weight": round(score.weight, 4),
                "raw_metrics": score.raw_metrics if False else score.raw_metrics,
                "sample_size": score.sample_size,
                "note": score.note,
            }
            for score in scores
        ],
        "total": weighted_total(scores),
        "weight_version": "v1",
    }


def _sensitivity(state: SupplierState, deps: SupplierDeps) -> dict[str, Any]:
    metrics = state["metrics"]
    weights = state["context"]["weights"]
    scores = build_dimensions(metrics, weights)
    return {"sensitivity": sensitivity_analysis(scores, base_weights=weights)}


async def _llm_review(state: SupplierState, deps: SupplierDeps) -> dict[str, Any]:
    if state.get("gate_veto"):
        return {"llm_review": None, "llm_status": "skipped_gate_hit", "llm_error": None}
    review, status, error, dropped = await call_llm_review(
        deps, state["context"], state["evidence_pack"], UUID(state["run_id"])
    )
    return {"llm_review": review, "llm_status": status, "llm_error": error, "llm_dropped_refs": dropped}


def _compose(state: SupplierState, deps: SupplierDeps) -> dict[str, Any]:
    context = state["context"]
    gates = state.get("gates", [])
    veto = state.get("gate_veto", False)
    frozen = state.get("gate_frozen", False)
    metrics = state.get("metrics") or {}
    dimensions = state.get("dimensions") or []
    total = float(state.get("total") or 0.0)
    conditions: list[str] = []
    for gate in gates:
        if not gate.get("veto"):
            conditions.append(gate["message"])
    if context.get("cold_start"):
        conditions.extend(COLD_START_CONDITIONS)
    if not veto and state.get("total") is not None and total < 80 and not context.get("cold_start"):
        conditions.append("连续 3 单验收合格后再复评，未达标则降级")

    verdict = (
        verdict_for(total, gate_reject=veto, frozen=frozen, cold_start=bool(context.get("cold_start")))
        if not veto
        else ("frozen" if frozen else "reject")
    )
    llm_review = state.get("llm_review")
    if isinstance(llm_review, dict) and llm_review.get("conditions"):
        for condition in llm_review["conditions"]:
            if condition not in conditions:
                conditions.append(condition)
    confidence_info = metrics.get("confidence") or {"value": 0.0, "level": "low"}
    report = SupplierEvalResult(
        supplier_id=context["supplier"]["supplier_id"],
        supplier_code=context["supplier"]["code"],
        supplier_name=context["supplier"]["name"],
        mode=context["mode"],
        category_code=context["target_category"],
        dimensions=[DimensionScoreModel(**dimension) for dimension in dimensions],
        total=round(total, 2),
        verdict=verdict,
        gate_hits=[GateHitModel(**gate) for gate in gates],
        confidence=confidence_info.get("level", "low"),
        confidence_detail=confidence_info,
        conditions=conditions,
        llm_review=LlmRiskReview.model_validate(llm_review) if llm_review else None,
        llm_status=state.get("llm_status", "unavailable"),
        sensitivity=state.get("sensitivity") or {},
        weight_version=state.get("weight_version", "v1"),
        evidence_pack_ids=(state.get("evidence_pack") or {}).get("ids", []),
        cold_start=bool(context.get("cold_start")),
        generated_at=datetime.now(UTC),
    ).model_dump(mode="json")
    report.update(
        {
            "module": "m2",
            "graph_version": GRAPH_VERSION,
            "review_kind": "submit_verdict",
            "requires_review": True,
            "editable": False,
            "summary": (
                f"{context['supplier']['name']}（{context['supplier']['code']}）"
                f"判定 {verdict}，总分 {round(total, 2)}，置信度 {confidence_info.get('level')}"
                if not veto
                else f"{context['supplier']['name']} 命中硬闸门 {', '.join(gate['code'] for gate in gates)}，直接否决"
            ),
            "gate_count": len(gates),
            "cold_start": bool(context.get("cold_start")),
            "llm_status": state.get("llm_status") or ("skipped_gate_hit" if veto else "unavailable"),
            "llm_error": state.get("llm_error"),
            "llm_dropped_refs": int(state.get("llm_dropped_refs") or 0),
            "llm_dropped_refs_note": (
                "LLM 输出的风险条目因 evidence_refs 不在证据包内被后置校验丢弃"
                if int(state.get("llm_dropped_refs") or 0) > 0
                else None
            ),
        }
    )
    return {"report": report}


async def _finalize(state: SupplierState, deps: SupplierDeps) -> dict[str, Any]:
    run_id = UUID(state["run_id"])
    decision = state.get("decision") or "reject"
    release = decision in {"accept", "edit"}
    report = state["report"]
    review_id = UUID(state["review_id"]) if state.get("review_id") else None
    context = state["context"]

    async with deps.session_factory() as session:
        # 锁 run 行：并发 resume 同一 run 时串行化，审计查重才真正原子
        run_row = await session.get(t.AgentRun, run_id, with_for_update=True)
        if run_row is None:
            raise ValueError(f"运行不存在：{run_id}")
        existing = await session.scalar(
            select(t.AuditLog.id).where(t.AuditLog.run_id == run_id, t.AuditLog.target_table == "supplier")
        )
        if existing is not None:
            if run_row.status != "succeeded":
                run_row.status = "succeeded"
                run_row.finished_at = datetime.now(UTC)
                await session.commit()
            return {"finalized": True, "artifact_id": None}

        supplier = await session.get(t.Supplier, UUID(context["supplier"]["supplier_id"]))
        if supplier is None:
            raise ValueError("供应商不存在")
        before_status = supplier.status
        if release:
            verdict = report["verdict"]
            if verdict == "approve":
                supplier.status = "active"
            elif verdict == "conditional":
                supplier.status = "conditional"
            elif verdict == "frozen":
                supplier.status = "frozen"
            # reject 不改状态（保持当前状态，由黑名单/冻结保留）

        artifact = t.Artifact(
            run_id=run_id,
            module="m2",
            kind="supplier_eval",
            payload={
                "report": report,
                "decision": decision,
                "reviewer_note": state.get("reviewer_note"),
            },
        )
        session.add(artifact)
        await session.flush()

        session.add(
            t.SupplierEval(
                supplier_id=supplier.id,
                run_id=run_id,
                artifact_id=artifact.id,
                mode=report["mode"],
                total=Decimal(str(report["total"])),
                verdict=report["verdict"],
                confidence=report["confidence"],
                gate_hits=report["gate_hits"],
                dimensions=report["dimensions"],
                weight_version=report.get("weight_version"),
            )
        )
        session.add(
            t.AuditLog(
                run_id=run_id,
                review_id=review_id,
                actor_role="buyer",
                target_table="supplier",
                target_id=supplier.id,
                action="release" if release else "hold",
                before_json={"status": before_status},
                after_json={"status": supplier.status, "verdict": report["verdict"] if release else None},
                source="agent",
                note=f"M2 准入决策：{decision}",
            )
        )
        run_row.status = "succeeded"
        run_row.finished_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(artifact)
        artifact_id = str(artifact.id)
    return {"finalized": True, "artifact_id": artifact_id}


def _instrumented(deps: SupplierDeps, name: str, fn) -> Any:
    async def node(state: SupplierState) -> dict[str, Any]:
        run_id = UUID(state["run_id"])
        await deps.bus.emit(run_id, "node.start", {"node": name})
        started = perf_counter()
        result = fn(state, deps)
        updates = await result if hasattr(result, "__await__") else result
        await deps.bus.emit(
            run_id,
            "node.end",
            {
                "node": name,
                "duration_ms": int((perf_counter() - started) * 1000),
                "llm_calls": 1 if name == "llm_risk_review" and not state.get("gate_veto") else 0,
                "tool_calls": 0,
            },
        )
        return updates or {}

    return node


def build_supplier_graph(deps: SupplierDeps):
    graph = StateGraph(SupplierState)
    graph.add_node("load_supplier", _instrumented(deps, "load_supplier", _load))
    graph.add_node("gate_check", _instrumented(deps, "gate_check", _gate))
    graph.add_node("aggregate_metrics", _instrumented(deps, "aggregate_metrics", _aggregate))
    graph.add_node("score", _instrumented(deps, "score", _score))
    graph.add_node("sensitivity", _instrumented(deps, "sensitivity", _sensitivity))
    graph.add_node("llm_risk_review", _instrumented(deps, "llm_risk_review", _llm_review))
    graph.add_node("compose_result", _instrumented(deps, "compose_result", _compose))
    graph.add_node("finalize", _instrumented(deps, "finalize", _finalize))

    graph.add_edge(START, "load_supplier")
    graph.add_edge("load_supplier", "gate_check")
    graph.add_conditional_edges(
        "gate_check",
        lambda state: "reject" if state.get("gate_veto") else "score_path",
        {"reject": "compose_result", "score_path": "aggregate_metrics"},
    )
    graph.add_edge("aggregate_metrics", "score")
    graph.add_edge("score", "sensitivity")
    graph.add_edge("sensitivity", "llm_risk_review")
    graph.add_edge("llm_risk_review", "compose_result")
    graph.add_edge("compose_result", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=MemorySaver(), interrupt_before=["finalize"])


async def compute_supplier_report(
    session: AsyncSession,
    supplier_code: str,
    *,
    mode: str = "review",
    category_code: str | None = None,
    llm_complete: Callable[[list[Message], Any], Awaitable[LLMResult]] | None = None,
) -> dict[str, Any]:
    """评测/调试用：不经图，跑同一条确定性链路（gate → metrics → score → sensitivity → llm → compose）。"""
    context = await load_supplier_context(session, supplier_code, mode=mode, category_code=category_code)
    gates = evaluate_gates(
        risk_flags=context["supplier"]["risk_flags"],
        status=context["supplier"]["status"],
        certs=context["certs"],
        target_category=context["target_category"],
        category_scope=context["category_scope"],
    )
    veto, frozen, _soft = gate_verdict(gates)
    state: SupplierState = {
        "run_id": str(uuid.uuid4()),
        "subject": {"supplier_code": supplier_code},
        "context": context,
        "gates": gates,
        "gate_veto": veto,
        "gate_frozen": frozen,
    }
    fake_deps = SupplierDeps(bus=EventBus(None), session_factory=None, llm_complete=llm_complete)  # type: ignore[arg-type]
    if not veto:
        metrics = await aggregate_metrics(session, context)
        scores = build_dimensions(metrics, context["weights"])
        state["metrics"] = metrics
        state["evidence_pack"] = build_evidence_pack(context, metrics, gates)
        state["dimensions"] = [
            {
                "dimension": score.dimension,
                "score": round(score.score, 2),
                "weight": round(score.weight, 4),
                "raw_metrics": score.raw_metrics,
                "sample_size": score.sample_size,
                "note": score.note,
            }
            for score in scores
        ]
        state["total"] = weighted_total(scores)
        state["sensitivity"] = sensitivity_analysis(scores, base_weights=context["weights"])
        review, status, error, _dropped = await call_llm_review(
            fake_deps, context, state["evidence_pack"], None
        )
        state["llm_review"] = review
        state["llm_status"] = status
        state["llm_error"] = error
    else:
        state["llm_review"] = None
        state["llm_status"] = "skipped_gate_hit"
        state["llm_error"] = None
    return _compose(state, fake_deps)["report"]




