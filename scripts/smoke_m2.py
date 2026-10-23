"""M2 端到端冒烟：真实 HTTP + SSE + HITL 准入结论（Spec §8.2 / §6）。

前置：DB 已 seed，API 已运行。默认用 SUP-001（干净供应商 → 五维评分 + 敏感性）。

用法：
    python scripts/smoke_m2.py --base http://127.0.0.1:8012
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import httpx  # noqa: E402

BROWSER_HITS = {"interrupt", "run.end", "error"}
SUPPLIER_CODE = "SUP-001"


def parse_sse_block(block: str) -> dict[str, Any] | None:
    event_type = "message"
    data_lines: list[str] = []
    event_id: int | None = None
    for line in block.split("\n"):
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line.startswith("id:"):
            try:
                event_id = int(line[3:].strip())
            except ValueError:
                event_id = None
    if not data_lines:
        return None
    try:
        payload = json.loads("\n".join(data_lines))
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
    role: str,
    timeout_s: float = 60.0,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    events: list[dict[str, Any]] = []
    async with client.stream(
        "GET",
        f"{base}/api/v1/runs/{run_id}/events",
        params={"after_seq": after_seq},
        headers={"X-Demo-Role": role},
        timeout=timeout_s,
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


def fail(message: str, payload: Any = None) -> None:
    print(f"[FAIL] {message}", file=sys.stderr)
    if payload is not None:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    raise SystemExit(1)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8012")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=30) as client:
        if (await client.get("/healthz")).json().get("db") is not True:
            fail("数据库未就绪（healthz db=false）")

        created = await client.post(
            "/api/v1/runs",
            json={"module": "m2", "subject": {"supplier_code": SUPPLIER_CODE, "mode": "review"}},
            headers={"X-Demo-Role": "requester"},
        )
        if created.status_code != 202:
            fail(f"创建 M2 运行失败：{created.status_code} {created.text}")
        run_id = created.json()["run_id"]
        print(f"[1/5] run created: {run_id}")

        events, interrupt = await stream_until(client, base, run_id, 0, BROWSER_HITS, role="requester")
        if interrupt is None or interrupt["type"] != "interrupt":
            fail("未收到 interrupt 事件", events[-1] if events else None)
        review = interrupt["data"]
        report = review.get("report") or {}
        nodes = [event["data"].get("node") for event in events if event["type"] == "node.end"]
        if not {"load_supplier", "gate_check", "aggregate_metrics", "score", "sensitivity", "llm_risk_review", "compose_result"}.issubset(set(nodes)):
            fail(f"节点事件不完整：{nodes}")
        if report.get("module") != "m2":
            fail("review 载荷缺少 M2 report", report)
        if len(report.get("dimensions") or []) != 5:
            fail("干净供应商应有 5 个维度分", report.get("dimensions"))
        last_seq = interrupt["id"] or 0
        print(
            f"[2/5] report ok：verdict={report['verdict']}，total={report['total']}，"
            f"confidence={report['confidence']}，llm={report.get('llm_status')}"
        )

        forbidden = await client.post(
            f"/api/v1/runs/{run_id}/resume",
            json={"review_id": review["review_id"], "decision": "accept"},
            headers={"X-Demo-Role": "requester"},
        )
        if forbidden.status_code != 403:
            fail(f"requester 复核 M2 应 403，实际 {forbidden.status_code}")
        resumed = await client.post(
            f"/api/v1/runs/{run_id}/resume",
            json={"review_id": review["review_id"], "decision": "accept", "note": "smoke m2"},
            headers={"X-Demo-Role": "buyer"},
        )
        if resumed.status_code != 200:
            fail(f"buyer resume 失败：{resumed.status_code} {resumed.text}")
        print("[3/5] RBAC ok（requester 403 / buyer 200）")

        tail, end_event = await stream_until(client, base, run_id, last_seq, BROWSER_HITS, role="buyer")
        if end_event is None or end_event["type"] != "run.end":
            fail("未收到 run.end 事件", tail[-1] if tail else None)
        if end_event["data"].get("status") != "succeeded":
            fail(f"run.end 状态异常：{end_event['data']}", end_event)

        snapshot = (await client.get(f"/api/v1/runs/{run_id}")).json()
        artifact = snapshot.get("artifact") or {}
        if artifact.get("decision") != "accept":
            fail("artifact.decision 不是 accept", artifact)
        print(f"[4/5] finalize ok：artifact.kind={artifact.get('kind') if 'kind' in artifact else 'supplier_eval'}")

        print(
            f"[5/5] snapshot ok：status={snapshot['run']['status']}，verdict={report['verdict']}，"
            f"sensitivity.flip_rate={(report.get('sensitivity') or {}).get('flip_rate')}"
        )
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "supplier": SUPPLIER_CODE,
                    "verdict": report["verdict"],
                    "total": report["total"],
                    "dimensions": len(report["dimensions"]),
                    "events_before_interrupt": len(events),
                    "events_after_resume": len(tail),
                },
                ensure_ascii=False,
            )
        )
        print("SMOKE_M2=OK")


if __name__ == "__main__":
    asyncio.run(main())
