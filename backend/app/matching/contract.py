"""合同行 ↔ 订单/发票的补充校验（M3 从两单升级为三单）。

设计取舍：
- 不把合同校验塞进 reconcile() 的行对齐主流程（那里处理 PO↔Invoice 容差）；
- 在 M3 reconcile 聚合后追加 contract_* 差异，命中即升级 overall；
- 合同行按 spec_bucket 优先、品名精确匹配兜底。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.matching.normalize import normalize


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


def _within(delta: float, expected: float, rel: float = 0.01, abs_tol: float = 0.01) -> bool:
    return abs(delta) <= max(abs_tol, abs(expected) * rel)


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
) -> list[dict[str, Any]]:
    """返回合同层差异；无合同/无合同行时返回空列表（由调用方保留 contract_absent 提示）。"""
    if not contract or not contract.get("lines"):
        return []
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
        if not _within(delta, expected):
            diffs.append(
                {
                    "po_line_no": po_line.get("line_no"),
                    "invoice_line_no": None,
                    "diff_type": "contract_price",
                    "severity": "high",
                    "expected": round(expected, 4),
                    "actual": round(actual, 4),
                    "delta": delta,
                    "rule_ref": "TOL-CONTRACT-01",
                    "suggestion": "PO 单价与合同价不一致：补签变更/价格确认后再放行",
                    "detail": f"合同价 ¥{expected:.4f}，PO 价 ¥{actual:.4f}",
                }
            )

        contract_qty = _f(match.get("qty"))
        po_qty = _f(po_line.get("qty_ordered"))
        if contract_qty > 0 and po_qty > contract_qty + 1e-9:
            diffs.append(
                {
                    "po_line_no": po_line.get("line_no"),
                    "invoice_line_no": None,
                    "diff_type": "contract_qty",
                    "severity": "high",
                    "expected": round(contract_qty, 4),
                    "actual": round(po_qty, 4),
                    "delta": round(po_qty - contract_qty, 4),
                    "rule_ref": "TOL-CONTRACT-02",
                    "suggestion": "PO 数量超过合同数量：需补签采购变更",
                    "detail": f"合同数量 {contract_qty}，PO 数量 {po_qty}",
                }
            )

    # 订单总金额 vs 合同总金额
    contract_total = _f(contract.get("total_amount"))
    po_total = _f(po.get("total_amount_tax_incl"))
    if contract_total > 0 and po_total > 0:
        # 合同金额含税/未税口径可能不同，这里只做存在合同行时的软校验（相对差 >5% 才报）
        delta = round(po_total - contract_total, 4)
        if abs(delta) > max(0.01, abs(contract_total) * 0.05):
            diffs.append(
                {
                    "po_line_no": None,
                    "invoice_line_no": None,
                    "diff_type": "contract_amount",
                    "severity": "medium",
                    "expected": round(contract_total, 4),
                    "actual": round(po_total, 4),
                    "delta": delta,
                    "rule_ref": "TOL-CONTRACT-03",
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
    if lead_time is not None and ordered_at is not None:
        expected_delivery = ordered_at.toordinal() + lead_time
        for po_line in po.get("lines") or []:
            promised = _as_date(po_line.get("promised_date"))
            if promised is None:
                continue
            overdue = promised.toordinal() - expected_delivery
            if overdue > 0:
                diffs.append(
                    {
                        "po_line_no": po_line.get("line_no"),
                        "invoice_line_no": None,
                        "diff_type": "contract_delivery",
                        "severity": "medium",
                        "expected": lead_time,
                        "actual": lead_time + overdue,
                        "delta": overdue,
                        "rule_ref": "TOL-CONTRACT-04",
                        "suggestion": "承诺交期晚于合同账期约定，需供应商确认或走变更",
                        "detail": f"合同 lead_time={lead_time} 天，订单承诺比该期限晚 {overdue} 天",
                    }
                )

    if invoice is not None:
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
            if not _within(delta, expected):
                diffs.append(
                    {
                        "po_line_no": None,
                        "invoice_line_no": invoice_line.get("line_no"),
                        "diff_type": "contract_price",
                        "severity": "high",
                        "expected": round(expected, 4),
                        "actual": round(actual, 4),
                        "delta": delta,
                        "rule_ref": "TOL-CONTRACT-01",
                        "suggestion": "发票单价与合同价不一致：要求冲红重开或补充合同变更",
                        "detail": f"合同价 ¥{expected:.4f}，发票价 ¥{actual:.4f}",
                    }
                )
    return diffs
