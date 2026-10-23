"""invoice.remark：M3 terms_conflict 判定的输入（Spec §7.5）。

Revision ID: 0002_invoice_remark
Revises: 0001_initial
Create Date: 2026-10-23

注意：0001 基线是用 `Base.metadata.create_all` 生成的，会用到**当前 ORM**（已含 remark 列）。
因此全新库上 0002 再 ADD COLUMN 会报 DuplicateColumn 并导致整个 upgrade 回滚；
这里用 IF NOT EXISTS 保证：旧库补列、新库跳过，两条升级路径都幂等。
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002_invoice_remark"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE invoice ADD COLUMN IF NOT EXISTS remark TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE invoice DROP COLUMN IF EXISTS remark")
