#!/bin/sh
# 容器入口：自动迁移 + 首次自动 seed + 启动 API（容器内托管前端 dist）
# SEED_ON_START=0 可关闭自动初始化（生产接已有数据库时使用）
set -eu

export PYTHONPATH=/workspace/backend

echo "[entrypoint] alembic upgrade head"
cd /workspace/backend
alembic upgrade head

if [ "${SEED_ON_START:-1}" = "1" ]; then
  cd /workspace
  python - <<'PY'
import asyncio
import subprocess
import sys

from sqlalchemy import func, select

from app.core.loop import ensure_windows_selector_loop
from app.db.session import get_session_factory
from app.models.tables import Supplier


async def supplier_count() -> int:
    async with get_session_factory()() as session:
        return int(await session.scalar(select(func.count()).select_from(Supplier)) or 0)


ensure_windows_selector_loop(force=True)
count = asyncio.run(supplier_count())
if count == 0:
    print("[entrypoint] empty database -> seeding synthetic demo data (seed=42)", flush=True)
    subprocess.run([sys.executable, "scripts/seed.py", "--seed", "42"], check=True)
else:
    print(f"[entrypoint] database already seeded ({count} suppliers), skip", flush=True)
PY
fi

echo "[entrypoint] starting uvicorn :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
