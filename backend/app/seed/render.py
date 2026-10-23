"""ReportLab PDF 模板：增值税发票 / 采购订单 / 采购合同（Spec §10.4）。

- 中文用 ReportLab 内置 CID 字体 `STSong-Light`，零外部字体文件；
- `rl_config.invariant = 1` 让 PDF 字节可复现（同样的数据 → 同样的 sha256），便于测试与幂等；
- 生成的是**数字原生 PDF**（带文本层），扫描件由 `degrade.py` 退化生成。
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
_font_ready = False


def _ensure_font() -> None:
    global _font_ready
    if not _font_ready:
        pdfmetrics.registerFont(UnicodeCIDFont(CN_FONT))
        _font_ready = True


def _styles() -> dict[str, ParagraphStyle]:
    _ensure_font()
    return {
        "title": ParagraphStyle("t", fontName=CN_FONT, fontSize=16, leading=22, alignment=1),
        "h2": ParagraphStyle("h2", fontName=CN_FONT, fontSize=11, leading=16, spaceBefore=6),
        "body": ParagraphStyle("b", fontName=CN_FONT, fontSize=9, leading=14),
        "small": ParagraphStyle("s", fontName=CN_FONT, fontSize=8, leading=12, textColor=colors.HexColor("#555555")),
    }


def _table(data: list[list[str]], col_widths: list[float] | None = None, font_size: float = 8.5) -> Table:
    _ensure_font()
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), CN_FONT),
                ("FONTSIZE", (0, 0), (-1, -1), font_size),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F3F7")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _build(story: list[Any]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="synthetic-procurement-doc",
        author="seed-factory",
    )
    doc.build(story)
    return buffer.getvalue()


def render_invoice(invoice: dict[str, Any], po: dict[str, Any], supplier_name: str) -> bytes:
    """仿增值税专用发票版式（数字原生，带文本层）。"""
    st = _styles()
    story: list[Any] = [
        Paragraph("增值税专用发票", st["title"]),
        Spacer(1, 3 * mm),
        _table(
            [
                ["发票代码", invoice["invoice_code"] or "", "发票号码", invoice["invoice_no"]],
                ["开票日期", str(invoice.get("invoice_date") or ""), "税率", " / ".join(sorted({str(l["tax_rate"]) for l in invoice["lines"]}))],
            ],
            [28 * mm, 55 * mm, 28 * mm, 55 * mm],
        ),
        Spacer(1, 3 * mm),
        _table(
            [
                ["购买方", "名称：示例科技有限公司    纳税人识别号：91110108MA00000001"],
                ["销售方", f"名称：{supplier_name}    纳税人识别号：91110108MA00000002"],
            ],
            [22 * mm, 144 * mm],
        ),
        Spacer(1, 3 * mm),
    ]
    rows: list[list[str]] = [["序号", "货物或应税劳务名称", "规格型号", "数量", "单价(未税)", "金额(未税)", "税率", "税额"]]
    for line in invoice["lines"]:
        rows.append(
            [
                str(line["line_no"]),
                line["item_desc"],
                line.get("spec_bucket") or "",
                f"{line['qty']:g}",
                f"{line['unit_price_tax_excl']:.2f}",
                f"{line['amount_tax_excl']:.2f}",
                f"{line['tax_rate'] * 100:g}%",
                f"{line['amount_tax_excl'] * line['tax_rate']:.2f}",
            ]
        )
    story.append(_table(rows, [10 * mm, 52 * mm, 30 * mm, 14 * mm, 22 * mm, 22 * mm, 12 * mm, 18 * mm]))
    story.append(Spacer(1, 2 * mm))
    story.append(
        _table(
            [
                ["金额(未税)", f"¥{invoice['amount_tax_excl']:.2f}", "税额", f"¥{invoice['tax_amount']:.2f}", "价税合计", f"¥{invoice['amount_tax_incl']:.2f}"],
            ],
            [20 * mm, 30 * mm, 16 * mm, 30 * mm, 22 * mm, 28 * mm],
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"备注：{invoice.get('remark') or '--'}    采购订单号：{po['po_no']}", st["body"]))
    story.append(Paragraph("开票人：张会计    收款人：李出纳    复核人：王主管", st["small"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("（本发票为合成数据，仅用于系统测试）", st["small"]))
    return _build(story)


def render_po(po: dict[str, Any], supplier_name: str) -> bytes:
    st = _styles()
    story: list[Any] = [
        Paragraph("采购订单（PO）", st["title"]),
        Spacer(1, 3 * mm),
        _table(
            [
                ["订单号", po["po_no"], "下单日期", str(po["ordered_at"])[:10]],
                ["供应商", supplier_name, "币种", po.get("currency", "CNY")],
            ],
            [22 * mm, 60 * mm, 24 * mm, 60 * mm],
        ),
        Spacer(1, 3 * mm),
    ]
    rows: list[list[str]] = [["行号", "物料描述", "规格桶", "数量", "未税单价", "税率", "未税金额", "含税金额", "承诺交期"]]
    for line in po["lines"]:
        rows.append(
            [
                str(line["line_no"]),
                line["item_desc"],
                line.get("spec_bucket") or "",
                f"{line['qty_ordered']:g}",
                f"{line['unit_price_tax_excl']:.2f}",
                f"{line['tax_rate'] * 100:g}%",
                f"{line['amount_tax_excl']:.2f}",
                f"{line['amount_tax_incl']:.2f}",
                str(line.get("promised_date") or ""),
            ]
        )
    story.append(_table(rows, [10 * mm, 42 * mm, 26 * mm, 12 * mm, 20 * mm, 12 * mm, 20 * mm, 20 * mm, 22 * mm]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(f"含税合计：¥{po['total_amount_tax_incl']:.2f}", st["h2"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("备注：请按承诺交期送货；到货需附送货单与质检记录。", st["body"]))
    story.append(Paragraph("（本订单为合成数据，仅用于系统测试）", st["small"]))
    return _build(story)


def render_contract(contract: dict[str, Any], supplier_name: str, po_no: str) -> bytes:
    st = _styles()
    terms = contract.get("payment_terms") or {}
    delivery = contract.get("delivery_terms") or {}
    story: list[Any] = [
        Paragraph("采购合同", st["title"]),
        Spacer(1, 3 * mm),
        _table(
            [
                ["合同编号", contract["contract_no"], "合同类型", contract["type"]],
                ["甲方", "示例科技有限公司", "乙方", supplier_name],
                ["合同金额", f"¥{contract.get('total_amount') or 0:.2f}", "订单号", po_no],
            ],
            [22 * mm, 60 * mm, 24 * mm, 60 * mm],
        ),
        Spacer(1, 4 * mm),
        Paragraph("第一条 标的与数量", st["h2"]),
        Paragraph(
            "乙方向甲方提供下述货物/服务，规格、数量以本合同明细为准；未经甲方书面同意不得变更。",
            st["body"],
        ),
        Spacer(1, 2 * mm),
    ]
    rows: list[list[str]] = [["行号", "标的物", "数量", "未税单价", "税率"]]
    for line in contract.get("lines") or []:
        rows.append(
            [
                str(line["line_no"]),
                line["item_desc"],
                f"{line['qty']:g}",
                f"{line['unit_price_tax_excl']:.2f}",
                f"{line['tax_rate'] * 100:g}%",
            ]
        )
    story.append(_table(rows, [14 * mm, 90 * mm, 18 * mm, 26 * mm, 18 * mm]))
    story += [
        Spacer(1, 3 * mm),
        Paragraph("第二条 付款条款", st["h2"]),
        Paragraph(
            f"双方约定：{terms.get('mode', '验收后电汇')}，账期 {terms.get('days', 30)} 天。"
            "甲方在收到合规发票且验收合格后按账期支付。",
            st["body"],
        ),
        Paragraph("第三条 交付与验收", st["h2"]),
        Paragraph(
            f"交付周期：{delivery.get('lead_time_days', 30)} 天；"
            f"逾期违约金：{delivery.get('penalty_clause', '每日 0.5%')}。"
            "验收标准见附件《验收要点》。",
            st["body"],
        ),
        Paragraph("第四条 违约与争议", st["h2"]),
        Paragraph("任一方违约应赔偿守约方直接损失；争议提交甲方所在地人民法院管辖。", st["body"]),
        Spacer(1, 12 * mm),
        _table([["甲方（盖章）", "乙方（盖章）"], ["授权代表：", "授权代表："], ["日期：", "日期："]], [90 * mm, 90 * mm]),
        Spacer(1, 3 * mm),
        Paragraph("（本合同为合成数据，仅用于系统测试）", st["small"]),
    ]
    return _build(story)
