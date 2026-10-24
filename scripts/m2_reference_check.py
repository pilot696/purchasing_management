"""M2 第二实现差分校验：生产 build_dimensions vs 独立公式实现。

用法：
    python scripts/m2_reference_check.py
输出：
    evals/m2_reference.json
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.agents.supplier import build_dimensions  # noqa: E402
from app.evaluation.m2_reference import reference_scores, reference_verdict  # noqa: E402
from app.scoring.supplier import DEFAULT_WEIGHTS, verdict_for, weighted_total  # noqa: E402


def sample(rng: random.Random) -> dict:
    return {
        "price": {
            "price_index": rng.choice([None, rng.uniform(0.7, 1.35)]),
            "sample_size": rng.randint(0, 30),
        },
        "delivery": {
            "otd": rng.choice([None, rng.uniform(0.5, 1.0)]),
            "avg_delay_days": rng.uniform(0, 12),
            "partial_ship_rate": rng.uniform(0, 0.4),
        },
        "quality": {
            "qc_pass_rate": rng.choice([None, rng.uniform(0.5, 1.0)]),
            "defect_rate": None,
        },
        "service": {
            "first_response_hours": rng.choice([None, rng.uniform(1, 80)]),
            "resolve_hours": rng.choice([None, rng.uniform(10, 120)]),
            "escalation_rate": rng.uniform(0, 0.5),
        },
        "compliance": {
            "cert_expiring": rng.random() < 0.2,
            "scope_partial": rng.random() < 0.15,
            "missing_security_cert": rng.random() < 0.2,
            "abnormal_flag": rng.random() < 0.05,
        },
    }


def main() -> None:
    started = time.perf_counter()
    rng = random.Random(42)
    weights = DEFAULT_WEIGHTS["IT_HW"]
    max_delta = 0.0
    deltas: list[float] = []
    verdict_mismatch = 0
    for _ in range(500):
        metrics = sample(rng)
        production = build_dimensions(metrics, weights)
        reference = reference_scores(metrics, weights)
        for dimension in production:
            delta = abs(dimension.score - reference[dimension.dimension])
            deltas.append(delta)
            max_delta = max(max_delta, delta)
        total = weighted_total(production)
        verdict = verdict_for(total, gate_reject=False, frozen=False, cold_start=False)
        reference_verdict_value = reference_verdict(total)
        if verdict != reference_verdict_value:
            verdict_mismatch += 1

    summary = {
        "samples": 500,
        "dimension_mae": round(sum(deltas) / len(deltas), 8) if deltas else 0.0,
        "max_abs_delta": round(max_delta, 8),
        "verdict_mismatch": verdict_mismatch,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": [
            "第二实现差分校验（不同代码重写公式/阈值），不是人工专家 gold；",
            "只能证明实现一致性，不能证明权重/阈值符合业务；",
            "随机指标固定 seed=42 可复现。",
        ],
    }
    (ROOT / "evals" / "m2_reference.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"M2_REFERENCE samples={summary['samples']} mae={summary['dimension_mae']} "
        f"max_delta={summary['max_abs_delta']} verdict_mismatch={verdict_mismatch}"
    )


if __name__ == "__main__":
    main()
