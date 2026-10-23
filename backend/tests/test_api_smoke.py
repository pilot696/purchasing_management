"""API 冒烟（无 DB）：健康检查降级、就绪 503、OpenAPI 契约、角色校验。"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import httpx
import pytest

from app.api.deps import get_demo_role
from app.core.errors import RoleForbidden
from app.main import app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _require_db_down() -> None:
    """这些用例验证“DB 不可用时的降级”；本机若配置了可达 DB 则跳过（CI 无 DB 时照常执行）。"""
    url = os.getenv("DATABASE_URL") or "postgresql+psycopg://app:app@localhost:5432/procurement"
    parsed = urlparse(url.replace("postgresql+psycopg", "postgresql"))
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 5432), timeout=0.5):
            pytest.skip("当前 DATABASE_URL 指向可达数据库，跳过无 DB 降级用例")
    except OSError:
        return


async def test_healthz_reports_degraded_without_db() -> None:
    _require_db_down()
    async with _client() as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db"] is False
    assert body["status"] == "degraded"
    assert resp.headers["X-Request-ID"].startswith("req_")


async def test_readyz_returns_503_without_db() -> None:
    _require_db_down()
    async with _client() as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["ready"] is False


async def test_openapi_contains_p0_routes() -> None:
    async with _client() as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    for expected in (
        "/api/v1/runs",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/events",
        "/api/v1/runs/{run_id}/resume",
        "/api/v1/reviews",
        "/healthz",
    ):
        assert expected in paths, f"缺少契约路由: {expected}"


async def test_meta_enums_expose_event_types() -> None:
    async with _client() as client:
        resp = await client.get("/api/v1/meta/enums")
    body = resp.json()["enums"]
    assert "run.status" in body
    assert "interrupt" in body["event.type"]
    assert "hold" in body["overall.m3"]


async def test_start_run_returns_503_when_db_down() -> None:
    _require_db_down()
    async with _client() as client:
        resp = await client.post("/api/v1/runs", json={"module": "m1", "subject": {"pr_id": "PR-DEMO"}})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


async def test_demo_role_validation() -> None:
    assert await get_demo_role("finance") == "finance"
    assert await get_demo_role(None) == "buyer"
    with pytest.raises(RoleForbidden):
        await get_demo_role("hacker")

