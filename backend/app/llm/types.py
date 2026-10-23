"""LLM 网关的类型契约（Spec §4.2/§4.3）。所有 provider 与 router 共用这一组模型。"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

Role = str
MessageRole = Literal["system", "user", "assistant", "tool"]


class Message(BaseModel):
    role: MessageRole
    content: str


class Usage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0
    cache_hit: bool = False


class LLMResult(BaseModel):
    provider: str
    model: str
    text: str
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    raw: dict[str, Any] | None = None


class ProviderError(Exception):
    """provider 层错误。retryable 决定网关是否重试 / 走 fallback。"""

    def __init__(self, code: str, message: str, *, retryable: bool = True, status_code: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        schema_mode: str = "json_object",
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout_s: float = 60.0,
    ) -> LLMResult: ...
