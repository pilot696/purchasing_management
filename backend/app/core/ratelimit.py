"""演示级限流：进程内滑动窗口（单副本）。

为什么不用 Redis 令牌桶：P0 目标是防止演示/误点重复触发，不是生产网关；
生产化时替换为 Redis + 网关级限流（Spec §8.4）。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.core.errors import AppError


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    default_message = "请求过于频繁，请稍后再试"


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, window_s: float = 60.0, now: float | None = None) -> None:
        if limit <= 0:
            return
        current = time.time() if now is None else now
        hits = self._hits[key]
        while hits and current - hits[0] >= window_s:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(window_s - (current - hits[0])))
            raise RateLimited(details={"limit": limit, "window_s": window_s, "retry_after_s": retry_after})
        hits.append(current)

    def reset(self) -> None:
        self._hits.clear()


run_rate_limiter = SlidingWindowLimiter()
