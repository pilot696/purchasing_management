"""上传文档 → 发票建档的集成测试（需 VM/本地 PostgreSQL + seed）。"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.db.session import get_session_factory
from app.extraction.ingest import InvoiceConflict, ingest_invoice
from app.models import tables as t
from app.schemas.extraction import DocLine, InvoiceExtract

pytestmark = pytest.mark.integration


def _payload(invoice_no: str) -> InvoiceExtract:
    return InvoiceExtract(
        invoice_no=invoice_no,
        invoice_code=None,
        amount_tax_excl=Decimal("100"),
        tax_amount=Decimal("13"),
        amount_tax_incl=Decimal("113"),
        lines=[
            DocLine(
                line_no=1,
                item_desc="集成测试物料",
                qty=Decimal("1"),
                unit_price_tax_excl=Decimal("100"),
                amount_tax_excl=Decimal("100"),
                tax_rate=Decimal("0.13"),
                amount_tax_incl=Decimal("113"),
            )
        ],
    )


async def _cleanup(invoice_no: str, doc_id: uuid.UUID) -> None:
    async with get_session_factory()() as session:
        invoice = await session.scalar(select(t.Invoice).where(t.Invoice.invoice_no == invoice_no))
        if invoice is not None:
            await session.execute(delete(t.InvoiceLine).where(t.InvoiceLine.invoice_id == invoice.id))
            await session.delete(invoice)
        doc = await session.get(t.Doc, doc_id)
        if doc is not None:
            await session.delete(doc)
        await session.commit()


async def test_ingest_invoice_creates_invoice_lines_and_links_doc() -> None:
    invoice_no = f"INV-INGEST-{uuid.uuid4().hex[:8].upper()}"
    async with get_session_factory()() as session:
        doc = t.Doc(
            doc_type="invoice",
            file_path="data/files/ingest-test-not-exist.pdf",
            sha256=uuid.uuid4().hex,
            page_count=1,
            has_text_layer=True,
        )
        session.add(doc)
        await session.flush()
        doc_id = doc.id
        try:
            outcome = await ingest_invoice(session, doc=doc, payload=_payload(invoice_no))
            assert outcome["created"] is True
            assert outcome["invoice"]["invoice_no"] == invoice_no
            assert outcome["lines"] == 1
            invoice = await session.scalar(select(t.Invoice).where(t.Invoice.invoice_no == invoice_no))
            assert invoice is not None and invoice.doc_id == doc_id
            lines = (await session.scalars(select(t.InvoiceLine).where(t.InvoiceLine.invoice_id == invoice.id))).all()
            assert len(lines) == 1
            refreshed_doc = await session.get(t.Doc, doc_id)
            assert refreshed_doc is not None and refreshed_doc.linked_kind == "invoice"
        finally:
            await session.rollback()
    await _cleanup(invoice_no, doc_id)


async def test_ingest_invoice_conflicts_when_invoice_linked_to_other_doc() -> None:
    invoice_no = f"INV-INGEST-{uuid.uuid4().hex[:8].upper()}"
    async with get_session_factory()() as session:
        first = t.Doc(doc_type="invoice", file_path="data/files/a.pdf", sha256=uuid.uuid4().hex)
        second = t.Doc(doc_type="invoice", file_path="data/files/b.pdf", sha256=uuid.uuid4().hex)
        session.add_all([first, second])
        await session.flush()
        first_id, second_id = first.id, second.id
        try:
            await ingest_invoice(session, doc=first, payload=_payload(invoice_no))
            with pytest.raises(InvoiceConflict):
                await ingest_invoice(session, doc=second, payload=_payload(invoice_no))
        finally:
            await session.rollback()
    await _cleanup(invoice_no, first_id)
    await _cleanup(invoice_no, second_id)


def _client():  # noqa: ANN202
    import httpx

    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", trust_env=False)


async def test_ingest_endpoint_idempotent_for_already_linked_doc() -> None:
    async with get_session_factory()() as session:
        doc = await session.scalar(
            select(t.Doc).where(t.Doc.linked_kind == "invoice", t.Doc.linked_id.is_not(None)).limit(1)
        )
    if doc is None:
        pytest.skip("没有已关联发票的文档")
    async with _client() as client:
        response = await client.post(f"/api/v1/documents/{doc.id}/ingest")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["already_linked"] is True
    assert body["invoice"]["invoice_no"]


async def test_ingest_endpoint_rejects_non_invoice_doc() -> None:
    async with get_session_factory()() as session:
        doc = await session.scalar(select(t.Doc).where(t.Doc.doc_type == "po").limit(1))
    if doc is None:
        pytest.skip("没有 PO 文档")
    async with _client() as client:
        response = await client.post(f"/api/v1/documents/{doc.id}/ingest")
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "APP_ERROR"
