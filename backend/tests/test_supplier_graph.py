"""M2 供应商图集成测试：闸门短路、评分、冷启动、finalize 写库（需 DB + seed）。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.agents.supplier import SupplierDeps, build_supplier_graph
from app.db.session import get_session_factory
from app.models import tables as t
from app.observability.runevents import EventBus

pytestmark = pytest.mark.integration


async def _offline_llm(messages, schema):  # noqa: ANN001 - 测试强制离线（LLM 降级路径）
    raise RuntimeError("offline: LLM disabled in tests")


def _deps() -> SupplierDeps:
    return SupplierDeps(
        bus=EventBus(get_session_factory()),
        session_factory=get_session_factory(),
        llm_complete=_offline_llm,
    )


async def _seeded() -> None:
    try:
        async with get_session_factory()() as session:
            code = await session.scalar(select(t.Supplier.code).limit(1))
    except Exception:  # noqa: BLE001 - DB 未起
        pytest.skip("PostgreSQL 不可用：先 docker compose up -d db && make migrate && make seed")
    if code is None:
        pytest.skip("种子数据为空：先运行 scripts/seed.py --seed 42")


async def _create_run(subject: dict) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(t.AgentRun(id=run_id, module="m2", subject_ref=subject, thread_id=str(run_id), status="queued"))
        await session.commit()
    return run_id


async def test_gate_hit_short_circuits_and_finalize_writes_eval() -> None:
    await _seeded()
    # idx=3 → SUP-004：license_invalid → G1 否决
    supplier_code = "SUP-004"
    subject = {"supplier_code": supplier_code, "mode": "review"}
    run_id = await _create_run(subject)
    graph = build_supplier_graph(_deps())
    config = {"configurable": {"thread_id": str(run_id)}}

    state = await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)
    report = state["report"]
    assert report["verdict"] == "reject"
    assert {hit["code"] for hit in report["gate_hits"]} == {"G1_LICENSE_INVALID"}
    assert report["dimensions"] == [], "闸门命中必须跳过评分"
    assert report["llm_status"] == "skipped_gate_hit"

    async with get_session_factory()() as session:
        supplier = await session.scalar(select(t.Supplier).where(t.Supplier.code == supplier_code))
        before_status = supplier.status
        eval_row = await session.scalar(select(t.SupplierEval.id).where(t.SupplierEval.run_id == run_id))
        assert eval_row is None, "决策前不得写 supplier_eval"

    await graph.aupdate_state(config, {"decision": "accept", "reviewer_note": "confirm reject", "review_id": None})
    final_state = await graph.ainvoke(None, config)
    assert final_state["finalized"] is True

    async with get_session_factory()() as session:
        supplier = await session.scalar(select(t.Supplier).where(t.Supplier.code == supplier_code))
        assert supplier.status == before_status, "reject 不应改写供应商状态"
        eval_row = await session.scalar(select(t.SupplierEval).where(t.SupplierEval.run_id == run_id))
        assert eval_row is not None and eval_row.verdict == "reject"
        artifact = await session.scalar(select(t.Artifact).where(t.Artifact.run_id == run_id))
        assert artifact is not None and artifact.kind == "supplier_eval"
        actions = (await session.scalars(select(t.AuditLog.action).where(t.AuditLog.run_id == run_id))).all()
        assert "release" in actions


async def test_clean_supplier_scores_five_dimensions_and_sensitivity() -> None:
    await _seeded()
    supplier_code = "SUP-001"
    subject = {"supplier_code": supplier_code, "mode": "review"}
    run_id = await _create_run(subject)
    graph = build_supplier_graph(_deps())
    config = {"configurable": {"thread_id": str(run_id)}}
    state = await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)

    report = state["report"]
    assert report["gate_hits"] == [], report["gate_hits"]
    assert len(report["dimensions"]) == 5
    assert 0 <= report["total"] <= 100
    assert report["verdict"] in {"approve", "conditional"}
    assert report["sensitivity"]["samples"] == 300
    assert report["llm_status"] in {"ok", "unavailable"}
    assert report["confidence"] in {"high", "medium", "low"}

    await graph.aupdate_state(config, {"decision": "accept", "reviewer_note": None, "review_id": None})
    await graph.ainvoke(None, config)
    async with get_session_factory()() as session:
        eval_row = await session.scalar(select(t.SupplierEval).where(t.SupplierEval.run_id == run_id))
        assert eval_row is not None
        assert eval_row.total is not None
        assert len(eval_row.dimensions or []) == 5


async def test_cold_start_forced_conditional_low_confidence() -> None:
    await _seeded()
    # idx=24 → SUP-025：pending（冷启动）
    supplier_code = "SUP-025"
    subject = {"supplier_code": supplier_code, "mode": "onboard"}
    run_id = await _create_run(subject)
    graph = build_supplier_graph(_deps())
    config = {"configurable": {"thread_id": str(run_id)}}
    state = await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)
    report = state["report"]
    assert report["cold_start"] is True
    assert report["verdict"] == "conditional"
    assert report["confidence"] == "low"
    assert any("试用期" in condition for condition in report["conditions"])
