"""抽取管线单测（纯函数 + 内存 PDF，不依赖 DB/文件系统）。"""

from __future__ import annotations

import random

import pytest

from app.extraction.router import route_document
from app.extraction.service import extract_document
from app.extraction.validators import validate_invoice
from app.schemas.extraction import InvoiceExtract
from app.seed.config import SeedConfig
from app.seed.dataset import build_dataset
from app.seed.degrade import degrade_pdf
from app.seed.render import render_contract, render_invoice, render_po


@pytest.fixture(scope="module")
def dataset() -> dict:
    return build_dataset(SeedConfig(seed=42))


def _supplier_name(dataset: dict, code: str) -> str:
    return next(s["name"] for s in dataset["master"]["suppliers"] if s["code"] == code)


def _clean_group(dataset: dict) -> dict:
    return next(g for g in dataset["doc_groups"] if not g["diff_types"] and g["contract"])


def _invoice_pdf(group: dict, dataset: dict) -> bytes:
    return render_invoice(group["invoices"][0], group["po"], _supplier_name(dataset, group["supplier_code"]))


def test_router_routes_native_templates(dataset: dict) -> None:
    group = _clean_group(dataset)
    supplier = _supplier_name(dataset, group["supplier_code"])
    invoice_route = route_document(_invoice_pdf(group, dataset))
    po_route = route_document(render_po(group["po"], supplier))
    contract_route = route_document(render_contract(group["contract"], supplier, group["po"]["po_no"]))

    assert invoice_route.kind == "pdf_native" and invoice_route.template == "cn_vat_invoice_v1"
    assert po_route.kind == "pdf_native" and po_route.template == "erp_po_v1"
    assert contract_route.kind == "pdf_native" and contract_route.template == "cn_contract_v1"
    assert invoice_route.text_ratio >= 0.15
    assert invoice_route.has_text_layer is True


def test_router_routes_scanned(dataset: dict) -> None:
    group = _clean_group(dataset)
    scanned = degrade_pdf(_invoice_pdf(group, dataset), random.Random(11))
    route = route_document(scanned)
    assert route.kind == "scanned"
    assert route.has_text_layer is False
    assert route.text_ratio < 0.05


def test_extract_invoice_fields_match_source(dataset: dict) -> None:
    group = _clean_group(dataset)
    source = group["invoices"][0]
    result = extract_document(_invoice_pdf(group, dataset), "invoice")
    assert result.ok, result.issues
    invoice = InvoiceExtract.model_validate(result.extract)
    assert invoice.invoice_no == source["invoice_no"]
    assert invoice.invoice_code == source["invoice_code"]
    assert invoice.seller_name == _supplier_name(dataset, group["supplier_code"])
    assert float(invoice.amount_tax_excl or 0) == pytest.approx(source["amount_tax_excl"], abs=0.01)
    assert float(invoice.amount_tax_incl or 0) == pytest.approx(source["amount_tax_incl"], abs=0.01)
    assert len(invoice.lines) == 1
    assert float(invoice.lines[0].qty) == pytest.approx(source["lines"][0]["qty"])
    assert invoice.po_no == group["po"]["po_no"]


def test_extract_po_and_contract(dataset: dict) -> None:
    group = _clean_group(dataset)
    supplier = _supplier_name(dataset, group["supplier_code"])
    po_result = extract_document(render_po(group["po"], supplier), "po")
    assert po_result.ok, po_result.issues
    assert po_result.extract is not None
    assert po_result.extract["po_no"] == group["po"]["po_no"]
    assert po_result.extract["supplier_name"] == supplier
    assert float(po_result.extract["total_amount_tax_incl"]) == pytest.approx(
        group["po"]["total_amount_tax_incl"], abs=0.01
    )

    contract_result = extract_document(
        render_contract(group["contract"], supplier, group["po"]["po_no"]), "contract"
    )
    assert contract_result.ok, contract_result.issues
    contract = contract_result.extract or {}
    assert contract["contract_no"] == group["contract"]["contract_no"]
    assert contract["supplier_name"] == supplier
    assert contract["payment_terms"]["days"] == 30
    assert contract["po_no"] == group["po"]["po_no"]


def test_annotations_are_normalized(dataset: dict) -> None:
    group = _clean_group(dataset)
    result = extract_document(_invoice_pdf(group, dataset), "invoice")
    fields = {a.field_name for a in result.annotations}
    assert {"invoice_no", "seller_name", "amount_tax_incl"} <= fields
    for annotation in result.annotations:
        assert 0.0 <= annotation.x0 < annotation.x1 <= 1.0
        assert 0.0 <= annotation.y0 < annotation.y1 <= 1.0


def test_extract_invoice_with_tail_has_no_high_issues(dataset: dict) -> None:
    group = next(g for g in dataset["doc_groups"] if "tail" in g["diff_types"])
    result = extract_document(_invoice_pdf(group, dataset), "invoice")
    assert result.extract is not None
    assert not [i for i in result.issues if i.severity == "high"], result.issues


def test_validator_detects_broken_total(dataset: dict) -> None:
    group = _clean_group(dataset)
    result = extract_document(_invoice_pdf(group, dataset), "invoice")
    invoice = InvoiceExtract.model_validate(result.extract)
    broken = invoice.model_copy(update={"amount_tax_incl": (invoice.amount_tax_incl or 0) + 1})
    issues = validate_invoice(broken)
    assert any(issue.code == "ARITH_TOTAL" and issue.severity == "high" for issue in issues)


def test_scanned_needs_vlm(dataset: dict) -> None:
    group = _clean_group(dataset)
    scanned = degrade_pdf(_invoice_pdf(group, dataset), random.Random(3))
    result = extract_document(scanned, "invoice")
    assert result.extract is None
    assert "vlm_required" in result.needs_review_fields
    assert result.route.kind == "scanned"
