"""横向比价接口测试（需 seed + seed_quotes）。"""

from __future__ import annotations

import httpx
import pytest

from app.main import app
from app.seed.quotes import adjusted_total, payment_days
from decimal import Decimal


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", trust_env=False)


def test_payment_terms_and_financing_adjustment() -> None:
    assert payment_days("月结45天") == 45
    assert payment_days("预付30%") == 0
    total = Decimal("10000")
    adjusted = adjusted_total(total, "月结60天", Decimal("0.08"))
    assert adjusted == Decimal("10131.5068")


@pytest.mark.integration
async def test_comparables_ranks_by_adjusted_amount() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/quotes/comparables", params={"pr_no": "PR-2026-0001"})
    assert response.status_code == 200, response.text
    body = response.json()
    if not body["quotes"]:
        pytest.skip("没有报价数据，先运行 scripts/seed_quotes.py")
    amounts = [row["adjusted_amount"] for row in body["quotes"]]
    assert amounts == sorted(amounts)
    assert [row["rank"] for row in body["quotes"]] == list(range(1, len(amounts) + 1))
    assert body["quotes"][0]["recommended"] is True
    assert body["recommended_supplier"] == body["quotes"][0]["supplier_code"]
