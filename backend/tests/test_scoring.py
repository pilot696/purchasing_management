"""M2 确定性评分与硬闸门单测（纯函数）。"""

from __future__ import annotations

from datetime import date

from app.scoring.supplier import (
    DimensionScore,
    clamp,
    compliance_score,
    confidence,
    delivery_score,
    evaluate_gates,
    gate_verdict,
    normalize_weights,
    piecewise,
    price_score,
    quality_score,
    sensitivity_analysis,
    service_score,
    verdict_for,
    weighted_total,
)


def test_piecewise_interpolates_and_clamps() -> None:
    points = [(4.0, 100.0), (8.0, 85.0), (24.0, 70.0)]
    assert piecewise(2.0, points) == 100.0
    assert piecewise(6.0, points) == 92.5
    assert piecewise(100.0, points) == 70.0
    assert piecewise(10.0, []) == 0.0


def test_price_score_curve() -> None:
    assert price_score(0.85) == 100.0
    assert price_score(1.0) == 100.0
    assert price_score(1.1) == 88.0
    assert price_score(1.2) == 76.0
    assert price_score(2.0) == 0.0
    assert price_score(None) == 60.0


def test_delivery_quality_service_scores() -> None:
    assert delivery_score(0.95) == 95.0
    assert delivery_score(0.95, avg_delay_days=8) == 85.5
    assert delivery_score(None) == 60.0
    assert quality_score(0.95, 0.05) == 95.0
    assert quality_score(None, None) == 60.0
    assert service_score(4.0) == 100.0
    assert service_score(30.0) == 65.0
    assert service_score(4.0, escalation_rate=0.3) == 94.0
    assert service_score(None) == 60.0


def test_compliance_score_deductions() -> None:
    assert compliance_score() == 100.0
    assert compliance_score(cert_expiring=True) == 80.0
    assert compliance_score(scope_partial=True) == 85.0
    assert compliance_score(missing_security_cert=True) == 70.0
    assert compliance_score(any_abnormal_flag=True) == 0.0


def test_weighted_total_and_verdict_mapping() -> None:
    scores = [
        DimensionScore("price", 90, 0.25, {}, 1),
        DimensionScore("delivery", 85, 0.30, {}, 1),
        DimensionScore("quality", 95, 0.20, {}, 1),
        DimensionScore("service", 80, 0.15, {}, 1),
        DimensionScore("compliance", 100, 0.10, {}, 1),
    ]
    total = weighted_total(scores)
    assert total == 89.0
    assert verdict_for(total) == "approve"
    assert verdict_for(70) == "conditional"
    assert verdict_for(50) == "reject"
    assert verdict_for(50, cold_start=True) == "conditional"
    assert verdict_for(95, gate_reject=True) == "reject"
    assert verdict_for(95, frozen=True) == "frozen"


def test_weights_normalized() -> None:
    weights = normalize_weights({"price": 2, "delivery": 2})
    assert weights == {"price": 0.5, "delivery": 0.5}
    assert sum(normalize_weights({"a": 0, "b": 0}).values()) == 1.0


def test_confidence_levels() -> None:
    high = confidence(data_completeness=1.0, n_lines=20, months_since_last_order=0.0)
    assert high["level"] == "high"
    low = confidence(data_completeness=0.2, n_lines=0, months_since_last_order=24.0)
    assert low["level"] == "low"


def test_sensitivity_is_deterministic() -> None:
    scores = [
        DimensionScore("price", 88, 0.25, {}, 3),
        DimensionScore("delivery", 62, 0.30, {}, 3),
        DimensionScore("quality", 90, 0.20, {}, 3),
        DimensionScore("service", 80, 0.15, {}, 3),
        DimensionScore("compliance", 100, 0.10, {}, 3),
    ]
    weights = {"price": 0.25, "delivery": 0.30, "quality": 0.20, "service": 0.15, "compliance": 0.10}
    first = sensitivity_analysis(scores, base_weights=weights)
    second = sensitivity_analysis(scores, base_weights=weights)
    assert first == second
    assert first["samples"] == 300
    assert 0.0 <= first["flip_rate"] <= 1.0
    assert first["most_sensitive"] in {"price", "delivery", "quality", "service", "compliance", "none"}


def _certs(**overrides) -> list[dict]:
    base = [
        {"cert_no": "营业执照-1", "cert_type": "营业执照", "valid_to": "2030-01-01"},
        {"cert_no": "ISO27001-1", "cert_type": "ISO27001", "valid_to": "2030-01-01"},
    ]
    if overrides.get("no_license"):
        base = [cert for cert in base if cert["cert_type"] != "营业执照"]
    return base


def test_gates_detection() -> None:
    assert evaluate_gates(
        risk_flags={"license_invalid": True}, status="active", certs=_certs(), target_category="IT_HW", category_scope=["IT_HW"]
    )[0]["code"] == "G1_LICENSE_INVALID"
    assert evaluate_gates(
        risk_flags={"dishonest_judgment": True}, status="active", certs=_certs(), target_category="IT_HW", category_scope=["IT_HW"]
    )[0]["code"] == "G3_DISHONEST"
    assert evaluate_gates(
        risk_flags={"tax_abnormal": True}, status="active", certs=_certs(), target_category="IT_HW", category_scope=["IT_HW"]
    )[0]["code"] == "G5_TAX_ABNORMAL"
    assert evaluate_gates(
        risk_flags={"major_default": True}, status="active", certs=_certs(), target_category="IT_HW", category_scope=["IT_HW"]
    )[0]["code"] == "G4_DEFAULT"
    assert evaluate_gates(
        risk_flags={}, status="blacklisted", certs=_certs(), target_category="IT_HW", category_scope=["IT_HW"]
    )[0]["code"] == "G3_DISHONEST"


def test_soft_gates_not_veto() -> None:
    scope_hit = evaluate_gates(
        risk_flags={"scope_mismatch": True},
        status="active",
        certs=_certs(),
        target_category="SAAS",
        category_scope=["IT_HW"],
    )
    assert scope_hit[0]["code"] == "G2_SCOPE_MISMATCH"
    assert scope_hit[0]["veto"] is False
    assert gate_verdict(scope_hit)[0] is False

    security_hit = evaluate_gates(
        risk_flags={"security_cert_missing": True},
        status="active",
        certs=_certs(no_license=False),
        target_category="SAAS",
        category_scope=["SAAS"],
    )
    codes = [hit["code"] for hit in security_hit]
    assert "G6_SECURITY_CERT" in codes
    assert all(hit["veto"] is False for hit in security_hit)


def test_clean_supplier_no_gates() -> None:
    assert evaluate_gates(
        risk_flags={},
        status="active",
        certs=_certs(),
        target_category="IT_HW",
        category_scope=["IT_HW"],
    ) == []


def test_expired_license_gate() -> None:
    certs = [
        {"cert_no": "营业执照-1", "cert_type": "营业执照", "valid_to": "2020-01-01"},
        {"cert_no": "ISO27001-1", "cert_type": "ISO27001", "valid_to": "2030-01-01"},
    ]
    hits = evaluate_gates(
        risk_flags={}, status="active", certs=certs, target_category="IT_HW", category_scope=["IT_HW"]
    )
    assert hits and hits[0]["code"] == "G1_LICENSE_INVALID"


def test_clamp() -> None:
    assert clamp(150) == 100.0
    assert clamp(-5) == 0.0
    assert clamp(42) == 42.0
