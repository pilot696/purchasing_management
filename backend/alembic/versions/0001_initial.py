"""baseline: 全量 schema（P0）

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-23

说明（ADR-004）：P0 用 metadata.create_all 生成基线，便于在无 Docker daemon 的
环境里把 schema 一次性固化；W1 起改为 autogenerate 细粒度迁移。
本文件同时负责创建 pgvector / pg_trgm 扩展。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from app.db.base import Base
from app.models import tables  # noqa: F401  # 注册所有表

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    bind.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
