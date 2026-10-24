"""独立文档抽取评测（非 seed 渲染器 / 手写 gold）。

用法：
    python scripts/independent_extraction_eval.py
输出：
    evals/extraction_independent.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.evaluation.independent_docs import DOC_CASES, compare_extract, render_case  # noqa: E402
from app.extraction.pdf_native import PARSERS  # noqa: E402


def main() -> None:
    started = time.perf_counter()
    results = []
    total_fields = matched_fields = 0
    for case in DOC_CASES:
        pdf = render_case(case)
        item = {"name": case["name"], "doc_type": case["doc_type"], "bytes": len(pdf)}
        try:
            extract, _ = PARSERS[case["doc_type"]](pdf)
            matched, total, failures = compare_extract(case["doc_type"], extract, case["gold"])
            item.update({"ok": matched == total, "matched": matched, "total": total, "failures": failures})
        except Exception as exc:  # noqa: BLE001 - 评测要保留单条失败而不是中断
            item.update({"ok": False, "error": f"{type(exc).__name__}: {exc}", "matched": 0, "total": 1, "failures": []})
            matched, total = 0, 1
        matched_fields += matched
        total_fields += total
        results.append(item)

    summary = {
        "docs": len(results),
        "ok": sum(1 for item in results if item["ok"]),
        "field_total": total_fields,
        "field_matched": matched_fields,
        "field_accuracy": round(matched_fields / total_fields, 4) if total_fields else 0.0,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": [
            "独立渲染器 + 手写 gold：不复用 seed 的 PDF 模板/数据集，也不是从解析结果反推 gold；",
            "仍是合成 PDF（含真实 20 位发票号、全角括号、￥/小写等格式变体），不能替代真实票据人工标注；",
            "每条文档的字段级失败保留在 results[].failures，便于定位版式差异。",
        ],
        "results": results,
    }
    out = ROOT / "evals" / "extraction_independent.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"INDEPENDENT_EXTRACTION docs={summary['docs']} ok={summary['ok']} accuracy={summary['field_accuracy']}")
    for item in results:
        print(f"  {item['name']}: {'OK' if item['ok'] else 'FAIL'}")
        for failure in item.get("failures", []):
            print(f"    - {failure}")


if __name__ == "__main__":
    main()
