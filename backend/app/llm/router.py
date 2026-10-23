"""模型网关：角色路由 + 缓存 + 重试 + 跨厂商降级 + 记账（Spec §4.2）。

业务代码只写 `router.complete(role="reason", ...)`，不出现厂商名。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.llm.cache import PromptCache, cache_key, prompt_sha, schema_sha
from app.llm.config import LLMConfig, load_llm_config
from app.llm.cost import UnknownModelPrice, cost_for_model
from app.llm.provider import FakeLLMProvider, OpenAICompatProvider
from app.llm.types import LLMProvider, LLMResult, Message, ProviderError


class LLMUnavailableError(Exception):
    """整条降级链都失败：调用方必须降级为 needs_review，不得返回假成功（Spec §4.8）。"""

    def __init__(self, role: str, attempts: list[str]) -> None:
        super().__init__(f"all providers failed for role={role}: {attempts}")
        self.role = role
        self.attempts = attempts


@dataclass(slots=True)
class LLMCallRecord:
    step: str
    provider: str
    model: str
    prompt_sha: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0
    latency_ms: int = 0
    retry_no: int = 0
    cache_hit: bool = False
    schema_repair: int = 0
    ok: bool = True
    error_code: str | None = None
    run_id: UUID | None = None


class Recorder(Protocol):
    async def record(self, rec: LLMCallRecord) -> None: ...


class ListRecorder:
    """单测/本地调试用：记录在内存里，断言调用次数与顺序。"""

    def __init__(self) -> None:
        self.records: list[LLMCallRecord] = []

    async def record(self, rec: LLMCallRecord) -> None:
        self.records.append(rec)


class ModelRouter:
    def __init__(
        self,
        config: LLMConfig,
        providers: dict[str, LLMProvider],
        *,
        recorder: Recorder | None = None,
        cache: PromptCache | None = None,
        max_retries: int = 2,
        backoff_base_s: float = 0.2,
        sleep=asyncio.sleep,
        fx_usd_cny: float | None = None,
    ) -> None:
        self.config = config
        self.providers = providers
        self.recorder = recorder
        self.cache = cache
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.sleep = sleep
        self.fx = fx_usd_cny if fx_usd_cny is not None else config.fx_usd_cny

    async def _record(self, rec: LLMCallRecord) -> None:
        if self.recorder is not None:
            await self.recorder.record(rec)

    async def complete(
        self,
        *,
        role: str,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        step: str = "unnamed",
        run_id: UUID | None = None,
    ) -> LLMResult:
        role_cfg = self.config.role(role)
        chain = [role_cfg, *(self.config.role(name) for name in role_cfg.fallback)]
        digest = prompt_sha(messages)
        schema_digest = schema_sha(schema)
        attempts: list[str] = []

        for candidate in chain:
            provider = self.providers.get(candidate.provider)
            if provider is None:
                attempts.append(f"{candidate.provider}:{candidate.model}:provider-missing")
                continue
            temp = candidate.temperature if temperature is None else temperature
            key = cache_key(
                provider=candidate.provider,
                model=candidate.model,
                prompt_digest=digest,
                schema_digest=schema_digest,
                temperature=temp,
            )
            if self.cache is not None:
                hit = self.cache.get(key)
                if hit is not None:
                    result = LLMResult(
                        provider=candidate.provider,
                        model=candidate.model,
                        text=hit["text"],
                        usage={"tokens_in": hit["tokens_in"], "tokens_out": hit["tokens_out"], "cache_hit": True},
                        latency_ms=0,
                    )
                    await self._record(
                        LLMCallRecord(
                            step=step,
                            provider=candidate.provider,
                            model=candidate.model,
                            prompt_sha=digest,
                            tokens_in=hit["tokens_in"],
                            tokens_out=hit["tokens_out"],
                            cost_cny=0.0,
                            cache_hit=True,
                            run_id=run_id,
                        )
                    )
                    return result

            for retry in range(self.max_retries + 1):
                started = time.perf_counter()
                try:
                    result = await provider.complete(
                        model=candidate.model,
                        messages=messages,
                        schema=schema,
                        schema_mode=candidate.schema_mode,
                        temperature=temp,
                        max_tokens=max_tokens or candidate.max_tokens,
                        timeout_s=candidate.timeout_s,
                    )
                except ProviderError as exc:
                    attempts.append(f"{candidate.provider}:{candidate.model}:{exc.code}")
                    await self._record(
                        LLMCallRecord(
                            step=step,
                            provider=candidate.provider,
                            model=candidate.model,
                            prompt_sha=digest,
                            latency_ms=int((time.perf_counter() - started) * 1000),
                            retry_no=retry,
                            ok=False,
                            error_code=exc.code,
                            run_id=run_id,
                        )
                    )
                    if not exc.retryable:
                        break
                    if retry < self.max_retries:
                        await self.sleep(self.backoff_base_s * (2**retry))
                    continue

                try:
                    cost = cost_for_model(
                        candidate.model,
                        result.usage.tokens_in,
                        result.usage.tokens_out,
                        pricing=self.config.pricing,
                        fx_usd_cny=self.fx,
                    )
                except UnknownModelPrice:
                    cost = 0.0  # 未取价模型只记账 tokens，不编造成本（Spec §13 O1）
                result.usage.cost_cny = cost
                await self._record(
                    LLMCallRecord(
                        step=step,
                        provider=candidate.provider,
                        model=candidate.model,
                        prompt_sha=digest,
                        tokens_in=result.usage.tokens_in,
                        tokens_out=result.usage.tokens_out,
                        cost_cny=cost,
                        latency_ms=result.latency_ms,
                        retry_no=retry,
                        run_id=run_id,
                    )
                )
                if self.cache is not None:
                    self.cache.put(
                        key,
                        {
                            "text": result.text,
                            "tokens_in": result.usage.tokens_in,
                            "tokens_out": result.usage.tokens_out,
                        },
                    )
                return result

        raise LLMUnavailableError(role, attempts)


def build_providers(config: LLMConfig, *, force_fake: bool = False) -> dict[str, LLMProvider]:
    """按配置构建 provider 实例；force_fake=True 时全部替换为 FakeLLM（单测/离线开发）。"""
    providers: dict[str, LLMProvider] = {}
    for name, pcfg in config.providers.items():
        if force_fake or pcfg.compat == "fake":
            providers[name] = FakeLLMProvider(name=name)
        else:
            providers[name] = OpenAICompatProvider(
                name=name, base_url=pcfg.base_url, api_key=pcfg.api_key
            )
    if force_fake:
        providers.setdefault("fake", FakeLLMProvider(name="fake"))
    return providers


@lru_cache(maxsize=4)
def _cached_router(config_path: str, force_fake: bool, cache_dir: str) -> ModelRouter:
    from pathlib import Path

    config = load_llm_config(Path(config_path))
    return ModelRouter(
        config,
        build_providers(config, force_fake=force_fake),
        cache=PromptCache(Path(cache_dir)),
    )


def get_router() -> ModelRouter:
    """FastAPI 依赖注入入口；离线开发走 FORCE_FAKE_LLM=1。"""
    from app.core.config import get_settings

    settings = get_settings()
    return _cached_router(str(settings.llm_config_file), settings.force_fake_llm, str(settings.cache_dir))
