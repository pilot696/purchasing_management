"""M3 matching graph 集成测试：HITL 挂起 → 决策 → finalize 事务（需 DB + seed）。"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.agents.matching import MatchingDeps, build_matching_graph
from app.db.session import get_session_factory
from app.models import tables as t
from app.observability.runevents import EventBus
from app.services.runs import RunService

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_1 = ("PO-2026-0001", "INV-2026-3000", "m3-0001")
CASE_2 = ("PO-2026-0002", "INV-2026-3001", "m3-0002")
CASE_3 = ("PO-2026-0003", "INV-2026-3002", "m3-0003")


def _gold(case_id: str) -> dict:
    path = PROJECT_ROOT / "data" / "gold" / "m3" / f"{case_id}.json"
    if not path.exists():
        pytest.skip("缺少 gold 文件：先运行 scripts/seed.py --seed 42")
    return json.loads(path.read_text(encoding="utf-8"))


async def _seeded() -> None:
    try:
        async with get_session_factory()() as session:
            po_no = await session.scalar(select(t.Po.po_no).limit(1))
    except Exception:  # noqa: BLE001 - DB 未起
        pytest.skip("PostgreSQL 不可用：先 docker compose up -d db && make migrate && make seed")
    if po_no is None:
        pytest.skip("种子数据为空：先运行 scripts/seed.py --seed 42")


async def _create_run(subject: dict) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            t.AgentRun(
                id=run_id,
                module="m3",
                subject_ref=subject,
                thread_id=str(run_id),
                status="queued",
            )
        )
        await session.commit()
    return run_id


def _deps() -> MatchingDeps:
    return MatchingDeps(bus=EventBus(get_session_factory()), session_factory=get_session_factory())


async def test_graph_interrupts_before_finalize_then_release_writes_accounting() -> None:
    await _seeded()
    po_no, invoice_no, case_id = CASE_1
    gold = _gold(case_id)
    async with get_session_factory()() as session:
        po_row = await session.scalar(select(t.Po).where(t.Po.po_no == po_no))
        line_row = await session.scalar(select(t.PoLine).where(t.PoLine.po_id == po_row.id))
        if line_row is not None and float(line_row.qty_invoiced) > 0:
            pytest.skip("该 PO 已核销（集成测试重复运行需先 reseed）")
    subject = {"po_no": po_no, "invoice_nos": [invoice_no]}
    run_id = await _create_run(subject)
    graph = build_matching_graph(_deps())
    config = {"configurable": {"thread_id": str(run_id)}}

    state = await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)

    report = state["report"]
    assert report["overall"] == gold["expected_overall"], report
    assert report["po_no"] == po_no
    assert {diff["diff_type"] for diff in report["diffs"]} == {
        diff["type"] for diff in gold["expected_diffs"]
    }
    assert state.get("finalized") is not True, "finalize 必须在人工决策之后才执行"

    async with get_session_factory()() as session:
        po = await session.scalar(select(t.Po).where(t.Po.po_no == po_no))
        line = await session.scalar(select(t.PoLine).where(t.PoLine.po_id == po.id))
        before_invoiced = float(line.qty_invoiced)
        audit = await session.scalar(select(t.AuditLog.id).where(t.AuditLog.run_id == run_id))
        assert audit is None, "决策前不得写审计/会计事实"
    consumed = next(
        match["consumed_qty"] for match in report["line_matches"] if match["invoice_line_no"] is not None
    )

    await graph.aupdate_state(config, {"decision": "accept", "reviewer_note": "e2e release", "review_id": None})
    final_state = await graph.ainvoke(None, config)
    assert final_state["finalized"] is True

    async with get_session_factory()() as session:
        po = await session.scalar(select(t.Po).where(t.Po.po_no == po_no))
        line = await session.scalar(select(t.PoLine).where(t.PoLine.po_id == po.id))
        assert float(line.qty_invoiced) == pytest.approx(before_invoiced + consumed, abs=0.01)

        actions = (await session.scalars(select(t.AuditLog.action).where(t.AuditLog.run_id == run_id))).all()
        assert "release" in actions and "update" in actions

        match_run = await session.scalar(
            select(t.MatchRun).where(t.MatchRun.po_id == po.id).order_by(t.MatchRun.created_at.desc())
        )
        assert match_run is not None and match_run.status == "matched"
        diff_types = (
            await session.scalars(select(t.MatchDiff.diff_type).where(t.MatchDiff.run_id == match_run.id))
        ).all()
        assert set(diff_types) == {diff["type"] for diff in gold["expected_diffs"]}

        artifact = await session.scalar(select(t.Artifact).where(t.Artifact.run_id == run_id))
        assert artifact is not None and artifact.kind == "match_report"
        assert artifact.payload["decision"] == "accept"

        invoice = await session.scalar(select(t.Invoice).where(t.Invoice.invoice_no == invoice_no))
        assert invoice.status == "matched"

        node_end_count = await session.scalar(
            select(func.count())
            .select_from(t.RunEvent)
            .where(t.RunEvent.run_id == run_id, t.RunEvent.event_type == "node.end")
        )
        assert node_end_count >= 6  # load/normalize/align/reconcile/compose + finalize


async def test_graph_reject_keeps_accounting_untouched() -> None:
    await _seeded()
    po_no, invoice_no, _case_id = CASE_2
    subject = {"po_no": po_no, "invoice_nos": [invoice_no]}
    run_id = await _create_run(subject)
    graph = build_matching_graph(_deps())
    config = {"configurable": {"thread_id": str(run_id)}}

    await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)
    async with get_session_factory()() as session:
        po = await session.scalar(select(t.Po).where(t.Po.po_no == po_no))
        line = await session.scalar(select(t.PoLine).where(t.PoLine.po_id == po.id))
        before_invoiced = float(line.qty_invoiced)

    await graph.aupdate_state(config, {"decision": "reject", "reviewer_note": "hold it", "review_id": None})
    final_state = await graph.ainvoke(None, config)
    assert final_state["finalized"] is True

    async with get_session_factory()() as session:
        po = await session.scalar(select(t.Po).where(t.Po.po_no == po_no))
        line = await session.scalar(select(t.PoLine).where(t.PoLine.po_id == po.id))
        assert float(line.qty_invoiced) == pytest.approx(before_invoiced, abs=0.01)

        actions = (await session.scalars(select(t.AuditLog.action).where(t.AuditLog.run_id == run_id))).all()
        assert "hold" in actions and "release" not in actions

        match_run = await session.scalar(
            select(t.MatchRun).where(t.MatchRun.po_id == po.id).order_by(t.MatchRun.created_at.desc())
        )
        assert match_run is not None and match_run.status == "hold"
        invoice = await session.scalar(select(t.Invoice).where(t.Invoice.invoice_no == invoice_no))
        assert invoice.status == "hold"


async def test_run_service_drives_m3_hitl_flow() -> None:
    await _seeded()
    po_no, invoice_no, _case_id = CASE_3
    service = RunService(
        EventBus(get_session_factory()),
        get_session_factory(),
        step_delay_ms=0,
        matching_graph=build_matching_graph(_deps()),
    )
    started = await service.start("m3", {"po_no": po_no, "invoice_nos": [invoice_no]})
    run_id = uuid.UUID(started["run_id"])

    status = "queued"
    for _ in range(200):
        status = await service.run_status(run_id)
        if status in {"awaiting_review", "succeeded", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert status == "awaiting_review", status

    reviews = await service.list_pending_reviews()
    review = next(item for item in reviews if item.run_id == run_id)
    assert review.kind == "release_or_hold"
    await service.resume(run_id, review_id=review.id, decision="accept", edits=[], note="ok")

    for _ in range(200):
        status = await service.run_status(run_id)
        if status in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert status == "succeeded", status

    snapshot = await service.snapshot(run_id)
    assert snapshot["artifact"] is not None
    assert snapshot["artifact"]["report"]["module"] == "m3"
    assert snapshot["review"] is None
