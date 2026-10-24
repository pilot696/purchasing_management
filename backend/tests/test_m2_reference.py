"""M2 第二实现差分测试（非人工 gold，只保证公式实现一致）。"""

from __future__ import annotations

import random

from app.agents.supplier import build_dimensions
from app.evaluation.m2_reference import reference_scores, reference_verdict
from app.scoring.supplier import DEFAULT_WEIGHTS, verdict_for, weighted_total


def _sample(rng: random.Random) -> dict:
    return {
        "price": {"price_index": rng.choice([None, rng.uniform(0.7, 1.35)]), "sample_size": 12},
        "delivery": {
            "otd": rng.choice([None, rng.uniform(0.5, 1.0)]),
            "avg_delay_days": rng.uniform(0, 12),
            "partial_ship_rate": rng.uniform(0, 0.4),
        },
        "quality": {"qc_pass_rate": rng.choice([None, rng.uniform(0.5, 1.0)]), "defect_rate": None},
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


def test_m2_second_implementation_matches_production() -> None:
    rng = random.Random(7)
    weights = DEFAULT_WEIGHTS["IT_HW"]
    max_delta = 0.0
    for _ in range(200):
        metrics = _sample(rng)
        production = build_dimensions(metrics, weights)
        reference = reference_scores(metrics, weights)
        for dimension in production:
            max_delta = max(max_delta, abs(dimension.score - reference[dimension.dimension]))
        total = weighted_total(production)
        assert verdict_for(total) == reference_verdict(total)
    assert max_delta < 1e-4
