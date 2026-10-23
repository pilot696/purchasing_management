"""M3 基线评测：normalizer → align（匈牙利/贪心）→ reconcile，对照 80 组 gold。

产出 `evals/m3_baseline.json`（可直接引用到简历/报告）：
    python scripts/m3_eval.py
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.matching.align import align_greedy, align_hungarian  # noqa: E402
from app.matching.tolerance import policies_from_rows, reconcile  # noqa: E402
from app.seed.config import SeedConfig  # noqa: E402
from app.seed.dataset import build_dataset  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = PROJECT_ROOT / "evals" / "m3_baseline.json"


def _po_for_reconcile(group: dict) -> dict:
    received: dict[int, float] = {}
    for receipt in group["receipts"]:
        for line in receipt["lines"]:
            if str(line.get("qc_result", "pass")) == "fail":
                continue
            received[int(line["po_line_no"])] = received.get(int(line["po_line_no"]), 0.0) + float(line["qty"])
    po = dict(group["po"])
    po["category_code"] = group["category_code"]
    po["lines"] = [
        {**line, "qty_invoiced": 0.0, "qty_received": received.get(int(line["line_no"]), 0.0)}
        for line in group["po"]["lines"]
    ]
    return po


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def evaluate(dataset: dict, *, hungarian: bool = True) -> dict:
    policies = policies_from_rows(dataset["master"]["tolerance"])
    gold_by_case = {case["case_id"]: case for case in dataset["m3_gold"]}
    per_type: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fn": 0, "fp": 0})
    overall_correct = 0
    line_tp = line_fp = line_fn = 0
    mismatches: list[dict] = []
    started = time.perf_counter()

    for group in dataset["doc_groups"]:
        gold = gold_by_case[group["case_id"]]
        po = _po_for_reconcile(group)
        invoice = group["invoices"][0]
        align_fn = align_hungarian if hungarian else align_greedy
        align = align_fn(po["lines"], invoice["lines"])
        result = reconcile(
            po=po,
            invoice=invoice,
            align=align,
            contract=group["contract"],
            receipts=group["receipts"],
            policies=policies,
            requires_gr=group["requires_gr"],
            category_code=group["category_code"],
        )

        expected_types = {diff["type"] for diff in gold["expected_diffs"]}
        predicted_types = {diff.diff_type for diff in result.diffs}
        for diff_type in expected_types:
            per_type[diff_type]["tp" if diff_type in predicted_types else "fn"] += 1
        for diff_type in predicted_types - expected_types:
            per_type[diff_type]["fp"] += 1

        if result.overall == gold["expected_overall"]:
            overall_correct += 1
        elif len(mismatches) < 12:
            mismatches.append(
                {
                    "case_id": group["case_id"],
                    "expected_overall": gold["expected_overall"],
                    "actual_overall": result.overall,
                    "expected_diffs": sorted(expected_types),
                    "predicted_diffs": sorted(predicted_types),
                    "scanned": gold["scanned"],
                }
            )

        expected_pairs = {(m["po_line_no"], m["invoice_line_no"]) for m in gold["expected_line_matches"]}
        predicted_pairs = {(m.po_line_no, m.invoice_line_no) for m in result.line_matches if m.invoice_line_no}
        line_tp += len(expected_pairs & predicted_pairs)
        line_fp += len(predicted_pairs - expected_pairs)
        line_fn += len(expected_pairs - predicted_pairs)

    elapsed_ms = (time.perf_counter() - started) * 1000
    totals = {"tp": 0, "fn": 0, "fp": 0}
    for counts in per_type.values():
        for key in totals:
            totals[key] += counts[key]
    precision = _ratio(totals["tp"], totals["tp"] + totals["fp"])
    recall = _ratio(totals["tp"], totals["tp"] + totals["fn"])
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0

    return {
        "variant": "hungarian" if hungarian else "greedy",
        "cases": len(dataset["doc_groups"]),
        "overall_accuracy": _ratio(overall_correct, len(dataset["doc_groups"])),
        "diff_detection": {
            "micro_precision": precision,
            "micro_recall": recall,
            "micro_f1": f1,
            "tp": totals["tp"],
            "fp": totals["fp"],
            "fn": totals["fn"],
            "per_type": {
                diff_type: {
                    "recall": _ratio(counts["tp"], counts["tp"] + counts["fn"]),
                    "precision": _ratio(counts["tp"], counts["tp"] + counts["fp"]),
                    "tp": counts["tp"],
                    "fn": counts["fn"],
                    "fp": counts["fp"],
                }
                for diff_type, counts in sorted(per_type.items())
            },
        },
        "line_alignment": {
            "precision": _ratio(line_tp, line_tp + line_fp),
            "recall": _ratio(line_tp, line_tp + line_fn),
            "f1": round(
                2 * _ratio(line_tp, line_tp + line_fp) * _ratio(line_tp, line_tp + line_fn)
                / (_ratio(line_tp, line_tp + line_fp) + _ratio(line_tp, line_tp + line_fn)),
                4,
            )
            if line_tp
            else 0.0,
            "tp": line_tp,
            "fp": line_fp,
            "fn": line_fn,
        },
        "mismatches_sample": mismatches,
        "elapsed_ms": round(elapsed_ms, 2),
    }


def main() -> None:
    dataset = build_dataset(SeedConfig(seed=42))
    hungarian = evaluate(dataset, hungarian=True)
    greedy = evaluate(dataset, hungarian=False)
    summary = {
        "dataset": {
            "seed": 42,
            "cases": hungarian["cases"],
            "requires_gr_mix": {
                "true": sum(1 for g in dataset["doc_groups"] if g["requires_gr"]),
                "false": sum(1 for g in dataset["doc_groups"] if not g["requires_gr"]),
            },
            "scanned": sum(1 for g in dataset["doc_groups"] if g["scanned"]),
            "partial_invoice": sum(1 for g in dataset["doc_groups"] if g["partial_invoice"]),
        },
        "hungarian": hungarian,
        "greedy": {
            "overall_accuracy": greedy["overall_accuracy"],
            "diff_micro_f1": greedy["diff_detection"]["micro_f1"],
            "line_alignment_f1": greedy["line_alignment"]["f1"],
        },
        "notes": [
            "重要：gold 由与 reconcile 同源的规则生成，1.0 只证明『管线实现与 gold 契约一致』（回归基线），"
            "不代表真实业务精度；真实精度需在真实票据上评测。",
            "种子数据每组只有 1 个 PO 行项目，因此匈牙利与贪心的行对齐指标相同；",
            "匈牙利相对贪心的优势由单测 test_hungarian_beats_greedy_on_global_conflict 用冲突得分矩阵证明。",
            "本基线只覆盖确定性路径（不调用 LLM），扫描件抽取准确率见 extraction_baseline.json。",
        ],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"written={OUT_PATH}")
    print("M3_EVAL_OK")


if __name__ == "__main__":
    main()
