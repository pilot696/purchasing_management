"""端到端冒烟（真实 HTTP + SSE + HITL 恢复 + 快照校验）。

前置：PostgreSQL 已启动并完成 `alembic upgrade head`，API 已在 --base 端口运行。

用法：
    python scripts/smoke_e2e.py --base http://127.0.0.1:8012

验证链路（Spec §8.2）：
    POST /runs → SSE(run.start/node.start/node.end*N/interrupt)
    → 角色越权 resume 返回 403 → buyer resume 200 → SSE(finalize/run.end)
    → GET /runs/{id} 快照 status=succeeded + artifact.decision=accept
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

# psycopg 无关，但保持与后端一致的 Windows 事件循环策略（httpx 在两种循环下都能跑）
if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import httpx

BROWSER_HITS = {"interrupt", "run.end", "error"}


def parse_sse_block(block: str) -> dict[str, Any] | None:
    event_id = 0
    event_type: str | None = None
    data = ""
    for line in block.split("\n"):
        if not line or line.startswith(":") or line.startswith("retry:"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "id":
            try:
                event_id = int(value)
            except ValueError:
                event_id = 0
        elif field == "event":
            event_type = value
        elif field == "data":
            data = f"{data}\n{value}" if data else value
    if not event_type or not data:
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    return {"id": event_id, "type": event_type, "data": payload}


async def stream_until(
    client: httpx.AsyncClient,
    base: str,
    run_id: str,
    after_seq: int,
    stop_types: set[str],
    *,
    role: str = "buyer",
    timeout_s: float = 60.0,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    events: list[dict[str, Any]] = []
    url = f"{base}/api/v1/runs/{run_id}/events"
    async with client.stream(
        "GET", url, params={"after_seq": after_seq}, headers={"X-Demo-Role": role}, timeout=timeout_s
    ) as response:
        response.raise_for_status()
        buffer = ""
        async for chunk in response.aiter_text():
            buffer = (buffer + chunk).replace("\r\n", "\n")
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                event = parse_sse_block(block)
                if event is None:
                    continue
                events.append(event)
                if event["type"] in stop_types:
                    return events, event
    return events, None


def fail(message: str, event: dict[str, Any] | None = None) -> None:
    print(f"[FAIL] {message}", file=sys.stderr)
    if event is not None:
        print(json.dumps(event, ensure_ascii=False, indent=2), file=sys.stderr)
    raise SystemExit(1)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8012")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        # trust_env=False：本机冒烟必须绕过 HTTP(S)_PROXY，否则会被代理拦成 502
        health = await client.get(f"{base}/healthz")
        if health.status_code != 200 or not health.json().get("db"):
            fail(f"数据库不可用：status={health.status_code} body={health.text!r}")

        created = await client.post(
            f"{base}/api/v1/runs",
            json={"module": "m2", "subject": {"supplier_code": "SUP-001"}},
            headers={"X-Demo-Role": "buyer"},
        )
        if created.status_code != 202:
            fail(f"创建 run 失败：{created.status_code} {created.text}")
        run_id = created.json()["run_id"]
        print(f"[1/5] run created: {run_id}")

        events, interrupt = await stream_until(client, base, run_id, 0, BROWSER_HITS)
        seqs = [e["id"] for e in events]
        if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
            fail("事件 seq 不是严格递增", events[-1] if events else None)
        if interrupt is None or interrupt["type"] != "interrupt":
            fail("未收到 interrupt 事件", events[-1] if events else None)
        node_starts = {e["data"].get("node") for e in events if e["type"] == "node.start"}
        print(f"[2/5] received {len(events)} events, nodes={sorted(n for n in node_starts if n)}")

        review = interrupt["data"]
        review_id = review["review_id"]
        last_seq = events[-1]["id"]

        forbidden = await client.post(
            f"{base}/api/v1/runs/{run_id}/resume",
            json={"review_id": review_id, "decision": "accept"},
            headers={"X-Demo-Role": "requester"},
        )
        if forbidden.status_code != 403:
            fail(f"requester 不应能复核 m2（期望 403，实际 {forbidden.status_code}）")
        print("[3/5] RBAC ok: requester -> 403")

        resumed = await client.post(
            f"{base}/api/v1/runs/{run_id}/resume",
            json={"review_id": review_id, "decision": "accept", "note": "smoke_e2e"},
            headers={"X-Demo-Role": "buyer"},
        )
        if resumed.status_code != 200:
            fail(f"resume 失败：{resumed.status_code} {resumed.text}")
        print(f"[4/5] resumed by buyer at seq={last_seq}")

        tail, end_event = await stream_until(client, base, run_id, last_seq, BROWSER_HITS)
        if end_event is None:
            fail("未收到 run.end 事件", tail[-1] if tail else None)
        if end_event["type"] == "error":
            fail("run 以 error 结束", end_event)
        if end_event["data"].get("status") != "succeeded":
            fail(f"run.end 状态不是 succeeded：{end_event['data'].get('status')}")

        snapshot = (await client.get(f"{base}/api/v1/runs/{run_id}")).json()
        run = snapshot["run"]
        if run["status"] != "succeeded":
            fail(f"快照状态不是 succeeded：{run['status']}")
        if snapshot.get("review") is not None:
            fail("终态仍存在 pending 复核任务")
        artifact = snapshot.get("artifact") or {}
        if artifact.get("decision") != "accept":
            fail(f"artifact 决策不是 accept：{artifact}")
        print("[5/5] snapshot ok: status=succeeded, artifact.decision=accept")

        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "events_before_interrupt": len(events),
                    "events_after_resume": len(tail),
                    "artifact_id": end_event["data"].get("artifact_id"),
                    "tokens_in": run["tokens_in"],
                    "tokens_out": run["tokens_out"],
                    "cost_cny": run["cost_cny"],
                },
                ensure_ascii=False,
            )
        )
        print("SMOKE_E2E=OK")


if __name__ == "__main__":
    asyncio.run(main())


