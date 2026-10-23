"""Windows 事件循环策略（GOTCHAS G-009）：回归保护 run.py / conftest 所依赖的前提。"""

from __future__ import annotations

import asyncio
import sys

import pytest

from app.core.loop import ensure_windows_selector_loop


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 需要 SelectorEventLoop")
def test_policy_switches_to_selector_on_windows() -> None:
    ensure_windows_selector_loop(force=True)
    policy = asyncio.get_event_loop_policy()
    assert isinstance(policy, asyncio.WindowsSelectorEventLoopPolicy)


def test_returns_bool_and_is_idempotent() -> None:
    first = ensure_windows_selector_loop(force=True)
    second = ensure_windows_selector_loop()
    assert isinstance(first, bool) and isinstance(second, bool)
    if sys.platform != "win32":
        assert first is False and second is False
