"""prompt 缓存键与磁盘缓存（Spec §4.2）：同 prompt 第二次调用 cost=0，且可复现。"""

from __future__ import annotations

from pydantic import BaseModel

from app.llm.cache import PromptCache, cache_key, prompt_sha, schema_sha
from app.llm.types import Message


class DemoOutput(BaseModel):
    ok: bool


class OtherOutput(BaseModel):
    value: int


def _msgs(text: str = "你好") -> list[Message]:
    return [Message(role="system", content="sys"), Message(role="user", content=text)]


def test_prompt_sha_is_stable_and_order_sensitive() -> None:
    assert prompt_sha(_msgs("a")) == prompt_sha(_msgs("a"))
    assert prompt_sha(_msgs("a")) != prompt_sha(_msgs("b"))
    swapped = list(reversed(_msgs("a")))
    assert prompt_sha(_msgs("a")) != prompt_sha(swapped)


def test_schema_sha_distinguishes_models() -> None:
    assert schema_sha(None) == "none"
    assert schema_sha(DemoOutput) != schema_sha(OtherOutput)
    assert schema_sha(DemoOutput) == schema_sha(DemoOutput)


def test_cache_key_changes_with_provider_model_and_temperature() -> None:
    base = dict(prompt_digest="p", schema_digest="s")
    k = cache_key(provider="deepseek", model="deepseek-flash", temperature=0.0, **base)
    assert k != cache_key(provider="zhipu", model="deepseek-flash", temperature=0.0, **base)
    assert k != cache_key(provider="deepseek", model="deepseek-v4-pro", temperature=0.0, **base)
    assert k != cache_key(provider="deepseek", model="deepseek-flash", temperature=0.7, **base)


def test_disk_cache_roundtrip(tmp_path) -> None:
    cache = PromptCache(tmp_path)
    assert cache.get("abc") is None
    cache.put("abc", {"text": "hello", "tokens_in": 3, "tokens_out": 1})
    assert cache.get("abc") == {"text": "hello", "tokens_in": 3, "tokens_out": 1}
    cache.clear()
    assert cache.get("abc") is None
