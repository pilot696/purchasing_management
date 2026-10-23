"""演示闭环接口的集成测试：运行历史 / 复核收件箱 / Trace / 规则库 / 可观测（需 DB + seed）。"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.main import app
from app.models import tables as t

pytestmark = pytest.mark.integration


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", trust_env=False)


async def _require_data() -> None:
    try:
        async with get_session_factory()() as session:
            rules = int(await session.scalar(select(func.count()).select_from(t.Rule)) or 0)
            runs = int(await session.scalar(select(func.count()).select_from(t.AgentRun)) or 0)
    except Exception:  # noqa: BLE001 - DB 未起
        pytest.skip("PostgreSQL 不可用：先 docker compose up -d db && make migrate && make seed")
    if rules == 0:
        pytest.skip("规则库为空：先运行 scripts/seed.py --seed 42")
    if runs == 0:
        pytest.skip("运行历史为空：先运行 scripts/demo_runs.py（或任意一次真实运行）")


async def test_run_history_partial_and_pagination() -> None:
    await _require_data()
    async with _client() as client:
        response = await client.get("/api/v1/runs", params={"limit": 3})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] >= len(payload["items"])
        assert payload["limit"] == 3
        for item in payload["items"]:
            assert {"id", "module", "status", "started_at", "duration_ms", "pending_review_id"} <= set(item)
        # 分页游标语义：offset=1 不应返回同一首条
        if payload["total"] > 1:
            second = (await client.get("/api/v1/runs", params={"limit": 1, "offset": 1})).json()
            assert second["items"][0]["id"] != payload["items"][0]["id"]


async def test_trace_contains_events_and_llm_ledger() -> None:
    await _require_data()
    async with _client() as client:
        runs = (await client.get("/api/v1/runs", params={"limit": 10})).json()["items"]
        assert runs
        trace = await client.get(f"/api/v1/runs/{runs[0]['id']}/trace")
        assert trace.status_code == 200, trace.text
        payload = trace.json()
        assert payload["events"], "每个已启动 run 至少应有 run.start 等事件"
        assert isinstance(payload["llm_calls"], list)
        assert payload["run"]["id"] == runs[0]["id"]


async def test_review_inbox_shape() -> None:
    await _require_data()
    async with _client() as client:
        response = await client.get("/api/v1/reviews")
        assert response.status_code == 200, response.text
        for item in response.json():
            assert {"id", "run_id", "kind", "status", "run_module", "summary"} <= set(item)
            assert item["status"] == "pending"


async def test_rules_catalog_and_observability_summary() -> None:
    await _require_data()
    async with _client() as client:
        rules = (await client.get("/api/v1/rules")).json()
        assert rules["categories"], "应有品类模板"
        assert len(rules["rules"]) >= 10, "种子里应有 17 条规则"
        assert isinstance(rules["tolerances"], list)
        assert rules["rules"][0]["expression"], "规则表达式不能为空（规则即数据）"

        summary = await client.get("/api/v1/observability/summary")
        assert summary.status_code == 200
        body = summary.json()
        assert body["runs_total"] >= 1
        assert 0.0 <= body["success_rate"] <= 1.0
        assert body["by_module"].get("m1", 0) + body["by_module"].get("m2", 0) + body["by_module"].get("m3", 0) >= 1

        evals = await client.get("/api/v1/observability/evals")
        assert evals.status_code == 200
        assert evals.json()["available"] is True
