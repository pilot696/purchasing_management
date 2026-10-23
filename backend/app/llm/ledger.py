"""把每次 LLM 调用写入 `llm_call` 表并累加 `agent_run` 的 token/成本（Spec §4.2 记账）。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.router import LLMCallRecord
from app.models.tables import LlmCall


class LedgerRecorder:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def record(self, rec: LLMCallRecord) -> None:
        async with self._sf() as session:
            session.add(
                LlmCall(
                    run_id=rec.run_id,
                    step=rec.step,
                    provider=rec.provider,
                    model=rec.model,
                    prompt_sha=rec.prompt_sha,
                    tokens_in=rec.tokens_in,
                    tokens_out=rec.tokens_out,
                    cost_cny=rec.cost_cny,
                    latency_ms=rec.latency_ms,
                    retry_no=rec.retry_no,
                    cache_hit=rec.cache_hit,
                    schema_repair=rec.schema_repair,
                    ok=rec.ok,
                    error_code=rec.error_code,
                )
            )
            if rec.run_id is not None:
                await session.execute(
                    text(
                        "UPDATE agent_run SET tokens_in = tokens_in + :ti, tokens_out = tokens_out + :to, "
                        "cost_cny = cost_cny + :cost WHERE id = :rid"
                    ),
                    {"ti": rec.tokens_in, "to": rec.tokens_out, "cost": rec.cost_cny, "rid": rec.run_id},
                )
            await session.commit()
