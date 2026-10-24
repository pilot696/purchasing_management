"""聚合四套基线为 evals/latest.json（可重复生成，不再手工编辑快照）。

用法：
    python scripts/eval_report.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"


def load(name: str) -> dict:
    path = EVALS / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    extraction = load("extraction_baseline.json")
    extraction_vision = load("extraction_baseline_vision.json")
    extraction_independent = load("extraction_independent.json")
    m1 = load("m1_baseline.json")
    m2 = load("m2_baseline.json")
    m2_real = load("m2_baseline_real.json")
    m2_reference = load("m2_reference.json")
    m3 = load("m3_baseline.json")
    previous = load("latest.json")

    def pick(source: dict, *keys: str) -> dict:
        return {key: source[key] for key in keys if key in source}

    latest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seed": m3.get("dataset", {}).get("seed", 42),
        "llm_mode": previous.get(
            "llm_mode", "offline-fake-llm for full baselines; real gateway smoke validated separately"
        ),
        "extraction": {
            **pick(extraction, "docs", "route_distribution", "extractor_distribution", "scanned_needs_vlm", "per_doc_type", "invoice_field_accuracy", "invoice_field_n", "latency_ms"),
            "notes": "扫描件路由到 VLM 路径；离线未跑 VLM，失败数不代表版式精度",
        },
        "extraction_vision": {
            **pick(
                extraction_vision,
                "docs",
                "vision_mode",
                "per_doc_type",
                "invoice_field_accuracy",
                "invoice_field_n",
                "latency_ms",
                "llm_cost",
                "generated_at",
            ),
            "notes": "24 份扫描件（12 合同 + 12 发票）VLM 全部抽取成功；发票 3 字段 100%（n=12）；成本为 31 次调用估算",
        } if extraction_vision else {},
        "extraction_independent": {
            **pick(
                extraction_independent,
                "docs",
                "ok",
                "field_total",
                "field_matched",
                "field_accuracy",
                "elapsed_ms",
                "generated_at",
            ),
            "notes": "独立渲染器 + 手写 gold（4 份：20 位发票号/全角金额/PO/合同）；合成数据，非真实票据人工标注",
        } if extraction_independent else {},
        "m1": {
            **pick(m1, "cases", "verdict_accuracy", "findings_exact_accuracy", "finding_micro", "per_anomaly_verdict_accuracy", "retrieval_top1_accuracy", "retrieval_samples", "patches", "elapsed_ms"),
            "notes": "规则 DSL + 词法/精确/规格桶 RRF；gold 与数据工厂同源",
        },
        "m2": {
            **pick(m2, "suppliers", "gate_detection", "verdict_distribution", "confidence_distribution", "cold_start", "dimension_completeness", "total_score", "sensitivity", "llm_status", "elapsed_ms"),
            "notes": "无 M2 专家标注 gold；验证注入异常契约（检出/误杀/冷启动降级）",
        },
        "m2_real": {
            **pick(
                m2_real,
                "suppliers",
                "llm_mode",
                "llm_status",
                "llm_failures",
                "elapsed_ms",
                "elapsed_s",
                "llm_cost",
                "gate_detection",
                "verdict_distribution",
                "confidence_distribution",
                "cold_start",
                "dimension_completeness",
                "total_score",
                "sensitivity",
            ),
            "notes": "真实网关全量：23 家 LLM ok / 7 家闸门跳过 / 0 失败；金额为 23 次成功调用估算",
        } if m2_real else {},
        "m3": {
            "cases": m3.get("dataset", {}).get("cases"),
            "dataset": m3.get("dataset"),
            "hungarian": m3.get("hungarian"),
            "greedy": m3.get("greedy"),
            "notes": "gold 与 reconcile 同源；每组 1 行时匈牙利与贪心指标相同",
        },
        "m2_reference": {
            **pick(m2_reference, "samples", "dimension_mae", "max_abs_delta", "verdict_mismatch", "elapsed_ms", "generated_at"),
            "notes": "第二实现差分校验：不同代码重写五维公式/阈值，500 组随机指标；不是人工专家 gold",
        } if m2_reference else {},
        "engineering": previous.get("engineering", {}),
        "real_llm_smoke": previous.get("real_llm_smoke", {}),
        "pending_measurements": previous.get("pending_measurements", []),
    }
    (EVALS / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {EVALS / 'latest.json'}")
    print(f"  extraction docs={latest['extraction'].get('docs')}")
    if latest.get("extraction_vision"):
        print(
            f"  extraction_vision docs={latest['extraction_vision'].get('docs')} "
            f"cost_cny={latest['extraction_vision'].get('llm_cost', {}).get('cny')}"
        )
    if latest.get("extraction_independent"):
        print(
            f"  extraction_independent docs={latest['extraction_independent'].get('docs')} "
            f"accuracy={latest['extraction_independent'].get('field_accuracy')}"
        )
    print(f"  m1 cases={latest['m1'].get('cases')} verdict={latest['m1'].get('verdict_accuracy')}")
    print(f"  m2 suppliers={latest['m2'].get('suppliers')} gates={latest['m2'].get('gate_detection', {}).get('recall')}")
    if latest.get("m2_real"):
        print(
            f"  m2_real ok={latest['m2_real'].get('llm_status', {}).get('ok')} "
            f"failures={len(latest['m2_real'].get('llm_failures', []))} "
            f"cost_cny={latest['m2_real'].get('llm_cost', {}).get('cny')}"
        )
    print(f"  m3 cases={latest['m3'].get('cases')}")
    if latest.get("m2_reference"):
        print(
            f"  m2_reference mae={latest['m2_reference'].get('dimension_mae')} "
            f"verdict_mismatch={latest['m2_reference'].get('verdict_mismatch')}"
        )


if __name__ == "__main__":
    main()
