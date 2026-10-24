"""确定性校验器（Spec §7.2）：算术、税率白名单、日期合理性、数量合法性。

校验失败不抛异常，返回 `ValidationIssue` 列表——调用方决定 repair / needs_review / 挂起。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.schemas.extraction import ContractExtract, InvoiceExtract, PoExtract, ValidationIssue

TAX_RATE_WHITELIST = {Decimal("0"), Decimal("0.01"), Decimal("0.03"), Decimal("0.06"), Decimal("0.09"), Decimal("0.13")}
MONEY_TOLERANCE = Decimal("0.01")
TAIL_TOLERANCE = Decimal("0.05")


def _sum(values: list[Decimal | None]) -> Decimal:
    return sum((v for v in values if v is not None), Decimal("0"))


def validate_invoice(invoice: InvoiceExtract) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not invoice.lines:
        issues.append(ValidationIssue(code="LINES_EMPTY", message="发票行项目为空", field="lines", severity="high"))
    if invoice.seller_name is None:
        issues.append(ValidationIssue(code="SELLER_MISSING", message="销售方名称为空", field="seller_name", severity="high"))

    line_sum = _sum([line.amount_tax_excl for line in invoice.lines])
    if invoice.amount_tax_excl is not None and invoice.lines and abs(line_sum - invoice.amount_tax_excl) > MONEY_TOLERANCE:
        issues.append(
            ValidationIssue(
                code="ARITH_LINE_SUM",
                message=f"行项目金额合计 {line_sum} ≠ 表头未税金额 {invoice.amount_tax_excl}",
                field="amount_tax_excl",
                severity="high",
            )
        )
    if invoice.amount_tax_excl is not None and invoice.tax_amount is not None and invoice.amount_tax_incl is not None:
        delta = abs(invoice.amount_tax_excl + invoice.tax_amount - invoice.amount_tax_incl)
        if delta > TAIL_TOLERANCE:
            issues.append(
                ValidationIssue(
                    code="ARITH_TOTAL",
                    message=f"未税+税额 与 价税合计 偏差 {delta}",
                    field="amount_tax_incl",
                    severity="high",
                )
            )
        elif delta > MONEY_TOLERANCE:
            issues.append(
                ValidationIssue(
                    code="TAIL_DIFF",
                    message=f"发票内部尾差 {delta}（≤¥0.05，按尾差规则处理）",
                    field="amount_tax_incl",
                    severity="low",
                )
            )
    is_credit_note = bool(invoice.lines) and all(line.qty <= 0 for line in invoice.lines)
    if is_credit_note:
        issues.append(
            ValidationIssue(
                code="CREDIT_NOTE_DETECTED",
                message="检测到红冲/负数发票：允许负数数量，但必须人工复核",
                field="lines",
                severity="low",
            )
        )
    for line in invoice.lines:
        if line.tax_rate is not None and line.tax_rate not in TAX_RATE_WHITELIST:
            issues.append(
                ValidationIssue(
                    code="TAX_RATE_INVALID",
                    message=f"非法税率 {line.tax_rate}",
                    field=f"lines[{line.line_no}].tax_rate",
                    severity="high",
                )
            )
        if line.qty == 0 or (line.qty < 0 and not is_credit_note):
            issues.append(
                ValidationIssue(
                    code="QTY_INVALID",
                    message=f"数量必须为正（红冲发票除外）：{line.qty}",
                    field=f"lines[{line.line_no}].qty",
                    severity="high",
                )
            )
    if invoice.invoice_date and invoice.invoice_date > date.today():
        issues.append(
            ValidationIssue(code="DATE_FUTURE", message="开票日期晚于今天", field="invoice_date", severity="high")
        )
    return issues


def validate_po(po: PoExtract) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not po.lines:
        issues.append(ValidationIssue(code="LINES_EMPTY", message="PO 行项目为空", field="lines", severity="high"))
        return issues
    line_total = _sum([line.amount_tax_incl for line in po.lines])
    if po.total_amount_tax_incl is not None and abs(line_total - po.total_amount_tax_incl) > MONEY_TOLERANCE:
        issues.append(
            ValidationIssue(
                code="ARITH_PO_TOTAL",
                message=f"行项目含税合计 {line_total} ≠ PO 合计 {po.total_amount_tax_incl}",
                field="total_amount_tax_incl",
                severity="high",
            )
        )
    if po.supplier_name is None:
        issues.append(ValidationIssue(code="SUPPLIER_MISSING", message="PO 供应商为空", field="supplier_name", severity="high"))
    return issues


def validate_contract(contract: ContractExtract) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if contract.total_amount is not None and contract.total_amount <= 0:
        issues.append(
            ValidationIssue(code="AMOUNT_NONPOS", message="合同金额必须为正", field="total_amount", severity="high")
        )
    if contract.payment_terms is not None and contract.payment_terms.days is not None:
        if contract.payment_terms.days < 0 or contract.payment_terms.days > 180:
            issues.append(
                ValidationIssue(
                    code="PAYMENT_TERMS_SUSPICIOUS",
                    message=f"账期异常：{contract.payment_terms.days} 天",
                    field="payment_terms.days",
                    severity="medium",
                )
            )
    if contract.supplier_name is None:
        issues.append(
            ValidationIssue(code="SUPPLIER_MISSING", message="合同乙方为空", field="supplier_name", severity="high")
        )
    return issues


VALIDATORS = {
    "invoice": validate_invoice,
    "po": validate_po,
    "contract": validate_contract,
}
