"""为已有数据库补生成多供应商报价（不重跑完整 seed）。

用法：
    python scripts/seed_quotes.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.db.session import get_session_factory  # noqa: E402
from app.seed.quotes import generate_quotes  # noqa: E402


async def run() -> None:
    async with get_session_factory()() as session:
        counts = await generate_quotes(session)
        await session.commit()
    print(f"SEED_QUOTES_OK quotes={counts['quotes']} lines={counts['quote_lines']}")


if __name__ == "__main__":
    asyncio.run(run())
