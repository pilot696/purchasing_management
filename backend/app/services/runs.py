"""P0 演示用运行服务：真实落地「建 run → 发事件 → HITL 中断 → resume → finalize」。

W1 起由 LangGraph 图替代 `_execute`（保留同一套 EventBus / ReviewTask / Artifact 契约），
因此这里刻意把"编排"与"事件/复核/事务"分层：后续只替换节点序列，不动外围。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ReviewAlreadyDecided, RunNotFound, RunStateConflict
from app.models.tables import AgentRun, Artifact, ReviewTask
from app.observability.runevents import EventBus

TERMINAL_STATUS: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
MODULE_ARTIFACT_KIND = {"m1": "precheck", "m2": "supplier_eval", "m3": "match_report"}
DECISION_TO_REVIEW_STATUS = {"accept": "accepted", "reject": "rejected", "edit": "edited"}

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
            self._handles.pop(run_id, None)

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

