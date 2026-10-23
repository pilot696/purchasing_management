"""PDF 页面渲染为 PNG（供 VLM 抽取与前端降级预览）。"""

from __future__ import annotations

import io

import pypdfium2 as pdfium


def render_pdf_pages(pdf_bytes: bytes, *, scale: float = 2.0) -> list[bytes]:
    """逐页渲染成 PNG 字节；scale=2.0 ≈ 144dpi。"""
    document = pdfium.PdfDocument(pdf_bytes)
    images: list[bytes] = []
    for page in document:
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        images.append(buffer.getvalue())
    return images
