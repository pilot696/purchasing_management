"""生成 6 页工作台 + 设计稿预览截图（docs/screens/*.png，供 README/简历使用）。

前置：
  1) API 已启动并托管前端 dist（默认 http://127.0.0.1:8013）；
  2) DB 已 seed，且已运行 `python scripts/demo_runs.py` 生成待复核/成功运行；
  3) `playwright install chromium` 已完成。

用法：
    python scripts/screenshots.py --base http://127.0.0.1:8013
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "screens"
DESIGN_PREVIEW = (ROOT / "design" / "preview.html").resolve().as_uri()


def find_runs(base: str) -> dict[str, str]:
    with httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
        payload = client.get("/api/v1/runs", params={"limit": 50}).json()
    by_module: dict[str, dict[str, str]] = {"m1": {}, "m2": {}, "m3": {}}
    for run in payload["items"]:
        by_module[run["module"]].setdefault(run["status"], run["id"])
    return {
        "m1_awaiting": by_module["m1"].get("awaiting_review", ""),
        "m2_awaiting": by_module["m2"].get("awaiting_review", ""),
        "m3_awaiting": by_module["m3"].get("awaiting_review", ""),
        "m2_succeeded": by_module["m2"].get("succeeded", ""),
    }


def shoot(page, name: str, waits: list[str]) -> None:
    for selector in waits:
        page.wait_for_selector(selector, timeout=20_000)
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT_DIR / name), full_page=True)
    print(f"{name}: {(OUT_DIR / name).stat().st_size} bytes")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8013")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ids = find_runs(base)
    print("runs:", ids)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome")
        except Exception:
            browser = playwright.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1440, "height": 1080}, device_scale_factor=1)
        page.add_init_script("localStorage.setItem('procurement.demoRole', 'buyer')")

        page.goto(base + "/")
        shoot(page, "01-run-center.png", ["text=待复核收件箱", "text=近期运行"])

        page.goto(f"{base}/precheck?run={ids['m1_awaiting']}")
        shoot(page, "02-m1-precheck.png", ["text=发现的问题", "text=建议补丁"])

        page.goto(f"{base}/supplier?run={ids['m2_awaiting']}")
        shoot(page, "03-m2-supplier.png", ["text=硬闸门（6 条）", "text=LLM 风险复核"])

        page.goto(f"{base}/matching?run={ids['m3_awaiting']}")
        shoot(page, "04-m3-matching.png", ["text=差异"])

        page.goto(f"{base}/rules")
        shoot(page, "05-rules.png", ["text=规则表"])

        page.goto(f"{base}/observability")
        shoot(page, "06-observability.png", ["text=LLM 调用记账"])

        page.goto(DESIGN_PREVIEW)
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT_DIR / "00-design-preview.png"), full_page=True)
        print(f"00-design-preview.png: {(OUT_DIR / '00-design-preview.png').stat().st_size} bytes")

        browser.close()

    pngs = sorted(OUT_DIR.glob("*.png"))
    print(f"SCREENSHOTS_OK count={len(pngs)}")
    for png in pngs:
        print(f"  {png.name}: {png.stat().st_size} bytes")


if __name__ == "__main__":
    sys.exit(main())

