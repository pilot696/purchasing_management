"""M3 三单匹配图（Spec §7.6）：LangGraph 节点编排 + HITL 中断 + finalize 事务。

节点链路：load_subject → normalize → align → reconcile → compose_report →（interrupt）→ finalize

硬约束：
- 决策前 **不写会计事实**（qty_invoiced / 尾差池 / invoice.status 只在 finalize 事务里写）；
- finalize 幂等（同一 run 重入不会重复核销）；
- 每个节点都发 node.start / node.end 事件（llm_calls=0, tool_calls=0，M3 零工具）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any, TypedDict
from uuid import UUID

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.matching.align import AlignResult, LineMatch, align_hungarian
from app.matching.normalize import normalize
from app.matching.tolerance import Diff, policies_from_rows, reconcile
from app.models import tables as t
from app.observability.runevents import EventBus

GRAPH_VERSION = "m3-matching-v1"
RELEASE_DECISIONS = frozenset({"accept", "edit"})


class MatchingState(TypedDict, total=False):
    run_id: str
    subject: dict[str, Any]
    po_id: str
    contract_id: str | None
    invoice_ids: list[str]
    po: dict[str, Any]
    invoice: dict[str, Any]
    contract: dict[str, Any] | None
    documents: dict[str, str | None]
    receipts: list[dict[str, Any]]
    policies: list[dict[str, Any]]
    requires_gr: bool
    category_code: str
    align: dict[str, Any]
    result: dict[str, Any]
    report: dict[str, Any]
    decision: str | None
    reviewer_note: str | None
    review_id: str | None
    finalized: bool
    artifact_id: str | None


@dataclass(slots=True)
class MatchingDeps:
    bus: EventBus
    session_factory: async_sessionmaker[AsyncSession]


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _as_date_str(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _policy_snapshot(policies: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        policy["diff_type"]: {
            "abs_tol": policy.get("abs_tol"),
            "rel_tol": policy.get("rel_tol"),
            "action": policy.get("action"),
        }
        for policy in policies
    }


# --------------------------------------------------------------------------- 节点


async def load_subject(state: MatchingState, deps: MatchingDeps) -> dict[str, Any]:
    subject = state["subject"]
    po_no = str(subject.get("po_no") or "")
    invoice_nos = [str(no) for no in (subject.get("invoice_nos") or [])]
    if not po_no or not invoice_nos:
        raise ValueError("M3 运行需要 subject.po_no 与 subject.invoice_nos")

    async with deps.session_factory() as session:
        po = await session.scalar(select(t.Po).where(t.Po.po_no == po_no))
        if po is None:
            raise ValueError(f"PO 不存在：{po_no}")
        po_lines = (
            await session.scalars(select(t.PoLine).where(t.PoLine.po_id == po.id).order_by(t.PoLine.line_no))
        ).all()
        po_line_ids = {line.id: line.line_no for line in po_lines}

        invoice = await session.scalar(select(t.Invoice).where(t.Invoice.invoice_no == invoice_nos[0]))
        if invoice is None:
            raise ValueError(f"发票不存在：{invoice_nos[0]}")
        invoice_lines = (
            await session.scalars(
                select(t.InvoiceLine).where(t.InvoiceLine.invoice_id == invoice.id).order_by(t.InvoiceLine.line_no)
            )
        ).all()

        contract = await session.get(t.Contract, po.contract_id) if po.contract_id else None
        po_doc = await session.scalar(
            select(t.Doc).where(t.Doc.linked_kind == "po", t.Doc.linked_id == po.id)
        )
        invoice_doc_id = str(invoice.doc_id) if invoice.doc_id else None
        documents = {
            "po": str(po_doc.id) if po_doc else None,
            "invoice": invoice_doc_id,
            "contract": str(contract.doc_id) if contract is not None and contract.doc_id else None,
        }
        receipts_rows = (
            await session.scalars(
                select(t.GoodsReceipt).where(t.GoodsReceipt.po_id == po.id).order_by(t.GoodsReceipt.received_at)
            )
        ).all()
        receipts: list[dict[str, Any]] = []
        for receipt_row in receipts_rows:
            lines = (
                await session.scalars(select(t.ReceiptLine).where(t.ReceiptLine.receipt_id == receipt_row.id))
            ).all()
            receipts.append(
                {
                    "received_at": _as_date_str(receipt_row.received_at),
                    "lines": [
                        {
                            "po_line_no": po_line_ids.get(line.po_line_id),
                            "qty": _as_float(line.qty),
                            "qc_result": line.qc_result,
                        }
                        for line in lines
                        if po_line_ids.get(line.po_line_id) is not None
                    ],
                }
            )

        policies = [
            {
                "diff_type": row.diff_type,
                "abs_tol": _as_float(row.abs_tol),
                "rel_tol": _as_float(row.rel_tol),
                "action": row.action,
                "priority": row.priority,
                "category_code": row.category_code,
            }
            for row in (
                await session.scalars(
                    select(t.TolerancePolicy).order_by(t.TolerancePolicy.priority, t.TolerancePolicy.id)
                )
            ).all()
        ]

        category_code = ""
        requires_gr = True
        sku_id = next((line.sku_id for line in po_lines if line.sku_id), None)
        if sku_id:
            sku = await session.get(t.SkuCatalog, sku_id)
            if sku is not None:
                category_code = sku.category_code
                template = await session.scalar(
                    select(t.CategoryTemplate).where(t.CategoryTemplate.category_code == sku.category_code)
                )
                if template is not None:
                    requires_gr = bool(template.requires_gr)

        contract_payload = None
        if contract is not None:
            contract_payload = {
                "contract_id": str(contract.id),
                "contract_no": contract.contract_no,
                "total_amount": _as_float(contract.total_amount),
                "payment_terms": contract.payment_terms or {},
                "delivery_terms": contract.delivery_terms or {},
            }

        po_payload = {
            "po_id": str(po.id),
            "po_no": po.po_no,
            "status": po.status,
            "total_amount_tax_incl": _as_float(po.total_amount_tax_incl),
            "lines": [
                {
                    "line_id": str(line.id),
                    "line_no": line.line_no,
                    "item_desc": line.item_desc,
                    "spec_bucket": line.spec_bucket,
                    "qty_ordered": _as_float(line.qty_ordered),
                    "qty_received": _as_float(line.qty_received),
                    "qty_invoiced": _as_float(line.qty_invoiced),
                    "unit_price_tax_excl": _as_float(line.unit_price_tax_excl),
                    "tax_rate": _as_float(line.tax_rate),
                    "amount_tax_incl": _as_float(line.amount_tax_incl),
                    "promised_date": _as_date_str(line.promised_date),
                }
                for line in po_lines
            ],
        }
        invoice_payload = {
            "invoice_id": str(invoice.id),
            "invoice_no": invoice.invoice_no,
            "invoice_date": _as_date_str(invoice.invoice_date),
            "amount_tax_excl": _as_float(invoice.amount_tax_excl),
            "tax_amount": _as_float(invoice.tax_amount),
            "amount_tax_incl": _as_float(invoice.amount_tax_incl),
            "remark": invoice.remark,
            "lines": [
                {
                    "line_id": str(line.id),
                    "line_no": line.line_no,
                    "item_desc": line.item_desc,
                    "spec_bucket": line.spec_bucket,
                    "qty": _as_float(line.qty),
                    "unit_price_tax_excl": _as_float(line.unit_price_tax_excl),
                    "tax_rate": _as_float(line.tax_rate),
                    "amount_tax_excl": _as_float(line.amount_tax_excl),
                    "amount_tax_incl": _as_float(line.amount_tax_incl),
                }
                for line in invoice_lines
            ],
        }

    return {
        "po_id": str(po.id),
        "contract_id": str(po.contract_id) if po.contract_id else None,
        "invoice_ids": [str(invoice.id)],
        "documents": documents,
        "po": po_payload,
        "invoice": invoice_payload,
        "contract": contract_payload,
        "receipts": receipts,
        "policies": policies,
        "requires_gr": requires_gr,
        "category_code": category_code,
    }


def normalize_lines(state: MatchingState, deps: MatchingDeps) -> dict[str, Any]:
    po = state["po"]
    invoice = state["invoice"]
    category_code = state.get("category_code")
    for line in po["lines"]:
        if not line.get("spec_bucket") or line["spec_bucket"] == "unknown/unknown":
            line["spec_bucket"] = normalize(
                line["item_desc"], category_code=category_code
            ).spec_bucket
    for line in invoice["lines"]:
        if not line.get("spec_bucket"):
            line["spec_bucket"] = normalize(
                line["item_desc"], category_code=category_code
            ).spec_bucket
    return {"po": po, "invoice": invoice}


def align_lines(state: MatchingState, deps: MatchingDeps) -> dict[str, Any]:
    po_lines = [
        {
            "line_no": line["line_no"],
            "item_desc": line["item_desc"],
            "spec_bucket": line.get("spec_bucket"),
        }
        for line in state["po"]["lines"]
    ]
    invoice_lines = [
        {
            "line_no": line["line_no"],
            "item_desc": line["item_desc"],
            "spec_bucket": line.get("spec_bucket"),
        }
        for line in state["invoice"]["lines"]
    ]
    result = align_hungarian(po_lines, invoice_lines)
    return {
        "align": {
            "matches": [
                {
                    "po_line_no": match.po_line_no,
                    "invoice_line_no": match.invoice_line_no,
                    "method": match.method,
                    "score": match.score,
                    "ambiguous": match.ambiguous,
                }
                for match in result.matches
            ],
            "unmatched_po": result.unmatched_po,
            "unmatched_invoice": result.unmatched_invoice,
        }
    }


def reconcile_document(state: MatchingState, deps: MatchingDeps) -> dict[str, Any]:
    po = state["po"]
    invoice = state["invoice"]
    align_state = state["align"]
    contracts = state.get("contract")
    if contracts is not None:
        contract_rows = {
            "payment_terms": contracts.get("payment_terms") or {},
        }
    else:
        contract_rows = None

    align_result = AlignResult(
        matches=[LineMatch(**match) for match in align_state["matches"]],
        unmatched_po=list(align_state["unmatched_po"]),
        unmatched_invoice=list(align_state["unmatched_invoice"]),
    )
    result = reconcile(
        po=po,
        invoice=invoice,
        align=align_result,
        contract=contract_rows,
        receipts=[dict(receipt) for receipt in state.get("receipts", [])],
        policies=policies_from_rows(state.get("policies", [])),
        requires_gr=bool(state.get("requires_gr", True)),
        category_code=state.get("category_code"),
    )
    return {
        "result": {
            "overall": result.overall,
            "proposed_tail": result.proposed_tail,
            "tolerance_applied": result.tolerance_applied,
            "contract_absent": result.contract_absent,
            "diffs": [
                {
                    "diff_type": diff.diff_type,
                    "severity": diff.severity,
                    "po_line_no": diff.po_line_no,
                    "invoice_line_no": diff.invoice_line_no,
                    "expected": diff.expected,
                    "actual": diff.actual,
                    "delta": diff.delta,
                    "rule_ref": diff.rule_ref,
                    "suggestion": diff.suggestion,
                    "detail": diff.detail,
                }
                for diff in result.diffs
            ],
            "line_matches": [
                {
                    "po_line_no": line.po_line_no,
                    "invoice_line_no": line.invoice_line_no,
                    "method": line.method,
                    "score": line.score,
                    "consumed_qty": line.consumed_qty,
                    "remaining_qty": line.remaining_qty,
                    "status": line.status,
                    "ambiguous": line.ambiguous,
                }
                for line in result.line_matches
            ],
        }
    }


def compose_report(state: MatchingState, deps: MatchingDeps) -> dict[str, Any]:
    po = state["po"]
    invoice = state["invoice"]
    result = state["result"]
    proposed = [
        {
            "target": f"po_line:{line['po_line_no']}",
            "field": "qty_invoiced",
            "delta": line["consumed_qty"],
        }
        for line in result["line_matches"]
        if line.get("invoice_line_no") is not None and line["consumed_qty"]
    ]
    if abs(result["proposed_tail"]) >= 0.005:
        proposed.append({"target": f"po:{po['po_no']}", "field": "tail_diff_pool", "delta": result["proposed_tail"]})
    report = {
        "module": "m3",
        "graph_version": GRAPH_VERSION,
        "review_kind": "release_or_hold",
        "requires_review": True,
        "editable": False,
        "po_no": po["po_no"],
        "invoice_no": invoice["invoice_no"],
        "category_code": state.get("category_code"),
        "requires_gr": state.get("requires_gr"),
        "overall": result["overall"],
        "diffs": result["diffs"],
        "line_matches": result["line_matches"],
        "proposed_tail": result["proposed_tail"],
        "tolerance_applied": result["tolerance_applied"],
        "proposed_changes": proposed,
        # 前端对齐表/PDF 抽屉需要的原始行数据与文档 id（Spec §9.4）
        "documents": state.get("documents") or {},
        "po_total": po.get("total_amount_tax_incl"),
        "invoice_total": invoice.get("amount_tax_incl"),
        "po_lines": [
            {
                "line_no": line["line_no"],
                "item_desc": line["item_desc"],
                "spec_bucket": line.get("spec_bucket"),
                "qty_ordered": line["qty_ordered"],
                "qty_received": line.get("qty_received"),
                "qty_invoiced": line.get("qty_invoiced"),
                "unit_price_tax_excl": line["unit_price_tax_excl"],
                "tax_rate": line.get("tax_rate"),
                "amount_tax_incl": line.get("amount_tax_incl"),
                "promised_date": line.get("promised_date"),
            }
            for line in po["lines"]
        ],
        "invoice_lines": [
            {
                "line_no": line["line_no"],
                "item_desc": line["item_desc"],
                "spec_bucket": line.get("spec_bucket"),
                "qty": line["qty"],
                "unit_price_tax_excl": line.get("unit_price_tax_excl"),
                "tax_rate": line.get("tax_rate"),
                "amount_tax_excl": line.get("amount_tax_excl"),
                "amount_tax_incl": line.get("amount_tax_incl"),
            }
            for line in invoice["lines"]
        ],
        "diff_count": len(result["diffs"]),
        "multi_invoice": False,  # W1 限制：单张发票；W2 扩展多张（Spec §7.4）
        "summary": (
            f"{po['po_no']} ↔ {invoice['invoice_no']}：{result['overall']}，"
            f"{len(result['diffs'])} 项差异、拟核销 {len(proposed)} 处"
        ),
    }
    return {"report": report}


async def _finalize(state: MatchingState, deps: MatchingDeps) -> dict[str, Any]:
    run_id = UUID(state["run_id"])
    decision = state.get("decision") or "reject"
    release = decision in RELEASE_DECISIONS
    report = state["report"]
    result = state["result"]
    po = state["po"]
    invoice = state["invoice"]
    review_id = UUID(state["review_id"]) if state.get("review_id") else None

    po_lines = {line["line_no"]: line for line in po["lines"]}
    invoice_lines = {line["line_no"]: line for line in invoice["lines"]}

    async with deps.session_factory() as session:
        # 幂等：同一 run 的 finalize 重入不重复核销（以 match_run 审计记录为准）
        existing = await session.scalar(
            select(t.AuditLog.id).where(
                t.AuditLog.run_id == run_id,
                t.AuditLog.target_table == "match_run",
            )
        )
        if existing is not None:
            agent_run = await session.get(t.AgentRun, run_id)
            if agent_run is not None and agent_run.status != "succeeded":
                agent_run.status = "succeeded"
                agent_run.finished_at = datetime.now(UTC)
                await session.commit()
            return {"finalized": True, "artifact_id": None}

        match_run = t.MatchRun(
            po_id=UUID(state["po_id"]),
            contract_id=UUID(state["contract_id"]) if state.get("contract_id") else None,
            invoice_ids=list(state.get("invoice_ids") or []),
            status="matched" if release else "hold",
            overall=report["overall"],
            tolerance_snapshot=_policy_snapshot(state.get("policies", [])),
            tail_diff=Decimal(str(result["proposed_tail"])),
        )
        session.add(match_run)
        await session.flush()

        for match in result["line_matches"]:
            po_line = po_lines.get(match["po_line_no"])
            invoice_line = invoice_lines.get(match["invoice_line_no"] or -1)
            if po_line is None:
                continue
            if invoice_line is None and match.get("status") != "unmatched":
                continue
            session.add(
                t.MatchLine(
                    run_id=match_run.id,
                    po_line_id=UUID(po_line["line_id"]),
                    invoice_line_id=UUID(invoice_line["line_id"]) if invoice_line else None,
                    method=match.get("method", "unmatched"),
                    score=Decimal(str(match.get("score", 0))),
                    consumed_qty=Decimal(str(match.get("consumed_qty", 0))),
                    remaining_qty=Decimal(str(match.get("remaining_qty", 0))),
                    status=match.get("status", "unmatched"),
                    ambiguous=bool(match.get("ambiguous", False)),
                )
            )

        for diff in result["diffs"]:
            po_line = po_lines.get(diff.get("po_line_no") or -1)
            invoice_line = invoice_lines.get(diff.get("invoice_line_no") or -1)
            session.add(
                t.MatchDiff(
                    run_id=match_run.id,
                    diff_type=diff["diff_type"],
                    severity=diff["severity"],
                    po_line_id=UUID(po_line["line_id"]) if po_line else None,
                    invoice_line_id=UUID(invoice_line["line_id"]) if invoice_line else None,
                    expected=Decimal(str(diff["expected"])) if diff.get("expected") is not None else None,
                    actual=Decimal(str(diff["actual"])) if diff.get("actual") is not None else None,
                    delta=Decimal(str(diff["delta"])) if diff.get("delta") is not None else None,
                    rule_ref=diff["rule_ref"],
                    suggestion=diff.get("suggestion"),
                )
            )

        audit_after: dict[str, Any] = {
            "overall": report["overall"],
            "decision": decision,
            "proposed_tail": result["proposed_tail"],
        }
        if release:
            for match in result["line_matches"]:
                po_line = po_lines.get(match["po_line_no"])
                if po_line is None or not match.get("invoice_line_no") or not match.get("consumed_qty"):
                    continue
                row = await session.get(t.PoLine, UUID(po_line["line_id"]))
                if row is None:
                    continue
                before = _as_float(row.qty_invoiced)
                row.qty_invoiced = Decimal(str(before + float(match["consumed_qty"])))
                audit_after[f"po_line:{match['po_line_no']}"] = {
                    "qty_invoiced_before": before,
                    "qty_invoiced_after": float(row.qty_invoiced),
                }
                session.add(
                    t.AuditLog(
                        run_id=run_id,
                        review_id=review_id,
                        actor_role="finance",
                        target_table="po_line",
                        target_id=row.id,
                        action="update",
                        before_json={"qty_invoiced": before},
                        after_json={"qty_invoiced": float(row.qty_invoiced)},
                        source="agent",
                        note="M3 放行：累加已开票数量",
                    )
                )

            invoice_row = await session.get(t.Invoice, UUID(invoice["invoice_id"]))
            if invoice_row is not None:
                invoice_row.status = "matched"
            if abs(result["proposed_tail"]) >= 0.005:
                pool = await session.scalar(select(t.TailDiffPool).where(t.TailDiffPool.po_id == UUID(state["po_id"])))
                if pool is None:
                    session.add(
                        t.TailDiffPool(
                            po_id=UUID(state["po_id"]),
                            amount=Decimal(str(result["proposed_tail"])),
                        )
                    )
                else:
                    pool.amount = Decimal(str(_as_float(pool.amount) + float(result["proposed_tail"])))
        else:
            invoice_row = await session.get(t.Invoice, UUID(invoice["invoice_id"]))
            if invoice_row is not None:
                invoice_row.status = "hold"

        session.add(
            t.AuditLog(
                run_id=run_id,
                review_id=review_id,
                actor_role="finance",
                target_table="match_run",
                target_id=match_run.id,
                action="release" if release else "hold",
                before_json=None,
                after_json=audit_after,
                source="agent",
                note=f"M3 人工决策：{decision}",
            )
        )

        artifact = t.Artifact(
            run_id=run_id,
            module="m3",
            kind="match_report",
            payload={
                "report": report,
                "decision": decision,
                "reviewer_note": state.get("reviewer_note"),
                "match_run_id": str(match_run.id),
            },
        )
        session.add(artifact)
        agent_run = await session.get(t.AgentRun, run_id)
        if agent_run is not None:
            agent_run.status = "succeeded"
            agent_run.finished_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(artifact)
        artifact_id = str(artifact.id)

    return {"finalized": True, "artifact_id": artifact_id}


# --------------------------------------------------------------------------- 图装配


def _instrumented(deps: MatchingDeps, name: str, fn) -> Any:
    """统一节点包装：发 node.start / node.end（含耗时），异常原样抛出交给 RunService 兜底。"""

    async def node(state: MatchingState) -> dict[str, Any]:
        run_id = UUID(state["run_id"])
        await deps.bus.emit(run_id, "node.start", {"node": name})
        started = perf_counter()
        result = fn(state, deps)
        if hasattr(result, "__await__"):
            updates = await result
        else:
            updates = result
        await deps.bus.emit(
            run_id,
            "node.end",
            {
                "node": name,
                "duration_ms": int((perf_counter() - started) * 1000),
                "llm_calls": 0,
                "tool_calls": 0,
            },
        )
        return updates or {}

    return node


def build_matching_graph(deps: MatchingDeps):
    """编译 M3 图：finalize 前挂起（HITL），内存 checkpointer 支持同进程 resume。"""
    graph = StateGraph(MatchingState)
    graph.add_node("load_subject", _instrumented(deps, "load_subject", load_subject))
    graph.add_node("normalize", _instrumented(deps, "normalize", normalize_lines))
    graph.add_node("align", _instrumented(deps, "align", align_lines))
    graph.add_node("reconcile", _instrumented(deps, "reconcile", reconcile_document))
    graph.add_node("compose_report", _instrumented(deps, "compose_report", compose_report))
    graph.add_node("finalize", _instrumented(deps, "finalize", _finalize))

    graph.add_edge(START, "load_subject")
    graph.add_edge("load_subject", "normalize")
    graph.add_edge("normalize", "align")
    graph.add_edge("align", "reconcile")
    graph.add_edge("reconcile", "compose_report")
    graph.add_edge("compose_report", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=MemorySaver(), interrupt_before=["finalize"])
