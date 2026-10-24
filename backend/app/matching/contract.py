"""合同行 ↔ 订单/发票的补充校验（M3 从两单升级为三单）。

设计取舍：
- 不把合同校验塞进 reconcile() 的行对齐主流程（那里处理 PO↔Invoice 容差）；
- 在 M3 reconcile 聚合后追加 contract_* 差异，命中即升级 overall；
- 合同行按 spec_bucket 优先、品名精确匹配兜底；
- 阈值全部走 tolerance_policy（默认策略在 tolerance.DEFAULT_POLICIES，可按品类覆盖），
  不再在代码里硬编码 1%/5%/交期天数。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.matching.normalize import normalize
from app.matching.tolerance import TolerancePolicy, resolve_policy, within_tolerance

# 策略动作 → 差异严重度；hold 会升级 overall，release_with_note 只提示
ACTION_SEVERITY = {
    "hold": "high",
    "release_with_note": "medium",
    "note": "low",
    "auto_clear": "info",
}
RULE_REFS = {
    "contract_price": "TOL-CONTRACT-01",
    "contract_qty": "TOL-CONTRACT-02",
    "contract_amount": "TOL-CONTRACT-03",
    "contract_delivery": "TOL-CONTRACT-04",
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _policy(policies: list[TolerancePolicy], diff_type: str, category_code: str | None) -> TolerancePolicy:
    return resolve_policy(diff_type, category_code, policies)


def _severity(policy: TolerancePolicy) -> str:
    return ACTION_SEVERITY.get(policy.action, "medium")


def _contract_index(contract: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_bucket: dict[str, dict[str, Any]] = {}
    lines: list[dict[str, Any]] = []
    for line in contract.get("lines") or []:
        lines.append(line)
        bucket = line.get("spec_bucket")
        if bucket:
            by_bucket.setdefault(str(bucket), line)
    return by_bucket, lines


def _find_contract_line(
    *,
    item_desc: str | None,
    spec_bucket: str | None,
    by_bucket: dict[str, dict[str, Any]],
    lines: list[dict[str, Any]],
    category_code: str | None,
) -> dict[str, Any] | None:
    if spec_bucket and spec_bucket in by_bucket:
        return by_bucket[spec_bucket]
    if not item_desc:
        return None
    normalized = normalize(item_desc, category_code=category_code)
    target = normalized.spec_bucket
    if target in by_bucket:
        return by_bucket[target]
    for line in lines:
        if normalize(str(line.get("item_desc") or ""), category_code=category_code).spec_bucket == target:
            return line
    return None


def check_contract(
    *,
    contract: dict[str, Any] | None,
    po: dict[str, Any],
    invoice: dict[str, Any] | None = None,
    category_code: str | None = None,
    policies: list[TolerancePolicy] | None = None,
) -> list[dict[str, Any]]:
    """返回合同层差异；无合同/无合同行时返回空列表（由调用方保留 contract_absent 提示）。

    阈值来源优先级：品类专属策略 > 全局策略 > tolerance.DEFAULT_POLICIES。
    """
    if not contract or not contract.get("lines"):
        return []
    active_policies = list(policies or [])
    diffs: list[dict[str, Any]] = []
    by_bucket, lines = _contract_index(contract)

    for po_line in po.get("lines") or []:
        match = _find_contract_line(
            item_desc=po_line.get("item_desc"),
            spec_bucket=po_line.get("spec_bucket"),
            by_bucket=by_bucket,
            lines=lines,
            category_code=category_code,
        )
        if match is None:
            continue
        expected = _f(match.get("unit_price_tax_excl"))
        actual = _f(po_line.get("unit_price_tax_excl"))
        delta = round(actual - expected, 4)
        price_policy = _policy(active_policies, "contract_price", category_code)
        if not within_tolerance(delta, expected, price_policy):
            diffs.append(
                {
                    "po_line_no": po_line.get("line_no"),
                    "invoice_line_no": None,
                    "diff_type": "contract_price",
                    "severity": _severity(price_policy),
                    "expected": round(expected, 4),
                    "actual": round(actual, 4),
                    "delta": delta,
                    "rule_ref": RULE_REFS["contract_price"],
                    "suggestion": "PO 单价与合同价不一致：补签变更/价格确认后再放行",
                    "detail": f"合同价 ¥{expected:.4f}，PO 价 ¥{actual:.4f}",
                }
            )

        contract_qty = _f(match.get("qty"))
        po_qty = _f(po_line.get("qty_ordered"))
        delta_qty = round(po_qty - contract_qty, 4)
        qty_policy = _policy(active_policies, "contract_qty", category_code)
        if contract_qty > 0 and delta_qty > 0 and not within_tolerance(delta_qty, contract_qty, qty_policy):
            diffs.append(
                {
                    "po_line_no": po_line.get("line_no"),
                    "invoice_line_no": None,
                    "diff_type": "contract_qty",
                    "severity": _severity(qty_policy),
                    "expected": round(contract_qty, 4),
                    "actual": round(po_qty, 4),
                    "delta": delta_qty,
                    "rule_ref": RULE_REFS["contract_qty"],
                    "suggestion": "PO 数量超过合同数量：需补签采购变更",
                    "detail": f"合同数量 {contract_qty}，PO 数量 {po_qty}",
                }
            )

    # 订单总金额 vs 合同总金额
    contract_total = _f(contract.get("total_amount"))
    po_total = _f(po.get("total_amount_tax_incl"))
    if contract_total > 0 and po_total > 0:
        delta = round(po_total - contract_total, 4)
        amount_policy = _policy(active_policies, "contract_amount", category_code)
        if not within_tolerance(delta, contract_total, amount_policy):
            diffs.append(
                {
                    "po_line_no": None,
                    "invoice_line_no": None,
                    "diff_type": "contract_amount",
                    "severity": _severity(amount_policy),
                    "expected": round(contract_total, 4),
                    "actual": round(po_total, 4),
                    "delta": delta,
                    "rule_ref": RULE_REFS["contract_amount"],
                    "suggestion": "PO 总额与合同总额偏差较大，请确认是否含税口径或合同变更",
                    "detail": f"合同总额 {contract_total}，PO 总额（含税）{po_total}",
                }
            )

    # 合同交期：订单承诺日期不得晚于下单日 + 合同 lead_time_days
    lead_time_raw = (contract.get("delivery_terms") or {}).get("lead_time_days")
    try:
        lead_time = int(lead_time_raw) if lead_time_raw is not None else None
    except (TypeError, ValueError):
        lead_time = None
    ordered_at = _as_date(po.get("ordered_at"))
    delivery_policy = _policy(active_policies, "contract_delivery", category_code)
    if lead_time is not None and ordered_at is not None:
        expected_delivery = ordered_at.toordinal() + lead_time
        for po_line in po.get("lines") or []:
            promised = _as_date(po_line.get("promised_date"))
            if promised is None:
                continue
            overdue = promised.toordinal() - expected_delivery
            if overdue > 0 and not within_tolerance(overdue, lead_time, delivery_policy):
                diffs.append(
                    {
                        "po_line_no": po_line.get("line_no"),
                        "invoice_line_no": None,
                        "diff_type": "contract_delivery",
                        "severity": _severity(delivery_policy),
                        "expected": lead_time,
                        "actual": lead_time + overdue,
                        "delta": overdue,
                        "rule_ref": RULE_REFS["contract_delivery"],
                        "suggestion": "承诺交期晚于合同交期约定，需供应商确认或走变更",
                        "detail": f"合同 lead_time={lead_time} 天，订单承诺比该期限晚 {overdue} 天",
                    }
                )

    if invoice is not None:
        invoice_price_policy = _policy(active_policies, "contract_price", category_code)
        for invoice_line in invoice.get("lines") or []:
            match = _find_contract_line(
                item_desc=invoice_line.get("item_desc"),
                spec_bucket=invoice_line.get("spec_bucket"),
                by_bucket=by_bucket,
                lines=lines,
                category_code=category_code,
            )
            if match is None:
                continue
            expected = _f(match.get("unit_price_tax_excl"))
            actual = _f(invoice_line.get("unit_price_tax_excl"))
            delta = round(actual - expected, 4)
            if not within_tolerance(delta, expected, invoice_price_policy):
                diffs.append(
                    {
                        "po_line_no": None,
                        "invoice_line_no": invoice_line.get("line_no"),
                        "diff_type": "contract_price",
                        "severity": _severity(invoice_price_policy),
                        "expected": round(expected, 4),
                        "actual": round(actual, 4),
                        "delta": delta,
                        "rule_ref": RULE_REFS["contract_price"],
                        "suggestion": "发票单价与合同价不一致：要求冲红重开或补充合同变更",
                        "detail": f"合同价 ¥{expected:.4f}，发票价 ¥{actual:.4f}",
                    }
                )
    return diffs
