"""Windows 事件循环策略修正。

背景（GOTCHAS G-009）：psycopg 的异步连接不支持 Windows 默认的 `ProactorEventLoop`，
会直接抛 `InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode`。
SQLAlchemy 的 asyncio 扩展最终也走 psycopg 异步连接，因此**整个后端在 Windows 原生运行时**
都必须使用 `SelectorEventLoop`。

约定：
- 任何会创建事件循环的入口（`run.py`、pytest conftest、脚本）在**创建循环之前**调用 `ensure_windows_selector_loop(force=True)`；
- Linux 容器（Docker）不受影响，函数直接 no-op。
"""

from __future__ import annotations

import asyncio
import sys


def ensure_windows_selector_loop(*, force: bool = False) -> bool:
    """把 Windows 的事件循环策略切到 Selector；返回是否发生了切换。"""
    if sys.platform != "win32":
        return False
    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_cls is None:
        return False
    current = asyncio.get_event_loop_policy()
    if not force and isinstance(current, policy_cls):
        return False
    asyncio.set_event_loop_policy(policy_cls())
    return True
