"""M1 预检图集成测试：HITL 补字段、放行写回、退回不改 spec（需 DB + seed）。"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.precheck import PrecheckDeps, build_precheck_graph
from app.db.session import get_session_factory
from app.models import tables as t
from app.observability.runevents import EventBus
from app.services.runs import RunService

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _gold_cases() -> list[dict]:
    files = sorted((PROJECT_ROOT / "data" / "gold" / "m1").glob("*.json"))
    if not files:
        pytest.skip("缺少 M1 gold：先运行 scripts/seed.py --seed 42")
    return [json.loads(path.read_text(encoding="utf-8")) for path in files]


def _pick(cases: list[dict], **match) -> dict:
    for case in cases:
        if all(case.get(key) == value for key, value in match.items()):
            return case
    pytest.skip(f"找不到匹配的 gold 用例：{match}")


async def _seeded() -> None:
    try:
        async with get_session_factory()() as session:
            pr_no = await session.scalar(select(t.Pr.pr_no).limit(1))
    except Exception:  # noqa: BLE001 - DB 未起
        pytest.skip("PostgreSQL 不可用：先 docker compose up -d db && make migrate && make seed")
    if pr_no is None:
        pytest.skip("种子数据为空：先运行 scripts/seed.py --seed 42")


async def _create_run(subject: dict) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            t.AgentRun(id=run_id, module="m1", subject_ref=subject, thread_id=str(run_id), status="queued")
        )
        await session.commit()
    return run_id


def _deps() -> PrecheckDeps:
    return PrecheckDeps(bus=EventBus(get_session_factory()), session_factory=get_session_factory())


async def _pr_status(pr_no: str) -> str:
    async with get_session_factory()() as session:
        pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == pr_no))
        return pr.status if pr else "missing"


async def test_precheck_graph_accept_applies_patch() -> None:
    await _seeded()
    cases = _gold_cases()
    gold = _pick(cases, anomaly="missing_field", category_code="IT_HW")
    if await _pr_status(gold["pr_no"]) != "submitted":
        pytest.skip("该 PR 已被预检（重复运行需先 reseed）")
    subject = {"pr_no": gold["pr_no"]}
    run_id = await _create_run(subject)
    graph = build_precheck_graph(_deps())
    config = {"configurable": {"thread_id": str(run_id)}}

    state = await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)
    report = state["report"]
    assert report["verdict"] == gold["expected_verdict"]
    got = {(finding["type"], finding["field"], finding["severity"]) for finding in report["findings"]}
    expected = {(item["type"], item["field"], item["severity"]) for item in gold["expected_findings"]}
    assert got == expected
    assert report["requires_review"] is True
    assert any("memory_gb" in patch["path"] for patch in report["patches"])

    async with get_session_factory()() as session:
        pr = await session.scalar(select(t.Pr).where(t.Pr.po_no == gold["pr_no"])) if False else None
        pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == gold["pr_no"]))
        item = await session.scalar(select(t.PrItem).where(t.PrItem.pr_id == pr.id))
        assert "memory_gb" not in (item.spec or {}), "决策前不得写 spec"
        audit = await session.scalar(select(t.AuditLog.id).where(t.AuditLog.run_id == run_id))
        assert audit is None

    await graph.aupdate_state(config, {"decision": "accept", "reviewer_note": "precheck accept", "review_id": None})
    final_state = await graph.ainvoke(None, config)
    assert final_state["finalized"] is True

    async with get_session_factory()() as session:
        pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == gold["pr_no"]))
        item = await session.scalar(select(t.PrItem).where(t.PrItem.pr_id == pr.id))
        assert item.spec.get("memory_gb") is not None
        assert item.spec.get("owner")
        assert pr.status.startswith("prechecked")
        actions = (await session.scalars(select(t.AuditLog.action).where(t.AuditLog.run_id == run_id))).all()
        assert "apply_patch" in actions
        artifact = await session.scalar(select(t.Artifact).where(t.Artifact.run_id == run_id))
        assert artifact is not None and artifact.kind == "precheck"
        assert artifact.payload["decision"] == "accept"


async def test_precheck_reject_keeps_spec_untouched() -> None:
    await _seeded()
    cases = _gold_cases()
    gold = _pick(cases, anomaly="over_budget", category_code="SAAS")
    if await _pr_status(gold["pr_no"]) != "submitted":
        pytest.skip("该 PR 已被预检（重复运行需先 reseed）")
    subject = {"pr_no": gold["pr_no"]}
    run_id = await _create_run(subject)
    graph = build_precheck_graph(_deps())
    config = {"configurable": {"thread_id": str(run_id)}}

    await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)
    async with get_session_factory()() as session:
        pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == gold["pr_no"]))
        item = await session.scalar(select(t.PrItem).where(t.PrItem.pr_id == pr.id))
        before_spec = dict(item.spec or {})

    await graph.aupdate_state(config, {"decision": "reject", "reviewer_note": "back to requester", "review_id": None})
    final_state = await graph.ainvoke(None, config)
    assert final_state["finalized"] is True

    async with get_session_factory()() as session:
        pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == gold["pr_no"]))
        item = await session.scalar(select(t.PrItem).where(t.PrItem.pr_id == pr.id))
        assert dict(item.spec or {}) == before_spec
        assert pr.status == "returned"
        actions = (await session.scalars(select(t.AuditLog.action).where(t.AuditLog.run_id == run_id))).all()
        assert actions == ["hold"]


async def test_run_service_auto_finalizes_clean_pr() -> None:
    await _seeded()
    cases = _gold_cases()
    gold = _pick(cases, anomaly="clean", category_code="IT_HW")
    service = RunService(
        EventBus(get_session_factory()),
        get_session_factory(),
        step_delay_ms=0,
        precheck_graph=build_precheck_graph(_deps()),
    )
    started = await service.start("m1", {"pr_no": gold["pr_no"]})
    run_id = uuid.UUID(started["run_id"])
    status = "queued"
    for _ in range(200):
        status = await service.run_status(run_id)
        if status in {"succeeded", "failed", "awaiting_review"}:
            break
        await asyncio.sleep(0.05)
    assert status == "succeeded", status
    snapshot = await service.snapshot(run_id)
    assert snapshot["artifact"] is not None
    assert snapshot["artifact"]["report"]["verdict"] == "pass"
    assert snapshot["artifact"]["decision"] == "accept"


async def test_precheck_edits_selects_and_edits_patch() -> None:
    """HITL 只采纳并改值一条补丁：写回的 spec 必须是改值后的，审计只记录实际应用集合。"""
    await _seeded()
    cases = _gold_cases()
    gold = None
    for case in cases:
        if case.get("anomaly") != "missing_field":
            continue
        if await _pr_status(case["pr_no"]) == "submitted":
            gold = case
            break
    if gold is None:
        pytest.skip("没有处于 submitted 的 missing_field PR（需 reseed）")

    subject = {"pr_no": gold["pr_no"]}
    run_id = await _create_run(subject)
    graph = build_precheck_graph(_deps())
    config = {"configurable": {"thread_id": str(run_id)}}
    state = await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)
    report = state["report"]
    patches = report.get("patches") or []
    if not patches:
        pytest.skip(f"{gold['pr_no']} 没有建议补丁，换一条用例如需")

    patch = patches[0]
    path = patch["path"]
    leaf = path.rsplit("/", 1)[-1]
    index = int(path.split("/")[2])
    custom_value = 32 if leaf == "memory_gb" else 3
    edits = [{"path": path, "value": custom_value, "source": "reviewer", "ref": "manual"}]

    await graph.aupdate_state(
        config,
        {"decision": "edit", "reviewer_note": "只采纳并修改一条", "review_id": None, "edits": edits},
    )
    final_state = await graph.ainvoke(None, config)
    assert final_state["finalized"] is True

    async with get_session_factory()() as session:
        pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == gold["pr_no"]))
        items = (
            await session.scalars(select(t.PrItem).where(t.PrItem.pr_id == pr.id).order_by(t.PrItem.line_no))
        ).all()
        assert items[index].spec.get(leaf) == custom_value
        artifact = await session.scalar(select(t.Artifact).where(t.Artifact.run_id == run_id))
        assert artifact is not None
        assert artifact.payload["applied_patches"] == 1
        assert artifact.payload["selected_patches"][0]["value"] == custom_value
        assert artifact.payload["decision"] == "edit"
        audits = (
            await session.scalars(select(t.AuditLog).where(t.AuditLog.run_id == run_id))
        ).all()
        apply_logs = [row for row in audits if row.action == "apply_patch"]
        assert len(apply_logs) == 1
        assert len(apply_logs[0].patch_json or []) == 1



async def test_precheck_release_commits_budget_once() -> None:
    """放行时预算 amount_committed 必须在 finalize 同事务写回，且重复 finalize 不重复占用。"""
    await _seeded()
    async with get_session_factory()() as session:
        pr = await session.scalar(select(t.Pr).where(t.Pr.status == "submitted").limit(1))
    if pr is None:
        pytest.skip("没有 submitted PR（需 reseed）")
    subject = {"pr_no": pr.pr_no}
    run_id = await _create_run(subject)
    graph = build_precheck_graph(_deps())
    config = {"configurable": {"thread_id": str(run_id)}}
    await graph.ainvoke({"run_id": str(run_id), "subject": subject}, config)

    async with get_session_factory()() as session:
        pr_row = await session.scalar(select(t.Pr).where(t.Pr.pr_no == pr.pr_no))
        budget = await session.scalar(
            select(t.Budget).where(
                t.Budget.department_id == pr_row.department_id,
                t.Budget.category_code == pr_row.category_code,
                t.Budget.fiscal_year == (pr_row.created_at.year if pr_row.created_at else 2026),
            )
        )
        if budget is None:
            pytest.skip("该 PR 没有预算行")
        before = Decimal(budget.amount_committed)
        estimated = Decimal(pr_row.estimated_amount)

    await graph.aupdate_state(config, {"decision": "accept", "reviewer_note": "budget test", "review_id": None})
    await graph.ainvoke(None, config)

    async with get_session_factory()() as session:
        budget = await session.scalar(
            select(t.Budget).where(
                t.Budget.department_id == pr.department_id,
                t.Budget.category_code == pr.category_code,
                t.Budget.fiscal_year == (pr.created_at.year if pr.created_at else 2026),
            )
        )
        assert budget is not None and budget.amount_committed == (before + estimated).quantize(before)

    # 重复 finalize 应幂等（不重复占预算）
    await graph.aupdate_state(config, {"decision": "accept", "reviewer_note": "repeat", "review_id": None})
    await graph.ainvoke(None, config)
    async with get_session_factory()() as session:
        budget = await session.scalar(
            select(t.Budget).where(
                t.Budget.department_id == pr.department_id,
                t.Budget.category_code == pr.category_code,
                t.Budget.fiscal_year == (pr.created_at.year if pr.created_at else 2026),
            )
        )
        assert budget is not None and budget.amount_committed == (before + estimated).quantize(before)
