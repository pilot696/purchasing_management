"""M2 基线评测：30 家供应商的闸门检出 / 结论分布 / 置信度 / 敏感性（离线，LLM 不参与）。

产出 `evals/m2_baseline.json`：
    python scripts/m2_eval.py

说明：数据工厂没有 M2 的"专家标注 gold"，因此本基线以**可验证契约**为对照：
- 每条注入的 `risk_flags` / 黑名单是否 100% 触发对应闸门（检出率）；
- 无风险标记的供应商是否不被否决（误杀率）；
- 冷启动供应商是否强制 `conditional` + `low` 置信度；
- 敏感性 flip_rate 分布与维度分覆盖情况。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.agents.supplier import compute_supplier_report  # noqa: E402
from app.core.loop import ensure_windows_selector_loop  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.models import tables as t  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = PROJECT_ROOT / "evals" / "m2_baseline.json"

FLAG_TO_GATE = {
    "license_invalid": "G1_LICENSE_INVALID",
    "scope_mismatch": "G2_SCOPE_MISMATCH",
    "dishonest_judgment": "G3_DISHONEST",
    "major_default": "G4_DEFAULT",
    "tax_abnormal": "G5_TAX_ABNORMAL",
    "security_cert_missing": "G6_SECURITY_CERT",
}
VETO_RESULTS = {"reject", "frozen"}


async def _offline_llm(messages, schema):  # noqa: ANN001 - 评测强制离线
    raise RuntimeError("offline evaluation: LLM disabled")


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


async def run() -> dict:
    session_factory = get_session_factory()
    started = time.perf_counter()
    verdict_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    gate_hits_total = 0
    gate_expected_total = 0
    gate_extra_hits = 0
    gate_false_positive: list[str] = []
    gate_missing: list[str] = []
    false_rejects: list[str] = []
    cold_start_total = 0
    cold_start_ok = 0
    flip_rates: list[float] = []
    totals: list[float] = []
    dimension_completeness: list[int] = []
    sensitivity_most: Counter[str] = Counter()
    llm_status: Counter[str] = Counter()

    async with session_factory() as session:
        suppliers = (await session.scalars(select(t.Supplier).order_by(t.Supplier.code))).all()
        if not suppliers:
            raise SystemExit("供应商为空：先运行 scripts/seed.py --seed 42")
        for supplier in suppliers:
            report = await compute_supplier_report(
                session,
                supplier.code,
                mode="review",
                llm_complete=_offline_llm,
            )
            verdict_counts[report["verdict"]] += 1
            confidence_counts[report["confidence"]] += 1
            totals.append(float(report["total"]))
            dimension_completeness.append(len(report["dimensions"]))
            status = report.get("llm_status") or "unavailable"
            llm_status[status] += 1
            sensitivity = report.get("sensitivity") or {}
            if sensitivity.get("samples"):
                flip_rates.append(float(sensitivity["flip_rate"]))
                sensitivity_most[str(sensitivity["most_sensitive"])] += 1

            risk_flags = dict(supplier.risk_flags or {})
            # 只把"值为真"的标记算作预期闸门（seed 里未命中的标记是 False）
            expected_codes = {
                FLAG_TO_GATE[flag] for flag, value in risk_flags.items() if value and flag in FLAG_TO_GATE
            }
            if supplier.status == "blacklisted":
                expected_codes.add("G3_DISHONEST")
            expected_codes = {code for code in expected_codes if code}
            hit_codes = {hit["code"] for hit in report["gate_hits"]}
            gate_expected_total += len(expected_codes)
            gate_hits_total += len(expected_codes & hit_codes)
            gate_extra_hits += len(hit_codes - expected_codes)
            if expected_codes - hit_codes:
                gate_missing.append(f"{supplier.code}: {sorted(expected_codes - hit_codes)}")

            veto_expected = any(
                FLAG_TO_GATE[flag] not in {"G2_SCOPE_MISMATCH", "G6_SECURITY_CERT"}
                for flag, value in risk_flags.items()
                if value and flag in FLAG_TO_GATE
            )
            if supplier.status == "blacklisted":
                veto_expected = True
            veto_hits = {hit["code"] for hit in report["gate_hits"] if hit.get("veto")}
            if not veto_expected and veto_hits:
                gate_false_positive.append(f"{supplier.code}: {sorted(veto_hits)}")

            if report.get("cold_start") and not veto_hits:
                cold_start_total += 1
                if report["verdict"] == "conditional" and report["confidence"] == "low":
                    cold_start_ok += 1

    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "suppliers": len(suppliers),
        "gate_detection": {
            "expected": gate_expected_total,
            "recall": _ratio(gate_hits_total, gate_expected_total),
            "extra_hits": gate_extra_hits,
            "missing": gate_missing,
            "false_positives": gate_false_positive,
            "false_positive_rate": _ratio(len(gate_false_positive), len(suppliers)),
        },
        "verdict_distribution": dict(sorted(verdict_counts.items())),
        "confidence_distribution": dict(sorted(confidence_counts.items())),
        "cold_start": {
            "total": cold_start_total,
            "consistent_conditional_low": cold_start_ok,
            "consistency_rate": _ratio(cold_start_ok, cold_start_total),
        },
        "dimension_completeness": {
            "mean_dimensions": round(sum(dimension_completeness) / len(dimension_completeness), 4)
            if dimension_completeness
            else 0,
            "expected": 5,
        },
        "total_score": {
            "mean": round(sum(totals) / len(totals), 2) if totals else 0,
            "min": round(min(totals), 2) if totals else 0,
            "max": round(max(totals), 2) if totals else 0,
        },
        "sensitivity": {
            "mean_flip_rate": round(sum(flip_rates) / len(flip_rates), 4) if flip_rates else 0,
            "most_sensitive": dict(sorted(sensitivity_most.items())),
            "samples": 300,
        },
        "llm_status": dict(sorted(llm_status.items())),
        "elapsed_ms": round(elapsed_ms, 2),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": [
            "离线基线：LLM 复核者标记 unavailable（不伪造正文），分数/闸门/敏感性均为确定性计算；",
            "数据工厂无 M2 专家标注 gold，本基线验证的是『规则契约』（注入异常是否 100% 检出、无误杀、冷启动正确降级）；",
            "接入真实模型后可在同一脚本上补 llm_review 风险点 recall 与幻觉引用率（目标 0）。",
        ],
    }


def main() -> None:
    ensure_windows_selector_loop(force=True)
    summary = asyncio.run(run())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"written={OUT_PATH}")
    print("M2_EVAL_OK")


if __name__ == "__main__":
    main()

