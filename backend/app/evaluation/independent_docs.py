"""独立文档测试集：与 `app.seed.render` 不同的版式/字段组合，gold 手写。

目的（回应审查"100% 是自造数据自证"）：
- 不复用 seed 的 render 模板，也不从 seed 数据集派生；
- 发票号用真实 20 位号码，金额用全角括号/￥/小写等变体；
- 期望值（gold）由本文件手写常量给出，解析器不能反向推导；
- 仍是合成 PDF，不是真实票据；只能证明"版式/格式变体下的解析回归"，不能替代真实票据人工标注。
"""

from __future__ import annotations

import io
from typing import Any

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

rl_config.invariant = 1
CN_FONT = "STSong-Light"
_FONT_READY = False


def _font() -> None:
    global _FONT_READY
    if not _FONT_READY:
        pdfmetrics.registerFont(UnicodeCIDFont(CN_FONT))
        _FONT_READY = True


def _styles() -> dict[str, ParagraphStyle]:
    _font()
    return {
        "title": ParagraphStyle("title", fontName=CN_FONT, fontSize=15, leading=20, alignment=1),
        "body": ParagraphStyle("body", fontName=CN_FONT, fontSize=9, leading=13),
        "small": ParagraphStyle("small", fontName=CN_FONT, fontSize=8, leading=11, textColor=colors.HexColor("#444444")),
    }


def _table(data: list[list[str]], widths: list[float], *, font_size: float = 8.2) -> Table:
    table = Table(data, colWidths=widths, repeatRows=0)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), CN_FONT),
                ("FONTSIZE", (0, 0), (-1, -1), font_size),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#666666")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _build(story: list[Any]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="independent-eval-doc",
    )
    doc.build(story)
    return buffer.getvalue()


DOC_CASES: list[dict[str, Any]] = [
    {
        "name": "invoice_20_digit_fullwidth",
        "doc_type": "invoice",
        "gold": {
            "invoice_no": "24312000000012345678",
            "invoice_code": "011002400111",
            "invoice_date": "2026-08-15",
            "seller_name": "苏州恒远精密制造有限公司",
            "amount_tax_excl": "12000.00",
            "tax_amount": "1560.00",
            "amount_tax_incl": "13560.00",
            "line": {"item_desc": "工业级交换机 S5720", "qty": "4", "unit_price_tax_excl": "3000.00", "tax_rate": "0.13"},
        },
    },
    {
        "name": "invoice_legacy_alias",
        "doc_type": "invoice",
        "gold": {
            "invoice_no": "INV-2026-9002",
            "invoice_code": "031002300222",
            "invoice_date": "2026-09-01",
            "seller_name": "北京云帆软件技术有限公司",
            "amount_tax_excl": "8600.00",
            "tax_amount": "516.00",
            "amount_tax_incl": "9116.00",
            "line": {"item_desc": "协同办公 SaaS 年费", "qty": "1", "unit_price_tax_excl": "8600.00", "tax_rate": "0.06"},
        },
    },
    {
        "name": "po_independent_layout",
        "doc_type": "po",
        "gold": {
            "po_no": "PO-2026-9001",
            "supplier_name": "苏州恒远精密制造有限公司",
            "po_date": "2026-08-01",
            "total_amount_tax_incl": "13560.00",
            "line": {"item_desc": "工业级交换机 S5720", "qty": "4", "unit_price_tax_excl": "3000.00", "tax_rate": "0.13"},
        },
    },
    {
        "name": "contract_independent_layout",
        "doc_type": "contract",
        "gold": {
            "contract_no": "CT-2026-9001",
            "supplier_name": "苏州恒远精密制造有限公司",
            "total_amount": "13560.00",
            "payment_days": 45,
            "lead_time_days": 10,
            "po_no": "PO-2026-9001",
            "line": {"item_desc": "工业级交换机 S5720", "qty": "4", "unit_price_tax_excl": "3000.00", "tax_rate": "0.13"},
        },
    },
]


def _render_invoice(case: dict[str, Any]) -> bytes:
    st = _styles()
    g = case["gold"]
    line = g["line"]
    excl = float(g["amount_tax_excl"])
    tax = float(g["tax_amount"])
    incl = float(g["amount_tax_incl"])
    qty = float(line["qty"])
    unit = float(line["unit_price_tax_excl"])
    story: list[Any] = [
        Paragraph("增值税电子普通发票", st["title"]),
        Paragraph(f"发票号码：{g['invoice_no']}", st["body"]),
        Spacer(1, 2 * mm),
        _table(
            [
                ["发票代码", g["invoice_code"], "开票日期", g["invoice_date"]],
                ["购买方", "名称：杭州示例招标有限公司    纳税人识别号：91330108MA2B000001"],
                ["销售方", f"名称：{g['seller_name']}    纳税人识别号：91320505MA1X000002"],
            ],
            [22 * mm, 58 * mm, 24 * mm, 58 * mm],
        ),
        Spacer(1, 2 * mm),
        _table(
            [
                ["序号", "货物或应税劳务名称", "规格型号", "数量", "单价(未税)", "金额(未税)", "税率", "税额"],
                [str(1), line["item_desc"], line.get("spec_bucket", "S5720"), f"{qty:g}", f"{unit:.2f}", f"{excl:.2f}", f"{float(line['tax_rate']) * 100:g}%", f"{tax:.2f}"],
            ],
            [10 * mm, 54 * mm, 24 * mm, 14 * mm, 24 * mm, 24 * mm, 12 * mm, 20 * mm],
        ),
        Spacer(1, 2 * mm),
        _table(
            [
                ["金额（未税）", f"￥{excl:,.2f}", "税额", f"￥{tax:,.2f}", "价税合计（小写）", f"￥{incl:,.2f}"],
            ],
            [26 * mm, 34 * mm, 16 * mm, 30 * mm, 34 * mm, 38 * mm],
        ),
        Spacer(1, 2 * mm),
        Paragraph("备注：独立测试集（非 seed 渲染器）    开票人：测试会计", st["small"]),
    ]
    return _build(story)


def _render_po(case: dict[str, Any]) -> bytes:
    st = _styles()
    g = case["gold"]
    line = g["line"]
    qty = float(line["qty"])
    unit = float(line["unit_price_tax_excl"])
    total = float(g["total_amount_tax_incl"])
    story: list[Any] = [
        Paragraph("采购订单", st["title"]),
        Spacer(1, 2 * mm),
        _table(
            [
                ["供应商", g["supplier_name"], "下单日期", g["po_date"]],
                ["币种", "CNY", "采购订单号", g["po_no"]],
            ],
            [22 * mm, 60 * mm, 24 * mm, 54 * mm],
        ),
        Spacer(1, 2 * mm),
        _table(
            [
                ["行号", "物料描述", "规格桶", "数量", "未税单价", "税率", "未税金额", "含税金额", "承诺交期"],
                [
                    "1",
                    line["item_desc"],
                    line.get("spec_bucket", "network/switch"),
                    f"{qty:g}",
                    f"{unit:.2f}",
                    "13%",
                    f"{qty * unit:.2f}",
                    f"{qty * unit * 1.13:.2f}",
                    "2026-08-15",
                ],
            ],
            [10 * mm, 44 * mm, 25 * mm, 12 * mm, 20 * mm, 12 * mm, 22 * mm, 22 * mm, 22 * mm],
        ),
        Spacer(1, 2 * mm),
        Paragraph(f"含税合计：￥{total:,.2f}", st["body"]),
        Paragraph("备注：独立测试集，非 seed 模板。", st["small"]),
    ]
    return _build(story)


def _render_contract(case: dict[str, Any]) -> bytes:
    st = _styles()
    g = case["gold"]
    line = g["line"]
    qty = float(line["qty"])
    unit = float(line["unit_price_tax_excl"])
    total = float(g["total_amount"])
    story: list[Any] = [
        Paragraph("采购合同", st["title"]),
        Paragraph(f"合同编号：{g['contract_no']}", st["body"]),
        Spacer(1, 2 * mm),
        _table(
            [
                ["甲方", "杭州示例招标有限公司", "乙方", g["supplier_name"]],
                ["合同类型", "one_off", "币种", "CNY"],
            ],
            [22 * mm, 62 * mm, 22 * mm, 54 * mm],
        ),
        Spacer(1, 2 * mm),
        Paragraph(f"合同金额 ￥{total:,.2f}", st["body"]),
        Paragraph(f"账期 {g['payment_days']} 天，双方约定：月结{g['payment_days']}天", st["body"]),
        Paragraph(f"交付周期：{g['lead_time_days']} 天", st["body"]),
        Paragraph("逾期违约金：每日按合同金额 0.05% 计取。", st["body"]),
        Spacer(1, 2 * mm),
        _table(
            [
                ["序号", "标的物", "规格桶", "数量", "未税单价", "税率", "未税金额"],
                ["1", line["item_desc"], line.get("spec_bucket", "network/switch"), f"{qty:g}", f"{unit:.2f}", "13%", f"{qty * unit:.2f}"],
            ],
            [12 * mm, 62 * mm, 30 * mm, 14 * mm, 24 * mm, 14 * mm, 34 * mm],
        ),
        Spacer(1, 2 * mm),
        Paragraph(f"关联订单号 {g['po_no']}", st["small"]),
    ]
    return _build(story)


def render_case(case: dict[str, Any]) -> bytes:
    if case["doc_type"] == "invoice":
        return _render_invoice(case)
    if case["doc_type"] == "po":
        return _render_po(case)
    if case["doc_type"] == "contract":
        return _render_contract(case)
    raise ValueError(f"unknown doc_type: {case['doc_type']}")


def compare_extract(doc_type: str, extract: Any, gold: dict[str, Any]) -> tuple[int, int, list[str]]:
    """返回 (matched, total, failures)；字段级比对，便于独立评测报告。"""
    failures: list[str] = []
    matched = 0
    total = 0

    def check(name: str, expected: Any, actual: Any) -> None:
        nonlocal matched, total
        total += 1
        left = str(expected) if expected is not None else None
        right = str(actual) if actual is not None else None
        if left == right:
            matched += 1
        else:
            failures.append(f"{name}: expected={left!r} actual={right!r}")

    if doc_type == "invoice":
        check("invoice_no", gold["invoice_no"], extract.invoice_no)
        check("invoice_code", gold["invoice_code"], extract.invoice_code)
        check("invoice_date", gold["invoice_date"], extract.invoice_date)
        check("seller_name", gold["seller_name"], extract.seller_name)
        check("amount_tax_excl", gold["amount_tax_excl"], extract.amount_tax_excl)
        check("tax_amount", gold["tax_amount"], extract.tax_amount)
        check("amount_tax_incl", gold["amount_tax_incl"], extract.amount_tax_incl)
        line = extract.lines[0] if extract.lines else None
        check("line.item_desc", gold["line"]["item_desc"], line.item_desc if line else None)
        check("line.qty", gold["line"]["qty"], line.qty if line else None)
        check("line.unit_price_tax_excl", gold["line"]["unit_price_tax_excl"], line.unit_price_tax_excl if line else None)
        check("line.tax_rate", gold["line"]["tax_rate"], line.tax_rate if line else None)
    elif doc_type == "po":
        check("po_no", gold["po_no"], extract.po_no)
        check("supplier_name", gold["supplier_name"], extract.supplier_name)
        check("po_date", gold["po_date"], extract.po_date)
        check("total_amount_tax_incl", gold["total_amount_tax_incl"], extract.total_amount_tax_incl)
        line = extract.lines[0] if extract.lines else None
        check("line.item_desc", gold["line"]["item_desc"], line.item_desc if line else None)
        check("line.qty", gold["line"]["qty"], line.qty if line else None)
        check("line.unit_price_tax_excl", gold["line"]["unit_price_tax_excl"], line.unit_price_tax_excl if line else None)
        check("line.tax_rate", gold["line"]["tax_rate"], line.tax_rate if line else None)
    else:
        check("contract_no", gold["contract_no"], extract.contract_no)
        check("supplier_name", gold["supplier_name"], extract.supplier_name)
        check("total_amount", gold["total_amount"], extract.total_amount)
        check("payment_days", gold["payment_days"], extract.payment_terms.days if extract.payment_terms else None)
        check("lead_time_days", gold["lead_time_days"], extract.delivery_terms.lead_time_days if extract.delivery_terms else None)
        check("po_no", gold["po_no"], extract.po_no)
        line = extract.lines[0] if extract.lines else None
        check("line.item_desc", gold["line"]["item_desc"], line.item_desc if line else None)
        check("line.qty", gold["line"]["qty"], line.qty if line else None)
        check("line.unit_price_tax_excl", gold["line"]["unit_price_tax_excl"], line.unit_price_tax_excl if line else None)
        check("line.tax_rate", gold["line"]["tax_rate"], line.tax_rate if line else None)
    return matched, total, failures
