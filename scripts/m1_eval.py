"""M1 基线评测：DB 规则 + 混合检索 + 语义消歧 → 对比 120 条 gold。

产出 `evals/m1_baseline.json`：
    python scripts/m1_eval.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.agents.precheck import compute_precheck_report  # noqa: E402
from app.core.loop import ensure_windows_selector_loop  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.models import tables as t  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = PROJECT_ROOT / "data" / "gold" / "m1"
OUT_PATH = PROJECT_ROOT / "evals" / "m1_baseline.json"


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


async def run() -> dict:
    gold_files = sorted(GOLD_DIR.glob("*.json"))
    if not gold_files:
        raise SystemExit("缺少 M1 gold：先运行 scripts/seed.py --seed 42")
    gold_cases = [json.loads(path.read_text(encoding="utf-8")) for path in gold_files]

    session_factory = get_session_factory()
    started = time.perf_counter()
    verdict_ok = 0
    findings_exact = 0
    per_type: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    patch_stats = {"cases_with_patches": 0, "patch_total": 0}
    retrieval_ok = 0
    retrieval_total = 0
    mismatches: list[dict] = []
    per_anomaly: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "verdict_ok": 0})

    async with session_factory() as session:
        for gold in gold_cases:
            report = await compute_precheck_report(session, gold["pr_no"])
            expected_verdict = gold["expected_verdict"]
            per_anomaly[gold["anomaly"]]["total"] += 1
            if report["verdict"] == expected_verdict:
                verdict_ok += 1
                per_anomaly[gold["anomaly"]]["verdict_ok"] += 1

            expected = {(item["type"], item["field"], item["severity"]) for item in gold["expected_findings"]}
            got = {(finding["type"], finding["field"], finding["severity"]) for finding in report["findings"]}
            if expected == got:
                findings_exact += 1
            else:
                mismatches.append(
                    {
                        "pr": gold["pr_no"],
                        "anomaly": gold["anomaly"],
                        "expected_verdict": expected_verdict,
                        "got_verdict": report["verdict"],
                        "missing": sorted(expected - got),
                        "extra": sorted(got - expected),
                    }
                )
            for finding in report["findings"]:
                key = finding["type"]
                per_type[key]["tp" if (finding["type"], finding["field"], finding["severity"]) in expected else "fp"] += 1
            for item in expected:
                if item not in got:
                    per_type[item[0]]["fn"] += 1

            patches = report.get("patches") or []
            if patches:
                patch_stats["cases_with_patches"] += 1
                patch_stats["patch_total"] += len(patches)

            # 检索 top-1 命中率（与 DB 里 pr_item.sku_id 对比）
            pr = await session.scalar(select(t.Pr).where(t.Pr.pr_no == gold["pr_no"]))
            if pr is not None:
                db_items = (
                    await session.scalars(select(t.PrItem).where(t.PrItem.pr_id == pr.id))
                ).all()
                for db_item, report_item in zip(db_items, report["items"], strict=False):
                    if db_item.sku_id is None or report_item.get("ambiguous"):
                        continue
                    sku = await session.get(t.SkuCatalog, db_item.sku_id)
                    if sku is None:
                        continue
                    retrieval_total += 1
                    top = report_item["candidates"][0] if report_item["candidates"] else None
                    # 目录里同名同规格的 SKU 是等价类（不同 sku_code 可能同名），按等价类判定
                    if top and (top["sku_code"] == sku.sku_code or (top["name"] == sku.name and top["spec_bucket"] == sku.spec_bucket)):
                        retrieval_ok += 1

    totals = {"tp": 0, "fp": 0, "fn": 0}
    for counts in per_type.values():
        for key in totals:
            totals[key] += counts[key]
    precision = _ratio(totals["tp"], totals["tp"] + totals["fp"])
    recall = _ratio(totals["tp"], totals["tp"] + totals["fn"])
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0
    elapsed_ms = (time.perf_counter() - started) * 1000

    return {
        "cases": len(gold_cases),
        "verdict_accuracy": _ratio(verdict_ok, len(gold_cases)),
        "findings_exact_accuracy": _ratio(findings_exact, len(gold_cases)),
        "finding_micro": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": totals["tp"],
            "fp": totals["fp"],
            "fn": totals["fn"],
        },
        "per_anomaly_verdict_accuracy": {
            anomaly: _ratio(counts["verdict_ok"], counts["total"]) for anomaly, counts in sorted(per_anomaly.items())
        },
        "retrieval_top1_accuracy": _ratio(retrieval_ok, retrieval_total),
        "retrieval_samples": retrieval_total,
        "patches": patch_stats,
        "mismatches_sample": mismatches[:10],
        "elapsed_ms": round(elapsed_ms, 2),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": [
            "M1 走确定性链路（规则 DSL + 词法/结构化 RRF 检索 + 确定性歧义判定），不调用 LLM；",
            "gold 与数据工厂同源，指标代表『规则实现与 gold 契约一致』；检索 top-1 是真实 DB 数据上的检索质量；",
            "向量 leg 待 embedding provider 接入（ADR-020），当前 RRF 由词法 + 结构化两路融合。",
        ],
    }


def main() -> None:
    ensure_windows_selector_loop(force=True)
    summary = asyncio.run(run())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"written={OUT_PATH}")
    print("M1_EVAL_OK")


if __name__ == "__main__":
    main()
