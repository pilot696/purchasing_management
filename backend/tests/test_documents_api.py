"""文档抽取接口的集成测试（需 DB + seed，缺数据自动 skip）。"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.db.session import get_session_factory
from app.main import app
from app.models import tables as t

pytestmark = pytest.mark.integration


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", trust_env=False)


async def _pick_doc(*, has_text_layer: bool) -> str:
    try:
        async with get_session_factory()() as session:
            doc = await session.scalar(
                select(t.Doc)
                .where(t.Doc.doc_type == "invoice", t.Doc.has_text_layer.is_(has_text_layer))
                .limit(1)
            )
    except Exception:  # noqa: BLE001 - DB 未起
        pytest.skip("PostgreSQL 不可用：先 docker compose up -d db && make migrate && make seed")
    if doc is None:
        pytest.skip("种子里没有符合条件的文档：先运行 scripts/seed.py")
    return str(doc.id)


async def test_extract_native_invoice_and_annotations() -> None:
    doc_id = await _pick_doc(has_text_layer=True)
    async with _client() as client:
        response = await client.post(f"/api/v1/documents/{doc_id}/extract")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["result"]["route"]["kind"] == "pdf_native"
        assert payload["result"]["extract"]["invoice_no"].startswith("INV-")
        assert payload["summary"]["status"] in {"ok", "needs_review"}
        assert payload["record_id"] > 0

        annotations = await client.get(f"/api/v1/documents/{doc_id}/annotations")
        assert annotations.status_code == 200
        items = annotations.json()["items"]
        assert items, "抽取后应有 bbox 注解"
        assert any(item["field_name"] == "invoice_no" for item in items)
        for item in items:
            assert 0 <= item["x0"] < item["x1"] <= 1
            assert 0 <= item["y0"] < item["y1"] <= 1


async def test_extract_scanned_without_vision_returns_needs_review() -> None:
    doc_id = await _pick_doc(has_text_layer=False)
    async with _client() as client:
        response = await client.post(f"/api/v1/documents/{doc_id}/extract")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["result"]["route"]["kind"] == "scanned"
        assert payload["result"]["extract"] is None
        assert payload["summary"]["status"] == "failed"
        assert payload["result"]["needs_review_fields"] == ["vlm_required"]


async def test_extract_is_idempotent_for_annotations() -> None:
    doc_id = await _pick_doc(has_text_layer=True)
    async with _client() as client:
        first = await client.post(f"/api/v1/documents/{doc_id}/extract")
        second = await client.post(f"/api/v1/documents/{doc_id}/extract")
        annotations = await client.get(f"/api/v1/documents/{doc_id}/annotations")
    assert first.status_code == second.status_code == 200
    assert len(annotations.json()["items"]) > 0  # 重复抽取只替换注解，不重复累积
