"""生成一组演示运行（截图/录屏用）：M1/M2/M3 各一条待复核 + 一条已完成 M2。

前置：API 已启动（默认 http://127.0.0.1:8013），DB 已 seed。
用法：
    python scripts/demo_runs.py --base http://127.0.0.1:8013
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import httpx  # noqa: E402

ROLE = {"X-Demo-Role": "buyer"}


async def wait_status(client: httpx.AsyncClient, run_id: str, targets: set[str], timeout_s: float = 180) -> str:
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        payload = (await client.get(f"/api/v1/runs/{run_id}")).json()
        status = payload["run"]["status"]
        if status in targets:
            return status
        await asyncio.sleep(0.5)
    raise TimeoutError(f"run {run_id} 未在 {timeout_s}s 内进入 {targets}")


async def start(client: httpx.AsyncClient, module: str, subject: dict) -> str:
    response = await client.post("/api/v1/runs", json={"module": module, "subject": subject}, headers=ROLE)
    response.raise_for_status()
    return response.json()["run_id"]


async def accept(client: httpx.AsyncClient, run_id: str) -> None:
    snapshot = (await client.get(f"/api/v1/runs/{run_id}")).json()
    review = snapshot.get("review")
    if not review:
        return
    response = await client.post(
        f"/api/v1/runs/{run_id}/resume",
        json={"review_id": review["id"], "decision": "accept", "note": "demo accept"},
        headers=ROLE,
    )
    if response.status_code != 200:
        raise RuntimeError(f"resume failed {response.status_code}: {response.text}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8013")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    async with httpx.AsyncClient(base_url=base, timeout=60, trust_env=False) as client:
        pending: dict[str, str] = {}
        for module, subject in [
            ("m1", {"pr_no": "PR-2026-0001"}),
            ("m3", {"po_no": "PO-2026-0001", "invoice_nos": ["INV-2026-3000"]}),
        ]:
            run_id = await start(client, module, subject)
            await wait_status(client, run_id, {"awaiting_review", "failed"})
            pending[module] = run_id
            print(f"{module} awaiting: {run_id}")

        # M2 用真实 LLM 复核（SUP-001 干净供应商）；截图会显示 LLM 风险卡
        m2_id = await start(client, "m2", {"supplier_code": "SUP-001", "mode": "review"})
        await wait_status(client, m2_id, {"awaiting_review", "failed"}, timeout_s=240)
        pending["m2"] = m2_id
        print(f"m2 awaiting: {m2_id}")

        # 再跑一条并采纳，给可观测页提供带 LLM 调用的成功运行（最新记录）
        m2_done = await start(client, "m2", {"supplier_code": "SUP-003", "mode": "review"})
        await wait_status(client, m2_done, {"awaiting_review", "failed"}, timeout_s=240)
        await accept(client, m2_done)
        await wait_status(client, m2_done, {"succeeded", "failed"}, timeout_s=120)

        print(json.dumps({**pending, "m2_succeeded": m2_done}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
