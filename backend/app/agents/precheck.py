"""M1 采购申请预检图（Spec §5.2 / §5.4）：规则引擎 + 混合检索 + 语义消歧 + HITL 补字段。

节点链路：load_subject → retrieve → rules_engine → semantic_resolve → compose_report →（interrupt）→ finalize
- 决策前只写分析结果；`pr_item.spec` 的补丁只在 finalize（accept/edit）事务里写；
- 检索证据（sku_catalog 候选）进入 finding.evidence 与补丁来源，便于前端证据面板展示。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any, TypedDict
from uuid import UUID

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.matching.normalize import normalize
from app.models import tables as t
from app.observability.runevents import EventBus
from app.retrieval.search import ambiguity_threshold, retrieve_sku_candidates
from app.rules.engine import RuleDef, evaluate_rules, verdict_from_findings
from app.rules.loader import load_rules_for_category

GRAPH_VERSION = "m1-precheck-v1"
SPECIAL_KEYS = {"owner", "cost_center"}


class PrecheckState(TypedDict, total=False):
    run_id: str
    subject: dict[str, Any]
    context: dict[str, Any]
    items: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    patches: list[dict[str, Any]]
    report: dict[str, Any]
    decision: str | None
    reviewer_note: str | None
    review_id: str | None
    edits: list[dict[str, Any]] | None
    finalized: bool
    artifact_id: str | None


@dataclass(slots=True)
class PrecheckDeps:
    bus: EventBus
    session_factory: async_sessionmaker[AsyncSession]


def _as_float(value: Any) -> float:
    return float(value) if value is not None else 0.0


def _as_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value)


async def load_pr_context(session: AsyncSession, pr_no: str) -> dict[str, Any]:
    pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == pr_no))
    if pr is None:
        raise ValueError(f"PR 不存在：{pr_no}")
    items = (
        await session.scalars(select(t.PrItem).where(t.PrItem.pr_id == pr.id).order_by(t.PrItem.line_no))
    ).all()
    department = await session.get(t.Department, pr.department_id)
    requester = await session.get(t.AppUser, pr.requester_id)
    template = await session.scalar(
        select(t.CategoryTemplate).where(t.CategoryTemplate.category_code == pr.category_code)
    )
    budget = await session.scalar(
        select(t.Budget).where(
            t.Budget.department_id == pr.department_id,
            t.Budget.category_code == pr.category_code,
            t.Budget.fiscal_year == (pr.created_at.year if pr.created_at else 2026),
        )
    )
    available = 0.0
    if budget is not None:
        available = _as_float(budget.amount_total) - _as_float(budget.amount_used) - _as_float(budget.amount_committed)
    rules = await load_rules_for_category(session, pr.category_code)

    return {
        "pr": {
            "pr_id": str(pr.id),
            "pr_no": pr.pr_no,
            "title": pr.title,
            "category_code": pr.category_code,
            "status": pr.status,
            "estimated_amount": _as_float(pr.estimated_amount),
            "needed_by": _as_date(pr.needed_by),
            "requester_email": requester.email if requester else None,
            "department_name": department.name if department else None,
            "department_cost_center": department.cost_center if department else None,
        },
        "items": [
            {
                "pr_item_id": str(item.id),
                "line_no": item.line_no,
                "item_name_raw": item.item_name_raw,
                "spec": dict(item.spec or {}),
                "qty": _as_float(item.qty),
                "unit": item.unit,
                "unit_price_est": _as_float(item.unit_price_est),
                "amount_est": _as_float(item.amount_est),
                "spec_bucket": item.spec_bucket,
            }
            for item in items
        ],
        "budget_available": available,
        "template": {
            "category_code": template.category_code,
            "required_slots": list(template.required_slots or []),
            "acceptance_points": list(template.acceptance_points or []),
        }
        if template
        else {"category_code": pr.category_code, "required_slots": [], "acceptance_points": []},
        "rules": [
            {
                "code": rule.code,
                "type": rule.type,
                "severity": rule.severity,
                "field_path": rule.field_path,
                "expression": rule.expression,
                "message": rule.message,
                "policy_clause_ref": rule.policy_ref,
                "version": rule.version,
            }
            for rule in rules
        ],
    }


async def enrich_items(session: AsyncSession, context: dict[str, Any]) -> list[dict[str, Any]]:
    """归一化 + 混合检索：为每个 item 补 spec_bucket、候选 SKU、歧义标记。"""
    category = context["pr"]["category_code"]
    enriched: list[dict[str, Any]] = []
    for item in context["items"]:
        normalized = normalize(item["item_name_raw"], category_code=category)
        bucket = item.get("spec_bucket") or normalized.spec_bucket
        candidates = await retrieve_sku_candidates(
            session,
            item_name=item["item_name_raw"],
            spec_bucket=bucket,
            category_code=category,
            limit=3,
        )
        meaningful = {key: value for key, value in item["spec"].items() if key not in SPECIAL_KEYS}
        # 歧义判据用"从品名重算的规格桶"（DB 里可能残留历史 sku 的桶，不代表当前写法可检索）
        ambiguous = not meaningful and ambiguity_threshold(normalized.spec_bucket)
        enriched.append(
            {
                **item,
                "normalized_bucket": bucket,
                "family": normalized.family,
                "brand": normalized.brand,
                "candidates": [
                    {
                        "sku_code": hit.sku_code,
                        "name": hit.name,
                        "spec_bucket": hit.spec_bucket,
                        "score": hit.score,
                        "sources": list(hit.sources),
                        "spec": hit.spec,
                        "ref_price_p50": hit.ref_price_p50,
                    }
                    for hit in candidates
                ],
                "ambiguous": ambiguous,
            }
        )
    return enriched


def _rule_context(context: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    rule_items = []
    for item in items:
        spec = item["spec"]
        rule_items.append(
            {
                **item,
                "owner": spec.get("owner"),
                "cost_center": spec.get("cost_center"),
            }
        )
    return {
        "items": rule_items,
        "estimated_amount": context["pr"]["estimated_amount"],
        "budget_available": context["budget_available"],
    }


def evaluate_rule_findings(context: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = [
        RuleDef(
            code=rule["code"],
            type=rule["type"],
            severity=rule["severity"],
            field_path=rule["field_path"],
            expression=rule["expression"],
            message=rule["message"],
            policy_ref=rule["policy_clause_ref"],
            category_code=context["pr"]["category_code"],
            version=rule.get("version", 1),
        )
        for rule in context["rules"]
    ]
    return evaluate_rules(rules, _rule_context(context, items))


def _slot_defaults(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if not candidate:
        return {}
    spec = dict(candidate.get("spec") or {})
    defaults: dict[str, Any] = {}
    for key in ("memory_gb", "storage_gb", "cpu", "size_inch", "period_months", "seats", "duration_days"):
        if key in spec:
            defaults[key] = spec[key]
    return defaults


def semantic_resolve(
    context: dict[str, Any],
    items: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """语义消歧 + 补字段建议（返回 findings, patches, enriched items 更新）。"""
    findings = list(findings)
    patches: list[dict[str, Any]] = []
    required_slots = list(context["template"].get("required_slots") or [])
    requester = context["pr"].get("requester_email") or ""
    cost_center = context["pr"].get("department_cost_center")
    department_name = context["pr"].get("department_name")

    for index, item in enumerate(items):
        top = item["candidates"][0] if item["candidates"] else None
        if item["ambiguous"]:
            findings = [
                finding
                for finding in findings
                if not (
                    finding["type"] == "required" and (finding.get("field") or "").startswith(f"/items/{index}/")
                )
            ]
            findings.append(
                {
                    "rule_code": "SEMANTIC-AMBIGUITY",
                    "type": "ambiguity",
                    "severity": "high",
                    "field": f"/items/{index}/item_name",
                    "message": f"品名过于笼统（{item['item_name_raw']}），缺少可检索的规格信息，需澄清",
                    "evidence": (
                        [{"kind": "catalog", "ref": top["sku_code"], "snippet": top["name"]}] if top else []
                    ),
                }
            )
            defaults = _slot_defaults(top)
            for key, value in defaults.items():
                patches.append(
                    {
                        "op": "add",
                        "path": f"/items/{index}/spec/{key}",
                        "value": value,
                        "from": None,
                        "source": "catalog_sku",
                        "ref": top["sku_code"] if top else None,
                    }
                )
            if top is not None:
                patches.append(
                    {
                        "op": "replace",
                        "path": f"/items/{index}/item_name_raw",
                        "value": top["name"],
                        "from": item["item_name_raw"],
                        "source": "catalog_sku",
                        "ref": top["sku_code"],
                    }
                )

        spec = item["spec"]
        for slot in required_slots:
            if slot.startswith("spec."):
                key = slot[5:]
                if spec.get(key) not in (None, ""):
                    continue
                candidate_value = _slot_defaults(top).get(key)
                patches.append(
                    {
                        "op": "add",
                        "path": f"/items/{index}/spec/{key}",
                        "value": candidate_value,
                        "from": None,
                        "source": "catalog_sku" if candidate_value is not None else "manual_required",
                        "ref": top["sku_code"] if candidate_value is not None and top else None,
                    }
                )
            elif slot == "owner" and not spec.get("owner"):
                patches.append(
                    {
                        "op": "add",
                        "path": f"/items/{index}/spec/owner",
                        "value": requester.split("@")[0] if requester else None,
                        "from": None,
                        "source": "requester_profile",
                    }
                )
            elif slot == "cost_center" and not spec.get("cost_center"):
                patches.append(
                    {
                        "op": "add",
                        "path": f"/items/{index}/spec/cost_center",
                        "value": cost_center,
                        "from": None,
                        "source": "department_budget" if cost_center else "manual_required",
                    }
                )
            elif slot == "unit" and not item.get("unit"):
                patches.append(
                    {
                        "op": "add",
                        "path": f"/items/{index}/unit",
                        "value": "台",
                        "from": None,
                        "source": "manual_required",
                    }
                )
            elif slot == "delivery_address":
                patches.append(
                    {
                        "op": "add",
                        "path": f"/items/{index}/spec/delivery_address",
                        "value": department_name,
                        "from": None,
                        "source": "department_profile" if department_name else "manual_required",
                    }
                )
    return findings, patches, items


def compose_report(
    context: dict[str, Any],
    items: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    patches: list[dict[str, Any]],
) -> dict[str, Any]:
    pr = context["pr"]
    verdict = verdict_from_findings(findings)
    requires_review = bool(findings or patches)
    return {
        "module": "m1",
        "graph_version": GRAPH_VERSION,
        "review_kind": "apply_patch",
        "editable": True,
        "pr_no": pr["pr_no"],
        "title": pr["title"],
        "category_code": pr["category_code"],
        "verdict": verdict,
        "findings": findings,
        "patches": patches,
        "items": [
            {
                "line_no": item["line_no"],
                "item_name_raw": item["item_name_raw"],
                "spec": item["spec"],
                "spec_bucket": item["normalized_bucket"],
                "qty": item["qty"],
                "unit": item["unit"],
                "candidates": item["candidates"],
                "ambiguous": item["ambiguous"],
            }
            for item in items
        ],
        "budget": {
            "available": context["budget_available"],
            "estimated": pr["estimated_amount"],
            "over_budget": pr["estimated_amount"] > context["budget_available"],
        },
        "requires_review": requires_review,
        "questions": (
            ["请确认补全后的规格与使用人是否正确，确认后将写回 PR 明细。"] if requires_review else []
        ),
        "summary": (
            f"{pr['pr_no']} 预检：{verdict}，{len(findings)} 个问题、{len(patches)} 条建议补丁"
            if requires_review
            else f"{pr['pr_no']} 预检通过，无需人工处理"
        ),
        "patch_count": len(patches),
        "finding_count": len(findings),
    }


def _path_parts(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for chunk in path.strip("/").split("/"):
        if chunk.isdigit():
            parts.append(int(chunk))
        elif chunk:
            parts.append(chunk)
    return parts


def sanitize_reviewer_edits(edits: list[dict[str, Any]], item_count: int) -> list[dict[str, Any]]:
    """把 HITL 的 edits 规整成可应用补丁：只允许 pr items 内、有值、路径合法的 patch。"""
    cleaned: list[dict[str, Any]] = []
    for patch in edits[:100]:
        if not isinstance(patch, dict):
            continue
        path = str(patch.get("path") or "")
        parts = _path_parts(path)
        if len(parts) < 3 or parts[0] != "items":
            continue
        # /items/<i>/spec 是整对象，不允许 reviewer 直接替换；spec 下必须有具体 key
        if parts[2] == "spec" and len(parts) < 4:
            continue
        index = parts[1]
        if not isinstance(index, int) or not (0 <= index < item_count):
            continue
        value = patch.get("value")
        if value in (None, ""):
            continue
        cleaned.append(
            {
                "op": "replace",
                "path": path,
                "value": value,
                "source": patch.get("source") or "reviewer",
                "ref": patch.get("ref"),
            }
        )
    return cleaned


def apply_patches(items: list[dict[str, Any]], patches: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """把补丁应用到内存 items（返回更新后的 items + 实际生效条数）。"""
    updated = [dict(item) for item in items]
    applied = 0
    for patch in patches:
        value = patch.get("value")
        if value in (None, ""):
            continue
        parts = _path_parts(str(patch["path"]))
        if not parts or parts[0] != "items":
            continue
        index = parts[1]
        if not isinstance(index, int) or index >= len(updated):
            continue
        target: Any = updated[index]
        for part in parts[2:-1]:
            if isinstance(part, int):
                continue
            target = target.setdefault(part, {}) if isinstance(target, dict) else target
        leaf = parts[-1]
        if isinstance(target, dict) and not isinstance(leaf, int):
            target[leaf] = value
            applied += 1
    return updated, applied


async def _finalize(state: PrecheckState, deps: PrecheckDeps) -> dict[str, Any]:
    run_id = UUID(state["run_id"])
    decision = state.get("decision") or "reject"
    release = decision in {"accept", "edit"}
    report = state["report"]
    review_id = UUID(state["review_id"]) if state.get("review_id") else None

    async with deps.session_factory() as session:
        existing = await session.scalar(
            select(t.AuditLog.id).where(t.AuditLog.run_id == run_id, t.AuditLog.target_table == "pr_item")
        )
        if existing is not None:
            agent_run = await session.get(t.AgentRun, run_id)
            if agent_run is not None and agent_run.status != "succeeded":
                agent_run.status = "succeeded"
                agent_run.finished_at = datetime.now(UTC)
                await session.commit()
            return {"finalized": True, "artifact_id": None}

        pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == report["pr_no"]))
        if pr is None:
            raise ValueError(f"PR 不存在：{report['pr_no']}")
        db_items = (
            await session.scalars(select(t.PrItem).where(t.PrItem.pr_id == pr.id).order_by(t.PrItem.line_no))
        ).all()
        applied = 0
        edits = state.get("edits")
        if edits is None:
            # 兼容旧客户端：未提供 edits 时按推荐补丁全量应用
            selected_patches = list(report.get("patches", []))
        else:
            # 新协议：[] 表示一条都不采纳；否则按勾选/改值后的补丁应用
            selected_patches = sanitize_reviewer_edits(edits, len(db_items))
        if release:
            memory_items = [{"pr_item_id": str(item.id), "spec": dict(item.spec or {})} for item in db_items]
            _, applied = apply_patches(memory_items, selected_patches)
            by_id = {str(item.id): item for item in db_items}
            merged: dict[str, dict[str, Any]] = {}
            for index, memory_item in enumerate(memory_items):
                db_item = by_id[memory_item["pr_item_id"]]
                before = dict(db_item.spec or {})
                if before != memory_item["spec"]:
                    db_item.spec = memory_item["spec"]
                    merged[str(db_item.id)] = {"before": before, "after": dict(memory_item["spec"])}
                    session.add(
                        t.AuditLog(
                            run_id=run_id,
                            review_id=review_id,
                            actor_role="buyer",
                            target_table="pr_item",
                            target_id=db_item.id,
                            action="apply_patch",
                            patch_json=selected_patches,
                            before_json=before,
                            after_json=dict(memory_item["spec"]),
                            source="agent",
                            note="M1 预检：补字段与规格写回",
                        )
                    )
            pr.status = "prechecked"
        else:
            pr.status = "returned"
            session.add(
                t.AuditLog(
                    run_id=run_id,
                    review_id=review_id,
                    actor_role="buyer",
                    target_table="pr",
                    target_id=pr.id,
                    action="hold",
                    before_json={"status": "submitted"},
                    after_json={"status": "returned"},
                    source="agent",
                    note="M1 预检：退回补充",
                )
            )

        artifact = t.Artifact(
            run_id=run_id,
            module="m1",
            kind="precheck",
            payload={
                "report": report,
                "decision": decision,
                "reviewer_note": state.get("reviewer_note"),
                "applied_patches": applied,
                "selected_patches": selected_patches,
                "requested_edits": edits,
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


def _instrumented(deps: PrecheckDeps, name: str, fn) -> Any:
    async def node(state: PrecheckState) -> dict[str, Any]:
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
                "llm_calls": 0,
                "tool_calls": 0,
            },
        )
        return updates or {}

    return node


async def _load_subject(state: PrecheckState, deps: PrecheckDeps) -> dict[str, Any]:
    pr_no = str(state["subject"].get("pr_no") or "")
    if not pr_no:
        raise ValueError("M1 运行需要 subject.pr_no")
    async with deps.session_factory() as session:
        context = await load_pr_context(session, pr_no)
    return {"context": context}


async def _retrieve(state: PrecheckState, deps: PrecheckDeps) -> dict[str, Any]:
    async with deps.session_factory() as session:
        items = await enrich_items(session, state["context"])
    return {"items": items}


def _rules(state: PrecheckState, deps: PrecheckDeps) -> dict[str, Any]:
    return {"findings": evaluate_rule_findings(state["context"], state["items"])}


def _semantic(state: PrecheckState, deps: PrecheckDeps) -> dict[str, Any]:
    findings, patches, items = semantic_resolve(state["context"], state["items"], state["findings"])
    return {"findings": findings, "patches": patches, "items": items}


def _compose(state: PrecheckState, deps: PrecheckDeps) -> dict[str, Any]:
    report = compose_report(state["context"], state["items"], state["findings"], state["patches"])
    return {"report": report}


def build_precheck_graph(deps: PrecheckDeps):
    graph = StateGraph(PrecheckState)
    graph.add_node("load_subject", _instrumented(deps, "load_subject", _load_subject))
    graph.add_node("retrieve", _instrumented(deps, "retrieve", _retrieve))
    graph.add_node("rules_engine", _instrumented(deps, "rules_engine", _rules))
    graph.add_node("semantic_resolve", _instrumented(deps, "semantic_resolve", _semantic))
    graph.add_node("compose_report", _instrumented(deps, "compose_report", _compose))
    graph.add_node("finalize", _instrumented(deps, "finalize", _finalize))

    graph.add_edge(START, "load_subject")
    graph.add_edge("load_subject", "retrieve")
    graph.add_edge("retrieve", "rules_engine")
    graph.add_edge("rules_engine", "semantic_resolve")
    graph.add_edge("semantic_resolve", "compose_report")
    graph.add_edge("compose_report", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=MemorySaver(), interrupt_before=["finalize"])


async def compute_precheck_report(session: AsyncSession, pr_no: str) -> dict[str, Any]:
    """评测/调试用：不经图，跑同一条确定性链路（load → retrieve → rules → semantic → compose）。"""
    context = await load_pr_context(session, pr_no)
    items = await enrich_items(session, context)
    findings = evaluate_rule_findings(context, items)
    findings, patches, items = semantic_resolve(context, items, findings)
    return compose_report(context, items, findings, patches)


