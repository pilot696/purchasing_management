"""种子数据落库后的一致性校验（需 DB + 已执行 seed，否则 skip）。

在 `test_integration_db.py` 之外单独放，便于 seed 后单独跑：
    pytest -m integration tests/test_seed_integrity.py
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.session import get_session_factory

pytestmark = pytest.mark.integration


async def _scalar(sql: str) -> int:
    async with get_session_factory()() as session:
        value = await session.scalar(text(sql))
    return int(value or 0)


@pytest.fixture
async def seeded() -> None:
    try:
        count = await _scalar("SELECT count(*) FROM supplier")
    except Exception:  # noqa: BLE001 - DB 未起时 skip
        pytest.skip("PostgreSQL 不可用：先 docker compose up -d db && make migrate && make seed")
    if count == 0:
        pytest.skip("种子数据为空：先运行 scripts/seed.py --seed 42")


async def test_po_line_received_matches_receipts(seeded: None) -> None:
    """po_line.qty_received 必须等于收货行合计（M3 可开票上限依赖它，Spec §7.5）。"""
    mismatched = await _scalar(
        """
        SELECT count(*) FROM (
          SELECT pl.id, pl.qty_received,
                 COALESCE((SELECT sum(rl.qty) FROM receipt_line rl WHERE rl.po_line_id = pl.id), 0) AS gr_qty
          FROM po_line pl
        ) t WHERE t.qty_received <> t.gr_qty
        """
    )
    assert mismatched == 0


async def test_doc_text_ratio_consistent_with_text_layer(seeded: None) -> None:
    inconsistent = await _scalar(
        "SELECT count(*) FROM doc WHERE (has_text_layer IS TRUE AND text_ratio < 0.15) "
        "OR (has_text_layer IS FALSE AND text_ratio <> 0)"
    )
    assert inconsistent == 0, "doc.text_ratio 与 DocumentRouter 口径不一致"


async def test_seed_counts(seeded: None) -> None:
    assert await _scalar("SELECT count(*) FROM supplier") == 30
    assert await _scalar("SELECT count(*) FROM sku_catalog") == 100
    assert await _scalar("SELECT count(*) FROM po") == 80
    assert await _scalar("SELECT count(*) FROM pr") == 120
    assert await _scalar("SELECT count(*) FROM policy_chunk") == 60
