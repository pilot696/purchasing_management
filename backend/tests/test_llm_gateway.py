"""网关行为：跨厂商降级、重试、缓存命中、全链失败（Spec §4.2 降级矩阵）。"""

from __future__ import annotations

import pytest

from app.llm.cache import PromptCache
from app.llm.config import parse_llm_config
from app.llm.provider import FakeLLMProvider
from app.llm.router import LLMUnavailableError, ListRecorder, ModelRouter
from app.llm.types import Message

RAW_CONFIG = {
    "providers": {
        "p1": {"base_url": "fake://p1", "api_key": ""},
        "p2": {"base_url": "fake://p2", "api_key": ""},
    },
    "roles": {
        "reason": {
            "provider": "p1",
            "model": "m1",
            "schema_mode": "json_object",
            "fallback": ["reason_small"],
        },
        "reason_small": {"provider": "p2", "model": "m2", "schema_mode": "json_object"},
    },
    "pricing_usd_per_1m": {"m1": {"in": 0.15, "out": 0.6}, "m2": {"in": 0.15, "out": 0.6}},
    "fx_usd_cny": 7.1,
}


def _router(providers, *, recorder=None, cache=None, sleep=None) -> ModelRouter:
    cfg = parse_llm_config(RAW_CONFIG)
    return ModelRouter(
        cfg,
        providers,
        recorder=recorder,
        cache=cache,
        max_retries=2,
        backoff_base_s=0.01,
        sleep=sleep or _noop_sleep,
    )


async def _noop_sleep(_: float) -> None:
    return None


async def test_cross_vendor_fallback_after_retries() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    p1 = FakeLLMProvider(name="p1", fail_times=3)  # 1 次首次 + 2 次重试全失败
    p2 = FakeLLMProvider(name="p2", responses={"*": '{"ok": true}'})
    recorder = ListRecorder()
    router = _router({"p1": p1, "p2": p2}, recorder=recorder, sleep=fake_sleep)

    result = await router.complete(role="reason", messages=[Message(role="user", content="你好")], step="t")

    assert result.provider == "p2" and result.model == "m2"
    assert [r.ok for r in recorder.records] == [False, False, False, True]
    assert [r.retry_no for r in recorder.records] == [0, 1, 2, 0]
    assert len(sleeps) == 2  # 指数退避只在同厂商重试时发生
    assert result.usage.cost_cny > 0


async def test_cache_hit_skips_second_call(tmp_path) -> None:
    p1 = FakeLLMProvider(name="p1", responses={"*": '{"ok": true}'})
    recorder = ListRecorder()
    router = _router({"p1": p1, "p2": FakeLLMProvider(name="p2")}, recorder=recorder, cache=PromptCache(tmp_path))
    messages = [Message(role="user", content="同一条 prompt")]

    first = await router.complete(role="reason", messages=messages, step="t")
    second = await router.complete(role="reason", messages=messages, step="t")

    assert len(p1.calls) == 1  # 第二次没有真的调用模型
    assert second.usage.cache_hit is True
    assert second.usage.cost_cny == 0.0
    assert recorder.records[-1].cache_hit is True
    assert first.usage.cache_hit is False


async def test_all_providers_fail_raises_unavailable() -> None:
    p1 = FakeLLMProvider(name="p1", fail_times=99)
    p2 = FakeLLMProvider(name="p2", fail_times=99)
    router = _router({"p1": p1, "p2": p2})
    with pytest.raises(LLMUnavailableError) as excinfo:
        await router.complete(role="reason", messages=[Message(role="user", content="x")])
    assert excinfo.value.role == "reason"
    assert any("UPSTREAM_503" in a for a in excinfo.value.attempts)


async def test_unpriced_model_records_zero_cost() -> None:
    cfg = parse_llm_config(
        {
            "providers": {"p1": {"base_url": "fake://p1", "api_key": ""}},
            "roles": {"reason": {"provider": "p1", "model": "unpriced-model"}},
            "pricing_usd_per_1m": {},
        }
    )
    router = ModelRouter(cfg, {"p1": FakeLLMProvider(name="p1")}, recorder=ListRecorder(), sleep=_noop_sleep)
    result = await router.complete(role="reason", messages=[Message(role="user", content="x")])
    assert result.usage.cost_cny == 0.0


async def test_unknown_role_is_rejected() -> None:
    router = _router({"p1": FakeLLMProvider(name="p1"), "p2": FakeLLMProvider(name="p2")})
    with pytest.raises(KeyError):
        await router.complete(role="does-not-exist", messages=[Message(role="user", content="x")])
