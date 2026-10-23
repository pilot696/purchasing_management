"""DocumentRouter：按文本层密度 + 版式模板分流（Spec §7.2，阈值经 W1 数据标定为 0.15）。"""

from __future__ import annotations

import io

import pdfplumber

from app.schemas.extraction import DocRoute

# 版式模板锚点：命中越多越可信（发票 / 订单 / 合同）
TEMPLATE_ANCHORS: dict[str, tuple[str, ...]] = {
    "cn_vat_invoice_v1": ("增值税", "发票号码", "价税合计"),
    "erp_po_v1": ("采购订单", "订单号", "供应商"),
    "cn_contract_v1": ("采购合同", "合同编号", "甲方"),
}

NATIVE_THRESHOLD = 0.15  # 每 1e3 pt² 字符数；原生 PO 实测 0.435~0.509
SCANNED_THRESHOLD = 0.05


def detect_template(text: str) -> str | None:
    best: tuple[int, str] | None = None
    for name, anchors in TEMPLATE_ANCHORS.items():
        hits = sum(1 for anchor in anchors if anchor in text)
        if hits >= 2 and (best is None or hits > best[0]):
            best = (hits, name)
    return best[1] if best else None


def compute_text_ratio(pdf_bytes: bytes) -> tuple[float, bool, int]:
    """返回 (text_ratio, has_text_layer, page_count)。口径与 Spec §7.2 一致。"""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        ratios = []
        for page in pdf.pages:
            text = (page.extract_text() or "").strip()
            area = max(1.0, float(page.width) * float(page.height))
            ratios.append(len(text) / area * 1000)
        page_count = len(pdf.pages)
    ratio = round(sum(ratios) / max(1, len(ratios)), 4)
    return ratio, ratio >= NATIVE_THRESHOLD, page_count


def route_document(pdf_bytes: bytes) -> DocRoute:
    ratio, has_text, page_count = compute_text_ratio(pdf_bytes)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        first_text = pdf.pages[0].extract_text() or ""
    template = detect_template(first_text)
    if ratio >= NATIVE_THRESHOLD and template:
        return DocRoute(
            kind="pdf_native",
            template=template,
            confidence=0.95,
            text_ratio=ratio,
            has_text_layer=has_text,
            page_count=page_count,
        )
    if ratio < SCANNED_THRESHOLD:
        return DocRoute(kind="scanned", confidence=0.9, text_ratio=ratio, page_count=page_count)
    return DocRoute(
        kind="hybrid",
        template=template,
        confidence=0.6,
        text_ratio=ratio,
        has_text_layer=has_text,
        page_count=page_count,
    )
