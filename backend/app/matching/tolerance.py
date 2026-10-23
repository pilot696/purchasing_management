"""容差引擎与分批核销（Spec §7.5，v1.1 修正：可开票上限 = min(ordered, received)）。

纯函数：输入 PO/发票/合同/收货/容差策略，输出**拟核销**与差异清单；**不写数据库**。
真正的会计事实（`qty_invoiced`、尾差池、发票状态）只能在 HITL 放行后的 `finalize` 事务里提交。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

from app.matching.align import AlignResult

HARD_DIFF_TYPES = frozenset({"price", "amount", "tax_rate", "qty_over", "terms_conflict"})
SUPPRESS_AMOUNT_WHEN = frozenset({"price", "tax_rate", "qty_over", "terms_conflict"})

DIFF_SEVERITY = {
    "price": "medium",
    "amount": "high",
    "tax_rate": "high",
    "qty_over": "high",
    "date_overdue": "low",
    "terms_conflict": "medium",
    "tail": "low",
}
DIFF_RULES = {
    "price": "TOL-PRICE-01",
    "amount": "TOL-AMOUNT-01",
    "tax_rate": "TOL-TAX-01",
    "qty_over": "TOL-QTY-01",
    "date_overdue": "TOL-DATE-01",
    "terms_conflict": "TOL-TERMS-01",
    "tail": "TOL-TAIL-01",
}
SUGGESTIONS = {
    "price": "与供应商确认单价后冲红重开或补签价格变更",
    "amount": "核对合计金额，必要时要求重开发票",
    "tax_rate": "税率与合同/PO 不一致，需重开正确税率的发票",
    "qty_over": "超发：要求供应商冲红重开或补签超发说明",
    "date_overdue": "逾期到货，按合同违约条款处理并记录",
    "terms_conflict": "付款条款冲突，需财务与采购共同确认",
    "tail": "尾差在容差内，自动平账并留痕",
}


@dataclass(frozen=True, slots=True)
class TolerancePolicy:
    diff_type: str
    abs_tol: float = 0.0
    rel_tol: float = 0.0
    action: str = "hold"
    priority: int = 100
    category_code: str | None = None


DEFAULT_POLICIES: tuple[TolerancePolicy, ...] = (
    TolerancePolicy("price", 1.0, 0.005, "hold", 10),
    TolerancePolicy("amount", 1.0, 0.001, "hold", 20),
    TolerancePolicy("tax_rate", 0.0, 0.0, "hold", 5),
    TolerancePolicy("qty_over", 0.0, 0.0, "hold", 5),
    TolerancePolicy("date_overdue", 7.0, 0.0, "release_with_note", 40),
    TolerancePolicy("terms_conflict", 0.0, 0.0, "hold", 20),
    TolerancePolicy("tail", 0.05, 0.0, "auto_clear", 90),
)


@dataclass(slots=True)
class Diff:
    diff_type: str
    severity: str
    po_line_no: int | None
    invoice_line_no: int | None
    expected: float | None
    actual: float | None
    delta: float | None
    rule_ref: str
    suggestion: str
    detail: str | None = None


@dataclass(slots=True)
class LineReconcile:
    po_line_no: int
    invoice_line_no: int | None
    method: str
    score: float
    consumed_qty: float
    remaining_qty: float
    status: str  # matched | partial | over | unmatched
    ambiguous: bool = False


@dataclass(slots=True)
class ReconcileResult:
    line_matches: list[LineReconcile]
    diffs: list[Diff]
    overall: str  # pass | release_with_note | hold
    proposed_tail: float = 0.0
    tolerance_applied: list[str] = None  # type: ignore[assignment]
    contract_absent: bool = False

    def __post_init__(self) -> None:
        if self.tolerance_applied is None:
            self.tolerance_applied = []


def policies_from_rows(rows: Iterable[dict[str, Any]]) -> list[TolerancePolicy]:
    policies: list[TolerancePolicy] = []
    for row in rows:
        policies.append(
            TolerancePolicy(
                diff_type=str(row["diff_type"]),
                abs_tol=float(row.get("abs_tol") or 0),
                rel_tol=float(row.get("rel_tol") or 0),
                action=str(row.get("action") or "hold"),
                priority=int(row.get("priority") or 100),
                category_code=row.get("category_code"),
            )
        )
    return policies


def resolve_policy(diff_type: str, category_code: str | None, policies: list[TolerancePolicy]) -> TolerancePolicy:
    candidates = [
        policy
        for policy in policies
        if policy.diff_type == diff_type and policy.category_code in (None, category_code)
    ]
    if category_code:
        scoped = [policy for policy in candidates if policy.category_code == category_code]
        if scoped:
            candidates = scoped
    if candidates:
        return min(candidates, key=lambda policy: policy.priority)
    for policy in DEFAULT_POLICIES:
        if policy.diff_type == diff_type:
            return policy
    return TolerancePolicy(diff_type)


def within_tolerance(delta: float, expected: float, policy: TolerancePolicy) -> bool:
    return abs(delta) <= max(policy.abs_tol, abs(expected) * policy.rel_tol) + 1e-9


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _received(receipts: list[dict[str, Any]]) -> tuple[dict[int, float], dict[int, date]]:
    qty: dict[int, float] = {}
    latest: dict[int, date] = {}
    for receipt in receipts:
        received_at = _as_date(receipt.get("received_at"))
        for line in receipt.get("lines") or []:
            if str(line.get("qc_result", "pass")) == "fail":
                continue
            po_line_no = int(line.get("po_line_no") or 1)
            qty[po_line_no] = qty.get(po_line_no, 0.0) + _f(line.get("qty"))
            if received_at and (po_line_no not in latest or received_at > latest[po_line_no]):
                latest[po_line_no] = received_at
    return qty, latest


def reconcile(
    *,
    po: dict[str, Any],
    invoice: dict[str, Any],
    align: AlignResult,
    contract: dict[str, Any] | None = None,
    receipts: list[dict[str, Any]] | None = None,
    policies: list[TolerancePolicy] | None = None,
    requires_gr: bool = True,
    category_code: str | None = None,
) -> ReconcileResult:
    policies = policies or list(DEFAULT_POLICIES)
    receipts = receipts or []
    category = category_code or po.get("category_code")

    po_lines = {int(line["line_no"]): line for line in po.get("lines") or []}
    invoice_lines = {int(line["line_no"]): line for line in invoice.get("lines") or []}
    received_qty, received_date = _received(receipts)

    diffs: list[Diff] = []
    applied: list[str] = []
    line_results: list[LineReconcile] = []

    def add_diff(
        diff_type: str,
        *,
        po_line_no: int | None,
        invoice_line_no: int | None,
        expected: float | None = None,
        actual: float | None = None,
        delta: float | None = None,
        detail: str | None = None,
    ) -> None:
        applied.append(DIFF_RULES[diff_type])
        diffs.append(
            Diff(
                diff_type=diff_type,
                severity=DIFF_SEVERITY[diff_type],
                po_line_no=po_line_no,
                invoice_line_no=invoice_line_no,
                expected=expected,
                actual=actual,
                delta=delta,
                rule_ref=DIFF_RULES[diff_type],
                suggestion=SUGGESTIONS[diff_type],
                detail=detail,
            )
        )

    expected_invoiced_total = 0.0
    residual_total = 0.0
    for match in align.matches:
        po_line = po_lines.get(match.po_line_no)
        inv_line = invoice_lines.get(match.invoice_line_no or -1)
        if po_line is None or inv_line is None:
            continue
        ordered = _f(po_line.get("qty_ordered"))
        invoiced = _f(po_line.get("qty_invoiced"))
        received = received_qty.get(match.po_line_no, 0.0)
        billable = ordered if not requires_gr else min(ordered, received)
        open_qty = max(billable - invoiced, 0.0)
        inv_qty = _f(inv_line.get("qty"))
        delta_qty = inv_qty - open_qty
        consumed = min(inv_qty, open_qty)
        remaining = open_qty - consumed

        if delta_qty > 1e-9:
            add_diff(
                "qty_over",
                po_line_no=match.po_line_no,
                invoice_line_no=match.invoice_line_no,
                expected=round(open_qty, 4),
                actual=round(inv_qty, 4),
                delta=round(delta_qty, 4),
                detail="未收货或已开票数量不足，发票数量超出可开票上限",
            )
            status = "over"
        elif remaining > 1e-9:
            status = "partial"
        else:
            status = "matched"

        po_price = _f(po_line.get("unit_price_tax_excl"))
        inv_price = _f(inv_line.get("unit_price_tax_excl"))
        price_policy = resolve_policy("price", category, policies)
        price_delta = inv_price - po_price
        if not within_tolerance(price_delta, po_price, price_policy):
            add_diff(
                "price",
                po_line_no=match.po_line_no,
                invoice_line_no=match.invoice_line_no,
                expected=round(po_price, 4),
                actual=round(inv_price, 4),
                delta=round(price_delta, 4),
            )

        po_rate = _f(po_line.get("tax_rate"))
        inv_rate = _f(inv_line.get("tax_rate"))
        tax_policy = resolve_policy("tax_rate", category, policies)
        if not within_tolerance(inv_rate - po_rate, po_rate, tax_policy):
            add_diff(
                "tax_rate",
                po_line_no=match.po_line_no,
                invoice_line_no=match.invoice_line_no,
                expected=po_rate,
                actual=inv_rate,
                delta=round(inv_rate - po_rate, 4),
            )

        expected_invoiced_total += consumed * po_price * (1 + po_rate)
        # 尾差基准用"发票行项目含税金额本身"（行内已按分取整），避免再次相乘引入 1~2 分的浮点噪声
        line_incl = _f(inv_line.get("amount_tax_incl"))
        residual_total += line_incl if line_incl else consumed * inv_price * (1 + inv_rate)
        line_results.append(
            LineReconcile(
                po_line_no=match.po_line_no,
                invoice_line_no=match.invoice_line_no,
                method=match.method,
                score=match.score,
                consumed_qty=round(consumed, 4),
                remaining_qty=round(remaining, 4),
                status=status,
                ambiguous=match.ambiguous,
            )
        )

    for po_line_no in align.unmatched_po:
        po_line = po_lines.get(po_line_no)
        if po_line is None:
            continue
        ordered = _f(po_line.get("qty_ordered"))
        invoiced = _f(po_line.get("qty_invoiced"))
        received = received_qty.get(po_line_no, 0.0)
        billable = ordered if not requires_gr else min(ordered, received)
        line_results.append(
            LineReconcile(
                po_line_no=po_line_no,
                invoice_line_no=None,
                method="unmatched",
                score=0.0,
                consumed_qty=0.0,
                remaining_qty=round(max(billable - invoiced, 0.0), 4),
                status="unmatched",
            )
        )
    for invoice_line_no in align.unmatched_invoice:
        line_results.append(
            LineReconcile(
                po_line_no=0,
                invoice_line_no=invoice_line_no,
                method="unmatched",
                score=0.0,
                consumed_qty=0.0,
                remaining_qty=0.0,
                status="unmatched",
            )
        )

    invoice_total = _f(invoice.get("amount_tax_incl"))
    # 期望金额按分（2 位小数）取整，避免浮点噪声被误判成"尾差"
    expected_rounded = round(expected_invoiced_total, 2)
    residual_rounded = round(residual_total, 2)
    # 尾差 = 发票合计 - 按发票单价/税率还原的应收：行级差异（price/tax/qty）已被单独解释，
    # 因此即便同时存在价格/税率差异，也能把 ±0.05 的尾差独立识别出来（与 gold 一致）
    proposed_tail = round(invoice_total - residual_rounded, 2) if residual_total else 0.0
    tail_policy = resolve_policy("tail", category, policies)
    if residual_total and 0 < abs(proposed_tail) <= tail_policy.abs_tol:
        add_diff(
            "tail",
            po_line_no=None,
            invoice_line_no=None,
            expected=residual_rounded,
            actual=round(invoice_total, 2),
            delta=proposed_tail,
        )
    if not ({diff.diff_type for diff in diffs} & SUPPRESS_AMOUNT_WHEN):
        amount_policy = resolve_policy("amount", category, policies)
        amount_delta = round(invoice_total - expected_rounded, 2)
        if expected_invoiced_total and not within_tolerance(amount_delta, expected_rounded, amount_policy):
            add_diff(
                "amount",
                po_line_no=None,
                invoice_line_no=None,
                expected=expected_rounded,
                actual=round(invoice_total, 2),
                delta=amount_delta,
            )

    date_policy = resolve_policy("date_overdue", category, policies)
    for line_result in line_results:
        if line_result.invoice_line_no is None:
            continue
        po_line = po_lines.get(line_result.po_line_no) or {}
        promised = _as_date(po_line.get("promised_date"))
        actual = received_date.get(line_result.po_line_no)
        if promised and actual:
            overdue_days = (actual - promised).days
            if overdue_days > date_policy.abs_tol:
                add_diff(
                    "date_overdue",
                    po_line_no=line_result.po_line_no,
                    invoice_line_no=line_result.invoice_line_no,
                    expected=float(overdue_days),
                    actual=float(overdue_days),
                    delta=float(overdue_days),
                    detail=f"承诺交期 {promised.isoformat()}，实际 {actual.isoformat()}",
                )

    if contract is None:
        contract_absent = True
    else:
        contract_absent = False
        terms = contract.get("payment_terms") or {}
        remark = str(invoice.get("remark") or "")
        mode = str(terms.get("mode") or "")
        if "预付" in remark and "预付" not in mode:
            add_diff(
                "terms_conflict",
                po_line_no=None,
                invoice_line_no=None,
                detail=f"合同付款条款「{mode}」与发票备注「{remark}」冲突",
            )

    # 必须用"全部 diff 产生后"的类型集合判定 overall（terms_conflict 在金额检查之后才加入）
    hard_types = {diff.diff_type for diff in diffs}
    if hard_types & HARD_DIFF_TYPES:
        overall = "hold"
    elif diffs:
        overall = "release_with_note"
    else:
        overall = "pass"

    return ReconcileResult(
        line_matches=line_results,
        diffs=diffs,
        overall=overall,
        proposed_tail=proposed_tail,
        tolerance_applied=sorted(set(applied)),
        contract_absent=contract_absent,
    )
