"""Provider 实现：Fake（零网络，P0/单测默认）与 OpenAI 兼容（真实调用）。"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.llm.types import LLMProvider, LLMResult, Message, ProviderError, Usage


def _estimate_tokens(text: str) -> int:
    """粗略估算：中文约 1 token/字，英文约 1 token/4 字符；仅用于假实现与日志。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return max(1, cjk + (len(text) - cjk) // 4)


class FakeLLMProvider:
    """确定性的假 provider：按角色返回固定文本，可注入失败次数用于测重试/降级。

    - `responses`: {role_model_key: text}，key 用 `f"{provider}:{model}"` 或 `"*"`；
    - `fail_times`: 前 N 次调用抛可重试错误（模拟 503）；
    - `latency_ms`: 固定延迟，默认 0。
    """

    def __init__(
        self,
        *,
        name: str,
        responses: dict[str, str] | None = None,
        fail_times: int = 0,
        latency_ms: int = 0,
    ) -> None:
        self.name = name
        self.responses = responses or {}
        self.fail_times = fail_times
        self.latency_ms = latency_ms
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        schema: type | None = None,
        schema_mode: str = "json_object",
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout_s: float = 60.0,
    ) -> LLMResult:
        started = time.perf_counter()
        self.calls.append({"model": model, "messages": [m.model_dump() for m in messages]})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ProviderError("UPSTREAM_503", f"{self.name} injected failure", retryable=True, status_code=503)
        if self.latency_ms:
            import asyncio

            await asyncio.sleep(self.latency_ms / 1000)
        key = f"{self.name}:{model}"
        text = self.responses.get(key) or self.responses.get("*") or '{"ok": true}'
        prompt_text = "".join(m.content for m in messages)
        usage = Usage(tokens_in=_estimate_tokens(prompt_text), tokens_out=_estimate_tokens(text))
        return LLMResult(
            provider=self.name,
            model=model,
            text=text,
            usage=usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw={"fake": True, "schema_mode": schema_mode, "schema": getattr(schema, "__name__", None)},
        )


class OpenAICompatProvider:
    """OpenAI 兼容 /chat/completions（DeepSeek / DashScope compatible-mode / 智谱均可）。"""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client

    def _payload(
        self,
        *,
        model: str,
        messages: list[Message],
        schema: type | None,
        schema_mode: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if schema is not None:
            if schema_mode == "json_schema":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
                }
            else:
                payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        schema: type | None = None,
        schema_mode: str = "json_object",
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout_s: float = 60.0,
    ) -> LLMResult:
        started = time.perf_counter()
        payload = self._payload(
            model=model,
            messages=messages,
            schema=schema,
            schema_mode=schema_mode,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        client = self._client or httpx.AsyncClient(timeout=timeout_s)
        try:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("LLM_TIMEOUT", str(exc), retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("LLM_UPSTREAM", str(exc), retryable=True) from exc
        finally:
            if self._client is None:
                await client.aclose()
        if resp.status_code == 429:
            raise ProviderError("LLM_QUOTA", resp.text[:200], retryable=True, status_code=429)
        if resp.status_code >= 500:
            raise ProviderError("LLM_UPSTREAM", resp.text[:200], retryable=True, status_code=resp.status_code)
        if resp.status_code >= 400:
            raise ProviderError("LLM_BAD_REQUEST", resp.text[:200], retryable=False, status_code=resp.status_code)
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        u = data.get("usage") or {}
        return LLMResult(
            provider=self.name,
            model=model,
            text=text,
            usage=Usage(tokens_in=int(u.get("prompt_tokens", 0)), tokens_out=int(u.get("completion_tokens", 0))),
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw=data,
        )


PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "openai_compat": OpenAICompatProvider,
    "fake": FakeLLMProvider,
}
