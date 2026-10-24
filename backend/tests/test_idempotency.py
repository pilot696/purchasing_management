"""Idempotency-Key / 演示限流的单元与接口测试。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

import httpx

from app.core.idempotency import IdempotencyConflict, IdempotencyStore, idempotency_store
from app.core.ratelimit import RateLimited, SlidingWindowLimiter, run_rate_limiter
from app.db.session import get_session_factory
from app.main import app
from app.models import tables as t


@pytest.fixture(autouse=True)
def _reset_demo_stores() -> None:
    idempotency_store._entries.clear()  # noqa: SLF001 - 测试隔离
    run_rate_limiter.reset()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", trust_env=False)


def test_idempotency_store_replays_and_detects_payload_change() -> None:
    store = IdempotencyStore()
    payload = {"module": "m1", "subject": {"pr_no": "PR-X"}}
    assert store.lookup("runs:start", "key-1", payload) is None
    store.remember("runs:start", "key-1", payload, {"run_id": "run-1"})
    assert store.lookup("runs:start", "key-1", payload) == {"run_id": "run-1"}
    with pytest.raises(IdempotencyConflict):
        store.lookup("runs:start", "key-1", {"module": "m1", "subject": {"pr_no": "PR-Y"}})


def test_rate_limiter_window() -> None:
    limiter = SlidingWindowLimiter()
    limiter.check("k", limit=2, window_s=60, now=100.0)
    limiter.check("k", limit=2, window_s=60, now=101.0)
    with pytest.raises(RateLimited) as exc:
        limiter.check("k", limit=2, window_s=60, now=102.0)
    assert exc.value.details["limit"] == 2
    limiter.check("k", limit=2, window_s=60, now=161.0)  # 窗口滑出后可再请求


@pytest.mark.integration
async def test_post_runs_is_idempotent_and_conflicts_on_key_reuse() -> None:
    async with get_session_factory()() as session:
        pr_no = await session.scalar(select(t.Pr.pr_no).limit(1))
    if pr_no is None:
        pytest.skip("没有 PR 数据")
    async with get_session_factory()() as session:
        before = int(await session.scalar(select(func.count()).select_from(t.AgentRun)) or 0)

    key = f"test-{uuid.uuid4()}"
    async with _client() as client:
        first = await client.post(
            "/api/v1/runs",
            json={"module": "m1", "subject": {"pr_no": pr_no}},
            headers={"Idempotency-Key": key, "X-Demo-Role": "buyer"},
        )
        second = await client.post(
            "/api/v1/runs",
            json={"module": "m1", "subject": {"pr_no": pr_no}},
            headers={"Idempotency-Key": key, "X-Demo-Role": "buyer"},
        )
        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert second.json()["run_id"] == first.json()["run_id"]

        conflict = await client.post(
            "/api/v1/runs",
            json={"module": "m1", "subject": {"pr_no": pr_no}},
            headers={"Idempotency-Key": key, "X-Demo-Role": "buyer", "X-Force-Conflict": "1"},
        )
        # 同 key 但 body 相同 → 仍然幂等；改用不同 body 触发 409
        assert conflict.status_code == 202
        different = await client.post(
            "/api/v1/runs",
            json={"module": "m2", "subject": {"supplier_code": "SUP-001"}},
            headers={"Idempotency-Key": key, "X-Demo-Role": "buyer"},
        )
        assert different.status_code == 409, different.text
        assert different.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    async with get_session_factory()() as session:
        after = int(await session.scalar(select(func.count()).select_from(t.AgentRun)) or 0)
    assert after == before + 1
