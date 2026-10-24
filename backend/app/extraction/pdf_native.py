"""数字原生 PDF 的确定性字段抽取（Spec §7.2：能用确定性方法拿到的字段绝不让 LLM 猜）。

输入是 pdfplumber 可解析的文本层；输出 Pydantic 模型 + 归一化 bbox 注解（供前端 PDF 抽屉高亮）。
"""

from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pdfplumber

from app.schemas.extraction import (
    Annotation,
    ContractExtract,
    DeliveryTerms,
    DocLine,
    InvoiceExtract,
    PaymentTerms,
    PoExtract,
)

MONEY = r"([\d,]+\.\d{2})"
PO_NO_RE = re.compile(r"PO-\d{4}-\d{4}")
CONTRACT_NO_RE = re.compile(r"CT-\d{4}-\d{4}")
INVOICE_NO_RE = re.compile(r"INV-\d{4}-\d{4}")
DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
NAME_RE = re.compile(r"名称[:：]\s*([^\s]+)")
TAX_ID_RE = re.compile(r"纳税人识别号[:：]\s*([0-9A-Z]{15,20})")

# 真实增值税发票兼容：20 位发票号码、全角括号、￥/¥、“小写”等变体
INVOICE_NO_LABEL_RE = re.compile(r"发票号码\s*[:：]?\s*([0-9A-Za-z-]{8,32})")
INVOICE_NO_LEGACY_RE = re.compile(r"(INV[-_]?\d{4}[-_]?\d{4})")
PO_NO_LABEL_RE = re.compile(r"(?:采购订单号|订单号)\s*[:：]?\s*([A-Za-z0-9-]{4,32})")
AMOUNT_EXCL_PATTERNS = (
    re.compile(rf"金额\s*[（(]\s*(?:未税|不含税)\s*[）)]\s*[¥￥]?\s*{MONEY}"),
    re.compile(rf"不含税金额\s*[¥￥]?\s*{MONEY}"),
    re.compile(rf"金额\s*[¥￥]\s*{MONEY}"),
)
TAX_AMOUNT_RE = re.compile(rf"税额\s*[¥￥]?\s*{MONEY}")
AMOUNT_INCL_PATTERNS = (
    re.compile(rf"价税合计[^\d]{{0,30}}?[（(]?\s*小写\s*[）)]?\s*[¥￥]?\s*{MONEY}"),
    re.compile(rf"价税合计\s*[¥￥]\s*{MONEY}"),
)

LINE_HEADER_KEYS = {"行号", "序号"}
SKIP_HEADER_VALUES = {"物料描述", "货物或应税劳务名称", "标的物", "数量", "规格桶", "规格型号"}


class ParseError(Exception):
    pass


def _dec(value: str | None) -> Decimal | None:
    if not value:
        return None
    cleaned = (
        value.strip()
        .replace("¥", "")
        .replace("￥", "")
        .replace(",", "")
        .replace("，", "")
        .replace("%", "")
        .strip()
    )
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _rate(value: str | None) -> Decimal | None:
    if not value:
        return None
    text = value.strip()
    number = _dec(text)
    if number is None:
        return None
    return number / 100 if "%" in text else number


def _date(value: str | None) -> date | None:
    if not value:
        return None
    match = DATE_RE.search(value)
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def _search(pattern: re.Pattern[str], text: str) -> str | None:
    """取第一个匹配组（无捕获组时取整体）。"""
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1) if match.groups() else match.group(0)


def _header_map(tables: list[list[list[str]]]) -> dict[str, str]:
    """把 "标签 值 标签 值" 形式的表头行汇总成 dict（跳过行项目表头与数据行）。"""
    header: dict[str, str] = {}
    for table in tables:
        for row in table:
            cells = [str(c or "").strip() for c in row]
            if len(cells) < 2 or not cells[0]:
                continue
            if cells[0] in LINE_HEADER_KEYS:
                continue
            for i in range(0, len(cells) - 1, 2):
                key, value = cells[i], cells[i + 1]
                if key and value and value not in SKIP_HEADER_VALUES:
                    header.setdefault(key, value)
    return header


def _bbox(page: Any, needle: str | None) -> list[float] | None:
    """字段值 → 归一化 bbox（左上原点）。先按文本搜索，失败再按词表精确匹配。"""
    if not needle:
        return None
    width, height = float(page.width) or 1.0, float(page.height) or 1.0
    candidates = [needle, needle.lstrip("¥")]
    for candidate in candidates:
        if not candidate:
            continue
        rects = page.search(candidate)
        if rects:
            rect = rects[0]
            return [
                round(max(0.0, min(1.0, rect["x0"] / width)), 5),
                round(max(0.0, min(1.0, rect["top"] / height)), 5),
                round(max(0.0, min(1.0, rect["x1"] / width)), 5),
                round(max(0.0, min(1.0, rect["bottom"] / height)), 5),
            ]
    target = needle.strip().lstrip("¥")
    for word in page.extract_words():
        text = str(word["text"]).strip()
        if text == needle.strip() or text.lstrip("¥") == target:
            return [
                round(max(0.0, min(1.0, word["x0"] / width)), 5),
                round(max(0.0, min(1.0, word["top"] / height)), 5),
                round(max(0.0, min(1.0, word["x1"] / width)), 5),
                round(max(0.0, min(1.0, word["bottom"] / height)), 5),
            ]
    return None


def _annotation(page: Any, field_name: str, needle: str | None) -> Annotation | None:
    box = _bbox(page, needle)
    if not box or box[0] >= box[2] or box[1] >= box[3]:
        return None
    return Annotation(field_name=field_name, x0=box[0], y0=box[1], x1=box[2], y1=box[3], text_raw=needle)


def _lines_from_table(table: list[list[str]]) -> list[DocLine]:
    if not table:
        return []
    header = [str(cell or "").strip() for cell in table[0]]
    index = {name: i for i, name in enumerate(header)}
    lines: list[DocLine] = []

    def cell(cells: list[str], *names: str) -> str | None:
        for name in names:
            if name in index and index[name] < len(cells):
                return cells[index[name]]
        return None

    for row in table[1:]:
        cells = [str(cell_value or "").strip() for cell_value in row]
        if not any(cells):
            continue
        qty = _dec(cell(cells, "数量"))
        if qty is None:
            continue
        item_desc = cell(cells, "物料描述", "货物或应税劳务名称", "标的物") or ""
        if not item_desc:
            continue
        rate = _rate(cell(cells, "税率"))
        amount_excl = _dec(cell(cells, "未税金额", "金额(未税)", "金额"))
        unit_price = _dec(cell(cells, "未税单价", "单价(未税)", "单价"))
        amount_incl = _dec(cell(cells, "含税金额"))
        tax_amount = _dec(cell(cells, "税额"))
        if amount_incl is None and amount_excl is not None and rate is not None:
            amount_incl = amount_excl + amount_excl * rate
        if amount_excl is None and amount_incl is not None and tax_amount is not None:
            amount_excl = amount_incl - tax_amount
        lines.append(
            DocLine(
                line_no=int(cell(cells, "行号") or len(lines) + 1),
                item_desc=item_desc,
                spec_bucket=cell(cells, "规格桶", "规格型号"),
                qty=qty,
                unit_price_tax_excl=unit_price,
                tax_rate=rate,
                amount_tax_excl=amount_excl,
                amount_tax_incl=amount_incl,
            )
        )
    return lines


def _first_table_with(tables: list[list[list[str]]], *anchors: str) -> list[list[str]]:
    for table in tables:
        for row in table:
            if any(anchor in [str(c or "").strip() for c in row] for anchor in anchors):
                return table
    return []


def _extract_invoice_no(text: str) -> str | None:
    return _search(INVOICE_NO_LABEL_RE, text) or _search(INVOICE_NO_LEGACY_RE, text)


def _extract_invoice_amounts(text: str) -> dict[str, str | None]:
    excl = next((_search(pattern, text) for pattern in AMOUNT_EXCL_PATTERNS if _search(pattern, text)), None)
    incl = next((_search(pattern, text) for pattern in AMOUNT_INCL_PATTERNS if _search(pattern, text)), None)
    return {
        "amount_tax_excl": excl,
        "tax_amount": _search(TAX_AMOUNT_RE, text),
        "amount_tax_incl": incl,
    }


def parse_invoice(pdf_bytes: bytes) -> tuple[InvoiceExtract, list[Annotation]]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        tables = page.extract_tables() or []
    invoice_no = _extract_invoice_no(text)
    if not invoice_no:
        raise ParseError("发票号码未找到")
    header = _header_map(tables)
    line_table = _first_table_with(tables, "货物或应税劳务名称")
    lines = _lines_from_table(line_table)
    if not lines:
        raise ParseError("发票行项目表未解析到")

    buyer_block = header.get("购买方", "")
    seller_block = header.get("销售方", "")
    seller_name = _search(NAME_RE, seller_block)
    buyer_name = _search(NAME_RE, buyer_block)
    seller_tax = _search(TAX_ID_RE, seller_block)
    invoice_code = header.get("发票代码") or _search(re.compile(r"发票代码[:：]?\s*(\d{12,20})"), text)
    invoice_date = _date(header.get("开票日期")) or _date(text)
    amount_text = _extract_invoice_amounts(text)
    amounts = {
        "amount_tax_excl": _dec(amount_text["amount_tax_excl"]),
        "tax_amount": _dec(amount_text["tax_amount"]),
        "amount_tax_incl": _dec(amount_text["amount_tax_incl"]),
    }
    po_no = _search(re.compile(r"采购订单号[:：]\s*(PO-\d{4}-\d{4})"), text)
    remark = _search(re.compile(r"备注[:：]\s*(.*?)\s+采购订单号"), text)
    total_text = f"¥{amounts['amount_tax_incl']:.2f}" if amounts["amount_tax_incl"] is not None else None
    annotations = [
        a
        for a in (
            _annotation(page, "invoice_no", invoice_no),
            _annotation(page, "seller_name", seller_name),
            _annotation(page, "amount_tax_incl", total_text),
        )
        if a is not None
    ]
    rates = sorted({line.tax_rate for line in lines if line.tax_rate is not None})
    return (
        InvoiceExtract(
            invoice_no=invoice_no,
            invoice_code=invoice_code,
            invoice_date=invoice_date,
            seller_name=seller_name,
            seller_tax_id=seller_tax,
            buyer_name=buyer_name,
            amount_tax_excl=amounts["amount_tax_excl"],
            tax_amount=amounts["tax_amount"],
            amount_tax_incl=amounts["amount_tax_incl"],
            tax_rates=rates,
            lines=lines,
            remark=None if remark in (None, "--") else remark,
            po_no=po_no,
        ),
        annotations,
    )


def parse_po(pdf_bytes: bytes) -> tuple[PoExtract, list[Annotation]]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        tables = page.extract_tables() or []
    po_no = _search(PO_NO_RE, text)
    if not po_no:
        raise ParseError("PO 号未找到")
    header = _header_map(tables)
    lines = _lines_from_table(_first_table_with(tables, "物料描述"))
    if not lines:
        raise ParseError("PO 行项目表未解析到")
    total = _dec(_search(re.compile(rf"含税合计[:：]\s*[¥￥]?\s*{MONEY}"), text))
    supplier = header.get("供应商")
    annotations = [
        a
        for a in (_annotation(page, "po_no", po_no), _annotation(page, "supplier_name", supplier))
        if a is not None
    ]
    return (
        PoExtract(
            po_no=po_no,
            supplier_name=supplier,
            po_date=_date(header.get("下单日期")),
            currency=header.get("币种", "CNY") or "CNY",
            total_amount_tax_incl=total,
            lines=lines,
        ),
        annotations,
    )


def parse_contract(pdf_bytes: bytes) -> tuple[ContractExtract, list[Annotation]]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        tables = page.extract_tables() or []
    contract_no = _search(CONTRACT_NO_RE, text)
    if not contract_no:
        raise ParseError("合同编号未找到")
    header = _header_map(tables)
    supplier = header.get("乙方")
    total = _dec(_search(re.compile(rf"合同金额\s*[¥￥]?\s*{MONEY}"), text))
    payment_days = _search(re.compile(r"账期\s*(\d+)\s*天"), text)
    payment_mode = _search(re.compile(r"双方约定[:：]\s*([^，。]+)"), text)
    lead_days = _search(re.compile(r"交付周期[:：]\s*(\d+)\s*天"), text)
    penalty = _search(re.compile(r"逾期违约金[:：]\s*([^。]+)"), text)
    annotations = [
        a
        for a in (_annotation(page, "contract_no", contract_no), _annotation(page, "supplier_name", supplier))
        if a is not None
    ]
    return (
        ContractExtract(
            contract_no=contract_no,
            contract_type=header.get("合同类型", "one_off") or "one_off",  # type: ignore[arg-type]
            supplier_name=supplier,
            buyer_name=header.get("甲方"),
            total_amount=total,
            currency="CNY",
            payment_terms=PaymentTerms(days=int(payment_days) if payment_days else None, mode=payment_mode),
            delivery_terms=DeliveryTerms(
                lead_time_days=int(lead_days) if lead_days else None, penalty_clause=penalty
            ),
            lines=_lines_from_table(_first_table_with(tables, "标的物")),
            po_no=_search(re.compile(r"订单号\s*(PO-\d{4}-\d{4})"), text),
        ),
        annotations,
    )


PARSERS = {"invoice": parse_invoice, "po": parse_po, "contract": parse_contract}
