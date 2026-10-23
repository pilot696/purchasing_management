"""Windows 友好的 uvicorn 入口。

必须走本文件而不是 `python -m uvicorn app.main:app`：
uvicorn ≥0.36 在**单进程**模式下会把事件循环工厂硬编码为 `ProactorEventLoop`
（见 `uvicorn/loops/asyncio.py`），而 psycopg 异步驱动不支持 Proactor（GOTCHAS G-009）。
本入口在单进程模式下显式覆盖 loop factory 为 `SelectorEventLoop`；
`--reload` 模式则利用 uvicorn 的 `use_subprocess=True` 路径（该路径本身会选择 Selector）。

用法：
    python run.py --port 8000
    python run.py --port 8000 --reload
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.loop import ensure_windows_selector_loop

ensure_windows_selector_loop(force=True)

import uvicorn  # noqa: E402  （导入顺序是刻意的）


def main() -> None:
    parser = argparse.ArgumentParser(description="Procurement AI Agent Workbench API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    if args.reload:
        # use_subprocess=True → uvicorn 内部使用 SelectorEventLoop，Windows 下 psycopg 可用
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=True,
            log_level=args.log_level,
        )
        return

    config = uvicorn.Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    if sys.platform == "win32":
        config.get_loop_factory = lambda: asyncio.SelectorEventLoop  # type: ignore[method-assign]
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
