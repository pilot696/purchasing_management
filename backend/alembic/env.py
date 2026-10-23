"""Alembic 环境：读取 DATABASE_URL，绑定 ORM metadata。

P0 约定（ADR-004）：首个 revision 用 `Base.metadata.create_all` 作为基线，
W1 第一个 schema 增量前先补一次真实 autogenerate 基线，此后全部细粒度迁移。
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.db.base import Base
from app.models import tables  # noqa: F401  # 必须 import 才能注册 metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 优先级：显式环境变量 > .env（Settings）> alembic.ini。
# 历史坑：这里原来只读 os.getenv，导致 `alembic upgrade head` 不认 .env，
# 切数据库时会静默迁移到 alembic.ini 里的旧库（VM 库始终为空）。
database_url = os.getenv("DATABASE_URL") or get_settings().database_url or config.get_main_option("sqlalchemy.url")
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
