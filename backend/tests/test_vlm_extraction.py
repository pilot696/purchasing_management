"""VLM（扫描件）抽取路径单测：FakeVisionExtractor 驱动，零网络。"""

from __future__ import annotations

import random

import pytest

from app.extraction.render_pages import render_pdf_pages
from app.extraction.service import extract_document
from app.extraction.vlm import FakeVisionExtractor
from app.seed.config import SeedConfig
from app.seed.dataset import build_dataset
from app.seed.degrade import degrade_pdf
from app.seed.render import render_invoice


@pytest.fixture(scope="module")
def dataset() -> dict:
    return build_dataset(SeedConfig(seed=42))


def _group(dataset: dict) -> dict:
    return next(g for g in dataset["doc_groups"] if not g["diff_types"] and g["contract"])


def _supplier(dataset: dict, code: str) -> str:
    return next(s["name"] for s in dataset["master"]["suppliers"] if s["code"] == code)


def _pdf(group: dict, dataset: dict) -> bytes:
    return render_invoice(group["invoices"][0], group["po"], _supplier(dataset, group["supplier_code"]))


def _payload(group: dict, dataset: dict) -> dict:
    invoice = group["invoices"][0]
    return {
        "invoice_no": invoice["invoice_no"],
        "invoice_code": invoice["invoice_code"],
        "invoice_date": str(invoice["invoice_date"]),
        "seller_name": _supplier(dataset, group["supplier_code"]),
        "buyer_name": "示例科技有限公司",
        "amount_tax_excl": invoice["amount_tax_excl"],
        "tax_amount": invoice["tax_amount"],
        "amount_tax_incl": invoice["amount_tax_incl"],
        "tax_rates": [0.13],
        "po_no": group["po"]["po_no"],
        "lines": [
            {
                "line_no": 1,
                "item_desc": invoice["lines"][0]["item_desc"],
                "qty": invoice["lines"][0]["qty"],
                "unit_price_tax_excl": invoice["lines"][0]["unit_price_tax_excl"],
                "tax_rate": 0.13,
                "amount_tax_excl": invoice["amount_tax_excl"],
                "amount_tax_incl": invoice["amount_tax_incl"],
                "bbox": [0.1, 0.2, 0.8, 0.3],
            }
        ],
    }


async def test_scanned_invoice_extracted_by_vision_model(dataset: dict) -> None:
    group = _group(dataset)
    scanned = degrade_pdf(_pdf(group, dataset), random.Random(5))
    vision = FakeVisionExtractor(_payload(group, dataset))
    result = await extract_document(scanned, "invoice", vision)

    assert result.ok, result.issues
    assert result.route.kind == "scanned"
    assert result.extractor == "fake-vlm"
    assert result.repair_attempts == 1
    assert result.extract is not None
    assert result.extract["invoice_no"] == group["invoices"][0]["invoice_no"]
    assert len(result.annotations) == 1
    assert len(vision.calls) == 1


async def test_repair_loop_recovers_from_invalid_output(dataset: dict) -> None:
    group = _group(dataset)
    scanned = degrade_pdf(_pdf(group, dataset), random.Random(6))
    invalid = {"invoice_no": "BROKEN", "lines": [{"item_desc": "缺 qty"}]}  # DocLine.qty 缺失 → Schema 失败
    vision = FakeVisionExtractor([invalid, invalid, _payload(group, dataset)])

    result = await extract_document(scanned, "invoice", vision, max_attempts=3)

    assert result.ok, result.issues
    assert result.repair_attempts == 3
    assert len(result.structured_errors) == 2
    assert vision.calls[1]["error_hint"] is not None  # 第二次调用带上了校验错误
    assert result.extract is not None and result.extract["invoice_no"] == group["invoices"][0]["invoice_no"]


async def test_repair_exhausted_marks_needs_review(dataset: dict) -> None:
    group = _group(dataset)
    scanned = degrade_pdf(_pdf(group, dataset), random.Random(7))
    vision = FakeVisionExtractor({"invoice_no": "STILL_BROKEN", "lines": [{"item_desc": "缺 qty"}]})

    result = await extract_document(scanned, "invoice", vision, max_attempts=3)

    assert result.extract is None
    assert result.needs_review_fields == ["*"]
    assert result.repair_attempts == 3
    assert len(result.structured_errors) == 3
    assert any(issue.code == "STRUCTURED_OUTPUT_FAILED" and issue.severity == "high" for issue in result.issues)


async def test_scanned_without_vision_stays_vlm_required(dataset: dict) -> None:
    group = _group(dataset)
    scanned = degrade_pdf(_pdf(group, dataset), random.Random(8))
    result = await extract_document(scanned, "invoice")  # 未配置视觉模型
    assert result.extract is None
    assert result.extractor == "none"
    assert result.needs_review_fields == ["vlm_required"]


def test_render_pages_produces_png(dataset: dict) -> None:
    group = _group(dataset)
    images = render_pdf_pages(_pdf(group, dataset))
    assert len(images) == 1
    assert images[0][:8] == b"\x89PNG\r\n\x1a\n"
    assert len(images[0]) > 10_000



def test_normalize_tax_rate_percent_to_decimal() -> None:
    from app.extraction.service import _normalize_tax_rate

    assert _normalize_tax_rate(13) == 0.13
    assert _normalize_tax_rate("13%") == 0.13
    assert _normalize_tax_rate("0.13") == 0.13
    assert _normalize_tax_rate(None) is None
    assert _normalize_tax_rate("bad") == "bad"


def test_normalize_vision_payload_tax_rates() -> None:
    from app.extraction.service import _normalize_vision_payload

    payload = {
        "tax_rates": ["13"],
        "lines": [{"line_no": 1, "tax_rate": "13"}, {"line_no": 2, "tax_rate": 0.06}],
    }
    normalized = _normalize_vision_payload("invoice", payload)
    assert normalized["tax_rates"] == [0.13]
    assert normalized["lines"][0]["tax_rate"] == 0.13
    assert normalized["lines"][1]["tax_rate"] == 0.06
