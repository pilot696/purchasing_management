"""prompt 缓存（Spec §4.2 缓存键：sha256(model + prompt_sha + schema_sha + temperature)）。

缓存是"评测可复现"和"批量跑批省 token"的基础设施：
- 同一 prompt/模型/温度第二次调用 cost=0（仍记 llm_call，cache_hit=True）；
- 缓存内容落本地磁盘（默认 `.cache/prompts`），随仓库 gitignore，不污染代码。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.llm.types import Message


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def prompt_sha(messages: list[Message] | list[dict[str, str]]) -> str:
    payload = [m.model_dump() if isinstance(m, Message) else m for m in messages]
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def schema_sha(schema: type[BaseModel] | None) -> str:
    if schema is None:
        return "none"
    return hashlib.sha256(_canonical(schema.model_json_schema()).encode("utf-8")).hexdigest()


def cache_key(
    *,
    provider: str,
    model: str,
    prompt_digest: str,
    schema_digest: str = "none",
    temperature: float = 0.0,
) -> str:
    raw = f"{provider}|{model}|{prompt_digest}|{schema_digest}|{temperature:.4f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PromptCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def clear(self) -> None:
        if self.root.exists():
            for p in self.root.rglob("*.json"):
                p.unlink(missing_ok=True)
