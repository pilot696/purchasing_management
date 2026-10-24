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
    m1 = load("m1_baseline.json")
    m2 = load("m2_baseline.json")
    m2_real = load("m2_baseline_real.json")
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
        "engineering": previous.get("engineering", {}),
        "real_llm_smoke": previous.get("real_llm_smoke", {}),
        "pending_measurements": previous.get("pending_measurements", []),
    }
    (EVALS / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {EVALS / 'latest.json'}")
    print(f"  extraction docs={latest['extraction'].get('docs')}")
    print(f"  m1 cases={latest['m1'].get('cases')} verdict={latest['m1'].get('verdict_accuracy')}")
    print(f"  m2 suppliers={latest['m2'].get('suppliers')} gates={latest['m2'].get('gate_detection', {}).get('recall')}")
    if latest.get("m2_real"):
        print(
            f"  m2_real ok={latest['m2_real'].get('llm_status', {}).get('ok')} "
            f"failures={len(latest['m2_real'].get('llm_failures', []))} "
            f"cost_cny={latest['m2_real'].get('llm_cost', {}).get('cny')}"
        )
    print(f"  m3 cases={latest['m3'].get('cases')}")


if __name__ == "__main__":
    main()


