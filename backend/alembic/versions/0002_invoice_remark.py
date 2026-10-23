"""invoice.remark：M3 terms_conflict 判定的输入（Spec §7.5）。

Revision ID: 0002_invoice_remark
Revises: 0001_initial
Create Date: 2026-10-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_invoice_remark"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoice", sa.Column("remark", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice", "remark")
