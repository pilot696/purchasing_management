"""扫描件退化：数字 PDF → 图片 → 旋转/模糊/JPEG/阴影 → 单页 PDF（无文本层）。

用于验证 `DocumentRouter` 的 scanned 分支与 VLM 抽取路径（Spec §7.2、§10.4）。
"""

from __future__ import annotations

import io
import random

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter
from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

rl_config.invariant = 1


def degrade_pdf(pdf_bytes: bytes, rng: random.Random) -> bytes:
    """把数字 PDF 变成"拍照/扫描件"形态的 PDF（整页为图，无文本层）。"""
    document = pdfium.PdfDocument(pdf_bytes)
    page = document[0]
    bitmap = page.render(scale=2.2)  # ≈157 dpi，与 200dpi 视觉接近且更快
    image = bitmap.to_pil().convert("RGB")

    angle = rng.uniform(-3.0, 3.0)
    image = image.rotate(angle, expand=False, fillcolor=(246, 246, 244))
    image = image.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.3, 0.8)))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=rng.randint(40, 62))
    buffer.seek(0)
    image = Image.open(buffer).convert("RGB")

    # 轻微阴影/污点，模拟扫描台
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = image.size
    draw.rectangle(
        [0, height * 0.94, width, height],
        fill=(0, 0, 0, rng.randint(20, 40)),
    )
    if rng.random() < 0.25:
        x = rng.randint(0, max(1, width - 80))
        y = rng.randint(0, max(1, height - 80))
        draw.ellipse([x, y, x + 60, y + 40], fill=(80, 80, 80, 18))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4)
    pdf.setTitle("scanned-doc")
    pdf.drawImage(ImageReader(image), 0, 0, width=A4[0], height=A4[1])
    pdf.showPage()
    pdf.save()
    return out.getvalue()
