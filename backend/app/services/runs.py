"""P0 演示用运行服务：真实落地「建 run → 发事件 → HITL 中断 → resume → finalize」。

W1 起由 LangGraph 图替代 `_execute`（保留同一套 EventBus / ReviewTask / Artifact 契约），
因此这里刻意把"编排"与"事件/复核/事务"分层：后续只替换节点序列，不动外围。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import median
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ReviewAlreadyDecided, RunNotFound, RunStateConflict
from app.models.tables import (
    AgentRun,
    Artifact,
    CategoryTemplate,
    LlmCall,
    ReviewTask,
    Rule,
    RunEvent,
    TolerancePolicy,
)
from app.observability.runevents import EventBus

TERMINAL_STATUS: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
MODULE_ARTIFACT_KIND = {"m1": "precheck", "m2": "supplier_eval", "m3": "match_report"}
DECISION_TO_REVIEW_STATUS = {"accept": "accepted", "reject": "rejected", "edit": "edited"}


def _duration_ms(run: AgentRun) -> int | None:
    if run.started_at is None or run.finished_at is None:
        return None
    return max(0, int((run.finished_at - run.started_at).total_seconds() * 1000))


def _run_item(run: AgentRun, review: ReviewTask | None = None) -> dict[str, Any]:
    return {
        "id": run.id,
        "module": run.module,
        "status": run.status,
        "subject_ref": run.subject_ref or {},
        "last_seq": run.last_seq,
        "tokens_in": run.tokens_in,
        "tokens_out": run.tokens_out,
        "cost_cny": float(run.cost_cny or 0),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": _duration_ms(run),
        "error": run.error,
        "pending_review_id": review.id if review else None,
        "pending_review_kind": review.kind if review else None,
    }


def _review_preview(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """复核任务 payload → 收件箱摘要（module/verdict/summary/details）。"""
    report = payload.get("report") or {}
    module = report.get("module") or ""
    verdict = report.get("verdict") or report.get("overall")
    summary = report.get("summary")
    details: dict[str, Any] = {}
    if module == "m2":
        details = {
            "supplier": report.get("supplier_name") or report.get("supplier_code"),
            "total": report.get("total"),
            "confidence": report.get("confidence"),
            "gate_hits": len(report.get("gate_hits") or []),
        }
    elif module == "m1":
        details = {
            "pr_no": report.get("pr_no"),
            "findings": len(report.get("findings") or []),
            "patches": len(report.get("patches") or []),
        }
    elif module == "m3":
        details = {
            "po_no": report.get("po_no"),
            "invoice_no": report.get("invoice_no"),
            "diffs": len(report.get("diffs") or []),
        }
    if not summary:
        if module == "m2":
            summary = f"{details.get('supplier') or '供应商'} · 总分 {details.get('total')} · {verdict}"
        elif module == "m1":
            summary = f"{details.get('pr_no')} · {len(details.get('patches') or [])} 条补丁 · {verdict}"
        elif module == "m3":
            summary = f"{details.get('po_no')} · 差异 {details.get('diffs')} 条 · {verdict}"
        else:
            summary = verdict
    return {"verdict": verdict, "summary": summary, "details": details, "kind": kind}

# P0 的假节点序列（真实节点在 §5.2 / §6.2 / §7.6 中定义）
DEMO_NODES = ("load_subject", "rules_engine", "compose_result")


@dataclass
class _Handle:
    run_id: UUID
    resume_event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: str | None = None
    edits: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None
    review_id: UUID | None = None
    task: asyncio.Task[None] | None = None


class RunService:
    def __init__(
        self,
        bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        step_delay_ms: int = 350,
        resume_timeout_s: float = 600,
        sleep=asyncio.sleep,
        matching_graph: Any | None = None,
        precheck_graph: Any | None = None,
        supplier_graph: Any | None = None,
    ) -> None:
        self._bus = bus
        self._sf = session_factory
        self._step_delay_s = step_delay_ms / 1000
        self._resume_timeout_s = resume_timeout_s
        self._sleep = sleep
        self._matching = matching_graph
        self._precheck = precheck_graph
        self._supplier = supplier_graph
        self._handles: dict[UUID, _Handle] = {}

    # ------------------------------------------------------------------ 生命周期

    async def start(self, module: str, subject: dict[str, Any]) -> dict[str, Any]:
        run_id = uuid.uuid4()
        async with self._sf() as session:
            session.add(
                AgentRun(
                    id=run_id,
                    module=module,
                    subject_ref=subject,
                    thread_id=str(run_id),
                    status="queued",
                )
            )
            await session.commit()

        handle = _Handle(run_id=run_id)
        self._handles[run_id] = handle
        await self._bus.emit(run_id, "run.start", {"run_id": str(run_id), "module": module})
        handle.task = asyncio.create_task(self._execute(handle, module, subject))
        return {
            "run_id": str(run_id),
            "thread_id": str(run_id),
            "status": "queued",
            "sse_url": f"/api/v1/runs/{run_id}/events",
        }

    async def _execute(self, handle: _Handle, module: str, subject: dict[str, Any]) -> None:
        run_id = handle.run_id
        # M1/M2/M3 均由 LangGraph 图驱动（M2 的闸门命中路径会跳过 LLM）
        graph = {"m1": self._precheck, "m2": self._supplier, "m3": self._matching}.get(module)
        if graph is not None:
            await self._execute_graph(handle, subject, graph)
            return
        try:
            await self._set_status(run_id, "running")
            for node in DEMO_NODES:
                await self._bus.emit(run_id, "node.start", {"node": node})
                started = perf_counter()
                await self._sleep(self._step_delay_s)
                await self._bus.emit(
                    run_id,
                    "node.end",
                    {
                        "node": node,
                        "duration_ms": int((perf_counter() - started) * 1000),
                        "llm_calls": 0,
                        "tool_calls": 0,
                    },
                )

            review_payload = {
                "kind": "apply_patch",
                "review_id": "",
                "title": f"确认 {module.upper()} 结论（P0 演示）",
                "patch": [
                    {"op": "replace", "path": "/items/0/spec/memory_gb", "from": None, "to": 16}
                ],
                "questions": ["该批设备的最终使用人与成本中心是？"],
                "editable": True,
                "subject": subject,
            }
            review_id = uuid.uuid4()
            review_payload["review_id"] = str(review_id)
            async with self._sf() as session:
                session.add(
                    ReviewTask(
                        id=review_id,
                        run_id=run_id,
                        kind="apply_patch",
                        payload=review_payload,
                        status="pending",
                    )
                )
                await session.commit()
            await self._set_status(run_id, "awaiting_review")
            await self._bus.emit(run_id, "interrupt", review_payload)

            try:
                await asyncio.wait_for(handle.resume_event.wait(), timeout=self._resume_timeout_s)
            except TimeoutError:
                await self._fail(run_id, "REVIEW_TIMEOUT", "等待人工复核超时")
                return

            await self._bus.emit(run_id, "node.start", {"node": "finalize"})
            started = perf_counter()
            artifact = await self._finalize(run_id, module, handle)
            await self._bus.emit(
                run_id,
                "node.end",
                {
                    "node": "finalize",
                    "duration_ms": int((perf_counter() - started) * 1000),
                    "llm_calls": 0,
                    "tool_calls": 0,
                },
            )
            await self._bus.emit(run_id, "run.end", {"status": "succeeded", "artifact_id": str(artifact)})
        except asyncio.CancelledError:  # pragma: no cover - 由 cancel() 触发
            await self._set_status(run_id, "cancelled", finished=True)
            await self._bus.emit(run_id, "run.end", {"status": "cancelled"})
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底转成可观测失败态，绝不静默
            await self._fail(run_id, "RUN_FAILED", str(exc))
        finally:
            self._handles.pop(run_id, None)

    async def _execute_graph(self, handle: _Handle, subject: dict[str, Any], graph: Any) -> None:
        """M1/M3 共用的图运行：跑到 interrupt_before(finalize) → HITL（或自动放行）→ finalize。"""
        run_id = handle.run_id
        config = {"configurable": {"thread_id": str(run_id)}}
        try:
            await self._set_status(run_id, "running")
            state = await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)
            report = state.get("report") or {}
            if not report.get("requires_review", True):
                # 无问题且无需补字段：跳过人工，直接 finalize（仍留下 artifact 与事件）
                await graph.aupdate_state(
                    config, {"decision": "accept", "reviewer_note": "auto: 无需人工", "review_id": None}
                )
                final_state = await graph.ainvoke(None, config)
                await self._bus.emit(
                    run_id,
                    "run.end",
                    {
                        "status": "succeeded",
                        "artifact_id": final_state.get("artifact_id"),
                        "auto": True,
                    },
                )
                return

            review_id = uuid.uuid4()
            kind = str(report.get("review_kind") or "apply_patch")
            review_payload = {
                "kind": kind,
                "review_id": str(review_id),
                "title": (
                    f"M1 采购预检：{report.get('pr_no')}"
                    if report.get("module") == "m1"
                    else f"M3 三单匹配：{report.get('po_no')} ↔ {report.get('invoice_no')}"
                ),
                "overall": report.get("verdict") or report.get("overall"),
                "summary": report.get("summary"),
                "report": report,
                "findings": report.get("findings", []),
                "patches": report.get("patches", []),
                "diffs": report.get("diffs", []),
                "line_matches": report.get("line_matches", []),
                "proposed_changes": report.get("proposed_changes", []),
                "questions": report.get("questions", []),
                "editable": bool(report.get("editable", False)),
                "subject": subject,
            }
            handle.review_id = review_id
            async with self._sf() as session:
                session.add(
                    ReviewTask(
                        id=review_id,
                        run_id=run_id,
                        kind=kind,
                        payload=review_payload,
                        status="pending",
                    )
                )
                await session.commit()
            await self._set_status(run_id, "awaiting_review")
            await self._bus.emit(run_id, "interrupt", review_payload)

            try:
                await asyncio.wait_for(handle.resume_event.wait(), timeout=self._resume_timeout_s)
            except TimeoutError:
                await self._fail(run_id, "REVIEW_TIMEOUT", "等待人工复核超时")
                return

            await graph.aupdate_state(
                config,
                {
                    "decision": handle.decision,
                    "reviewer_note": handle.note,
                    "review_id": str(review_id),
                },
            )
            final_state = await graph.ainvoke(None, config)
            await self._bus.emit(
                run_id,
                "run.end",
                {"status": "succeeded", "artifact_id": final_state.get("artifact_id")},
            )
        except asyncio.CancelledError:  # pragma: no cover - 由 cancel() 触发
            await self._set_status(run_id, "cancelled", finished=True)
            await self._bus.emit(run_id, "run.end", {"status": "cancelled"})
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底转成可观测失败态
            await self._fail(run_id, "RUN_FAILED", str(exc))
        finally:
            await self._sync_run_usage(run_id)
            self._handles.pop(run_id, None)

    async def _sync_run_usage(self, run_id: UUID) -> None:
        """把 llm_call 的 token/成本汇总回 agent_run（可观测页与列表展示用）。"""
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(LlmCall.tokens_in), 0),
                        func.coalesce(func.sum(LlmCall.tokens_out), 0),
                        func.coalesce(func.sum(LlmCall.cost_cny), 0),
                    ).where(LlmCall.run_id == run_id)
                )
            ).one()
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            run.tokens_in = int(row[0] or 0)
            run.tokens_out = int(row[1] or 0)
            run.cost_cny = row[2] or 0
            await session.commit()

    async def _finalize(self, run_id: UUID, module: str, handle: _Handle) -> UUID:
        artifact_id = uuid.uuid4()
        async with self._sf() as session:
            session.add(
                Artifact(
                    id=artifact_id,
                    run_id=run_id,
                    module=module,
                    kind=MODULE_ARTIFACT_KIND.get(module, "unknown"),
                    payload={
                        "demo": True,
                        "decision": handle.decision,
                        "edits": handle.edits,
                        "note": handle.note,
                    },
                )
            )
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RunNotFound(details={"run_id": str(run_id)})
            run.status = "succeeded"
            run.finished_at = datetime.now(UTC)
            await session.commit()
        return artifact_id

    async def _fail(self, run_id: UUID, code: str, message: str) -> None:
        await self._bus.emit(run_id, "error", {"code": code, "message": message, "retryable": False})
        await self._set_status(run_id, "failed", finished=True, error={"code": code, "message": message})
        await self._bus.emit(run_id, "run.end", {"status": "failed", "error_code": code})

    async def _set_status(
        self,
        run_id: UUID,
        status: str,
        *,
        finished: bool = False,
        error: dict[str, Any] | None = None,
    ) -> None:
        async with self._sf() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RunNotFound(details={"run_id": str(run_id)})
            run.status = status
            if finished:
                run.finished_at = datetime.now(UTC)
            if error is not None:
                run.error = error
            await session.commit()

    # ------------------------------------------------------------------ HITL / 操作

    async def resume(
        self,
        run_id: UUID,
        *,
        review_id: UUID,
        decision: str,
        edits: list[dict[str, Any]],
        note: str | None,
    ) -> None:
        handle = self._handles.get(run_id)
        if handle is None:
            raise RunStateConflict("运行不在当前进程内（P0 已知限制；W1 起由 checkpointer 恢复）")
        async with self._sf() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RunNotFound(details={"run_id": str(run_id)})
            if run.status != "awaiting_review":
                raise RunStateConflict(details={"status": run.status})
            review = await session.get(ReviewTask, review_id)
            if review is None or review.run_id != run_id:
                raise RunNotFound("复核任务不存在", details={"review_id": str(review_id)})
            if review.status != "pending":
                raise ReviewAlreadyDecided(details={"status": review.status})
            review.status = DECISION_TO_REVIEW_STATUS[decision]
            review.note = note
            review.decided_at = datetime.now(UTC)
            run.status = "running"
            await session.commit()

        handle.decision = decision
        handle.edits = edits
        handle.note = note
        handle.resume_event.set()

    async def cancel(self, run_id: UUID) -> None:
        handle = self._handles.get(run_id)
        async with self._sf() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RunNotFound(details={"run_id": str(run_id)})
            if run.status in TERMINAL_STATUS:
                raise RunStateConflict(details={"status": run.status})
        if handle is not None and handle.task is not None:
            handle.task.cancel()
        else:
            await self._set_status(run_id, "cancelled", finished=True)
            await self._bus.emit(run_id, "run.end", {"status": "cancelled"})

    # ------------------------------------------------------------------ 查询

    async def review_context(self, review_id: UUID) -> tuple[UUID, str]:
        """复核任务 -> (run_id, module)，供 /reviews/{id}/decision 做角色校验。"""
        async with self._sf() as session:
            review = await session.get(ReviewTask, review_id)
            if review is None:
                raise RunNotFound("复核任务不存在", details={"review_id": str(review_id)})
            run = await session.get(AgentRun, review.run_id)
            if run is None:
                raise RunNotFound(details={"run_id": str(review.run_id)})
            return review.run_id, run.module

    async def snapshot(self, run_id: UUID) -> dict[str, Any]:
        async with self._sf() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RunNotFound(details={"run_id": str(run_id)})
            review = await session.scalar(
                select(ReviewTask)
                .where(ReviewTask.run_id == run_id, ReviewTask.status == "pending")
                .order_by(ReviewTask.created_at.desc())
                .limit(1)
            )
            artifact = await session.scalar(
                select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at.desc()).limit(1)
            )
            from app.schemas.runs import ReviewOut, RunOut

            return {
                "run": RunOut.from_row(run),
                "review": ReviewOut.from_row(review) if review else None,
                "artifact": (artifact.payload if artifact else None),
            }

    async def list_pending_reviews(self) -> list[ReviewTask]:
        async with self._sf() as session:
            rows = (
                await session.scalars(
                    select(ReviewTask).where(ReviewTask.status == "pending").order_by(ReviewTask.created_at.desc())
                )
            ).all()
        return list(rows)

    async def run_status(self, run_id: UUID) -> str:
        async with self._sf() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RunNotFound(details={"run_id": str(run_id)})
            return run.status

    # ------------------------------------------------------------------ 历史 / 收件箱 / Trace / 聚合

    async def list_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        module: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        conditions = []
        if module:
            conditions.append(AgentRun.module == module)
        if status:
            conditions.append(AgentRun.status == status)
        async with self._sf() as session:
            total = int(
                await session.scalar(select(func.count()).select_from(AgentRun).where(*conditions)) or 0
            )
            rows = (
                await session.scalars(
                    select(AgentRun)
                    .where(*conditions)
                    .order_by(AgentRun.started_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            reviews = (
                await session.scalars(
                    select(ReviewTask).where(
                        ReviewTask.run_id.in_([row.id for row in rows]), ReviewTask.status == "pending"
                    )
                )
            ).all() if rows else []
        pending = {row.run_id: row for row in reviews}
        return {
            "items": [_run_item(row, pending.get(row.id)) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def list_review_inbox(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = (
                await session.scalars(
                    select(ReviewTask)
                    .where(ReviewTask.status == "pending")
                    .order_by(ReviewTask.created_at.desc())
                    .limit(limit)
                )
            ).all()
            run_ids = [row.run_id for row in rows]
            runs = (
                await session.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))
            ).all() if run_ids else []
        runs_by_id = {run.id: run for run in runs}
        items: list[dict[str, Any]] = []
        for review in rows:
            run = runs_by_id.get(review.run_id)
            if run is None:
                continue
            preview = _review_preview(review.kind, review.payload or {})
            items.append(
                {
                    "id": review.id,
                    "run_id": review.run_id,
                    "kind": review.kind,
                    "status": review.status,
                    "created_at": review.created_at,
                    "run_module": run.module,
                    "run_status": run.status,
                    "subject_ref": run.subject_ref or {},
                    "verdict": preview["verdict"],
                    "summary": preview["summary"],
                    "details": preview["details"],
                }
            )
        return items

    async def trace(self, run_id: UUID) -> dict[str, Any]:
        async with self._sf() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RunNotFound(details={"run_id": str(run_id)})
            review = await session.scalar(
                select(ReviewTask)
                .where(ReviewTask.run_id == run_id, ReviewTask.status == "pending")
                .order_by(ReviewTask.created_at.desc())
                .limit(1)
            )
            events = (
                await session.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq))
            ).all()
            calls = (
                await session.scalars(select(LlmCall).where(LlmCall.run_id == run_id).order_by(LlmCall.id))
            ).all()
            artifacts = (
                await session.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at))
            ).all()
        return {
            "run": _run_item(run, review),
            "events": [
                {
                    "seq": event.seq,
                    "type": event.event_type,
                    "payload": event.payload or {},
                    "created_at": event.created_at,
                }
                for event in events
            ],
            "llm_calls": [
                {
                    "id": call.id,
                    "step": call.step,
                    "provider": call.provider,
                    "model": call.model,
                    "tokens_in": call.tokens_in,
                    "tokens_out": call.tokens_out,
                    "cost_cny": float(call.cost_cny or 0),
                    "latency_ms": call.latency_ms,
                    "retry_no": call.retry_no,
                    "cache_hit": call.cache_hit,
                    "schema_repair": call.schema_repair,
                    "ok": call.ok,
                    "error_code": call.error_code,
                    "created_at": call.created_at,
                }
                for call in calls
            ],
            "artifacts": [
                {"id": str(artifact.id), "module": artifact.module, "kind": artifact.kind}
                for artifact in artifacts
            ],
        }

    async def observability_summary(self) -> dict[str, Any]:
        async with self._sf() as session:
            runs = (await session.scalars(select(AgentRun))).all()
            pending_reviews = int(
                await session.scalar(
                    select(func.count()).select_from(ReviewTask).where(ReviewTask.status == "pending")
                )
                or 0
            )
            calls = (await session.scalars(select(LlmCall))).all()
        by_status: dict[str, int] = {}
        by_module: dict[str, int] = {}
        durations: list[int] = []
        for run in runs:
            by_status[run.status] = by_status.get(run.status, 0) + 1
            by_module[run.module] = by_module.get(run.module, 0) + 1
            duration = _duration_ms(run)
            if duration is not None:
                durations.append(duration)
        total = len(runs)
        succeeded = by_status.get("succeeded", 0)
        finished = succeeded + by_status.get("failed", 0) + by_status.get("cancelled", 0)
        cache_hits = sum(1 for call in calls if call.cache_hit)
        latencies = [call.latency_ms for call in calls if call.latency_ms]
        return {
            "runs_total": total,
            "by_status": by_status,
            "by_module": by_module,
            # 成功率只按终态运行计算：待复核/运行中不应拉低成功率
            "success_rate": round(succeeded / finished, 4) if finished else 0.0,
            "pending_reviews": pending_reviews,
            "tokens_in": sum(run.tokens_in or 0 for run in runs),
            "tokens_out": sum(run.tokens_out or 0 for run in runs),
            "cost_cny": round(sum(float(run.cost_cny or 0) for run in runs), 6),
            "p50_duration_ms": int(median(durations)) if durations else None,
            "p95_duration_ms": (
                int(sorted(durations)[min(len(durations) - 1, int(len(durations) * 0.95))]) if durations else None
            ),
            "llm_calls": len(calls),
            "avg_llm_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "cache_hit_rate": round(cache_hits / len(calls), 4) if calls else None,
        }

    async def rule_catalog(self, *, category_code: str | None = None) -> dict[str, Any]:
        async with self._sf() as session:
            categories = (await session.scalars(select(CategoryTemplate).order_by(CategoryTemplate.category_code))).all()
            conditions = []
            if category_code:
                conditions.append(CategoryTemplate.category_code == category_code)
            rules = (
                await session.scalars(
                    select(Rule)
                    .join(CategoryTemplate, Rule.category_template_id == CategoryTemplate.id)
                    .where(*conditions)
                    .order_by(CategoryTemplate.category_code, Rule.code)
                )
            ).all()
            tolerances = (
                await session.scalars(
                    select(TolerancePolicy)
                    .where(TolerancePolicy.enabled.is_(True))
                    .order_by(TolerancePolicy.category_code, TolerancePolicy.priority)
                )
            ).all()
        category_map = {category.id: category for category in categories}
        return {
            "categories": [
                {
                    "code": category.category_code,
                    "name": category.name,
                    "version": category.version,
                    "requires_gr": category.requires_gr,
                    "required_slots": (
                        list(category.required_slots.keys())
                        if isinstance(category.required_slots, dict)
                        else list(category.required_slots or [])
                    ),
                }
                for category in categories
            ],
            "rules": [
                {
                    "id": rule.id,
                    "category_code": category_map[rule.category_template_id].category_code if rule.category_template_id in category_map else "",
                    "category_name": category_map[rule.category_template_id].name if rule.category_template_id in category_map else "",
                    "code": rule.code,
                    "type": rule.type,
                    "severity": rule.severity,
                    "field_path": rule.field_path,
                    "expression": rule.expression or {},
                    "message_template": rule.message_template,
                    "policy_clause_ref": rule.policy_clause_ref,
                    "enabled": rule.enabled,
                    "version": rule.version,
                }
                for rule in rules
            ],
            "tolerances": [
                {
                    "id": policy.id,
                    "category_code": policy.category_code,
                    "diff_type": policy.diff_type,
                    "abs_tol": float(policy.abs_tol or 0),
                    "rel_tol": float(policy.rel_tol or 0),
                    "action": policy.action,
                    "priority": policy.priority,
                    "enabled": policy.enabled,
                }
                for policy in tolerances
            ],
        }


