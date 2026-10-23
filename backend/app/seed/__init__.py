"""数据工厂：确定性合成数据（Spec §10）。

设计要点：
- `build_dataset(config)` 是**纯函数**（不碰 DB、不碰文件），便于单测确定性；
- `persist(dataset, ...)` 才写 DB 与磁盘（PDF/gold）；
- 所有业务主键都用可读编码（PR-2026-0001 / PO-2026-0087 / INV-2026-3391），gold 用编码而非 UUID 关联。
"""

from __future__ import annotations

from app.seed.config import SeedConfig

__all__ = ["SeedConfig"]
