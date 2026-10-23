"""M3 端到端冒烟：真实 HTTP + SSE + HITL + PDF 抽屉数据（Spec §8.2 / §9.4）。

前置：PostgreSQL 已启动并 seed（`python scripts/seed.py --seed 42`），API 已运行。

用法：
    python scripts/smoke_m3.py --base http://127.0.0.1:8012
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
PO_NO = "PO-2026-0001"
INVOICE_NO = "INV-2026-3000"


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
            json={"module": "m3", "subject": {"po_no": PO_NO, "invoice_nos": [INVOICE_NO]}},
            headers={"X-Demo-Role": "buyer"},
        )
        if created.status_code != 202:
            fail(f"创建 M3 运行失败：{created.status_code} {created.text}")
        run_id = created.json()["run_id"]
        print(f"[1/6] run created: {run_id}")

        events, interrupt = await stream_until(client, base, run_id, 0, BROWSER_HITS, role="buyer")
        if interrupt is None or interrupt["type"] != "interrupt":
            fail("未收到 interrupt 事件", events[-1] if events else None)
        review = interrupt["data"]
        nodes = [e["data"].get("node") for e in events if e["type"] == "node.end"]
        required_nodes = {"load_subject", "normalize", "align", "reconcile", "compose_report"}
        if not required_nodes.issubset(set(nodes)):
            fail(f"节点事件不完整：{nodes}")
        print(f"[2/6] interrupt at seq={interrupt['id']}，节点={sorted(required_nodes)}")

        report = review.get("report") or {}
        if report.get("module") != "m3":
            fail("review 载荷缺少 M3 report", review)
        invoice_doc = (report.get("documents") or {}).get("invoice")
        if not invoice_doc:
            fail("report.documents.invoice 缺失（PDF 抽屉无法打开）", report.get("documents"))
        if not report.get("po_lines") or not report.get("invoice_lines"):
            fail("report 缺少 po_lines/invoice_lines（对齐表无法渲染）")
        last_seq = interrupt["id"] or 0
        print(
            f"[3/6] report ok：overall={report['overall']}，diffs={len(report['diffs'])}，"
            f"invoice_doc={invoice_doc[:8]}…"
        )

        page = await client.get(f"/api/v1/documents/{invoice_doc}/page.png?page=1")
        if page.status_code != 200 or not page.content.startswith(b"\x89PNG\r\n\x1a\n"):
            fail(f"page.png 异常：{page.status_code} len={len(page.content)}")
        extracted = await client.post(f"/api/v1/documents/{invoice_doc}/extract")
        if extracted.status_code != 200:
            fail(f"抽取失败：{extracted.status_code} {extracted.text}")
        annotations = (await client.get(f"/api/v1/documents/{invoice_doc}/annotations")).json()
        if annotations["total"] == 0:
            fail("原生发票抽取后应有 bbox 注解", annotations)
        print(f"[4/6] pdf ok：page.png {len(page.content)}B，annotations={annotations['total']}")

        forbidden = await client.post(
            f"/api/v1/runs/{run_id}/resume",
            json={"review_id": review["review_id"], "decision": "accept"},
            headers={"X-Demo-Role": "buyer"},
        )
        if forbidden.status_code != 403:
            fail(f"buyer 复核 M3 应 403，实际 {forbidden.status_code}")
        resumed = await client.post(
            f"/api/v1/runs/{run_id}/resume",
            json={"review_id": review["review_id"], "decision": "accept", "note": "smoke m3"},
            headers={"X-Demo-Role": "finance"},
        )
        if resumed.status_code != 200:
            fail(f"finance resume 失败：{resumed.status_code} {resumed.text}")
        print(f"[5/6] RBAC ok（buyer 403 / finance 200）")

        tail, end_event = await stream_until(client, base, run_id, last_seq, BROWSER_HITS, role="finance")
        if end_event is None or end_event["type"] != "run.end":
            fail("未收到 run.end 事件", tail[-1] if tail else None)
        if end_event["data"].get("status") != "succeeded":
            fail(f"run.end 状态异常：{end_event['data']}", end_event)

        snapshot = (await client.get(f"/api/v1/runs/{run_id}")).json()
        artifact = snapshot.get("artifact") or {}
        if artifact.get("decision") != "accept":
            fail("artifact.decision 不是 accept", artifact)
        if (artifact.get("report") or {}).get("overall") != report["overall"]:
            fail("artifact.report.overall 与 interrupt 时不一致", artifact.get("report"))
        print(f"[6/6] snapshot ok：status={snapshot['run']['status']}，artifact.decision=accept")

        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "overall": report["overall"],
                    "diffs": len(report["diffs"]),
                    "annotations": annotations["total"],
                    "events_before_interrupt": len(events),
                    "events_after_resume": len(tail),
                },
                ensure_ascii=False,
            )
        )
        print("SMOKE_M3=OK")


if __name__ == "__main__":
    asyncio.run(main())
