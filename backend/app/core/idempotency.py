"""演示级 Idempotency-Key 支持（进程内，单副本）。

与 Spec §8.4 的差异：
- 生产语义应为 24h 持久化（Redis/DB）；P0 用进程内 TTL 缓存，进程重启即失效；
- 同 key 不同 payload → 409 IDEMPOTENCY_KEY_REUSED；
- 只对会改变状态/创建运行的 POST 生效。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from app.core.errors import AppError


class IdempotencyConflict(AppError):
    status_code = 409
    code = "IDEMPOTENCY_KEY_REUSED"
    default_message = "同一 Idempotency-Key 对应了不同的请求体"


def payload_digest(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _Entry:
    payload_hash: str
    response: dict[str, Any]
    expires_at: float


class IdempotencyStore:
    def __init__(self, *, ttl_s: float = 24 * 3600, max_entries: int = 2048) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._entries: dict[str, _Entry] = {}

    def _prune(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)
        if len(self._entries) > self._max_entries:
            oldest = sorted(self._entries.items(), key=lambda item: item[1].expires_at)
            for key, _ in oldest[: len(self._entries) - self._max_entries]:
                self._entries.pop(key, None)

    def lookup(self, scope: str, key: str, payload: Any) -> dict[str, Any] | None:
        now = time.time()
        self._prune(now)
        entry = self._entries.get(f"{scope}:{key}")
        if entry is None:
            return None
        if entry.payload_hash != payload_digest(payload):
            raise IdempotencyConflict(details={"scope": scope, "idempotency_key": key})
        return entry.response

    def remember(self, scope: str, key: str, payload: Any, response: dict[str, Any]) -> None:
        now = time.time()
        self._entries[f"{scope}:{key}"] = _Entry(
            payload_hash=payload_digest(payload),
            response=response,
            expires_at=now + self._ttl_s,
        )
        self._prune(now)


idempotency_store = IdempotencyStore()
