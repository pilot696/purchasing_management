"""数据工厂 CLI：`python scripts/seed.py --seed 42`（或 `pwsh -File scripts/dev.ps1 seed`）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.loop import ensure_windows_selector_loop
from app.seed.config import SeedConfig
from app.seed.dataset import build_dataset
from app.seed.persist import persist_dataset


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成间接采购合成数据（Spec §10）")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data-dir", type=Path, default=None, help="默认 <repo>/data")
    parser.add_argument("--no-clean", action="store_true", help="保留既有 files/gold 并追加（默认清空重建）")
    parser.add_argument("--doc-groups", type=int, default=80, help="M3 单据组数（调试可调小）")
    parser.add_argument("--pr-count", type=int, default=120, help="M1 PR 数")
    return parser.parse_args()


async def _run(cfg: SeedConfig) -> dict:
    dataset = build_dataset(cfg)
    summary = await persist_dataset(dataset, cfg)
    summary["data_dir"] = str(cfg.data_dir)
    return summary


def main() -> None:
    ensure_windows_selector_loop(force=True)
    args = _parse()
    kwargs = {
        "seed": args.seed,
        "clean": not args.no_clean,
        "doc_groups": args.doc_groups,
        "pr_count": args.pr_count,
    }
    if args.data_dir is not None:
        kwargs["data_dir"] = args.data_dir
    cfg = SeedConfig(**kwargs)
    summary = asyncio.run(_run(cfg))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("SEED_OK")


if __name__ == "__main__":
    sys.exit(main())
