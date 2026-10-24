"""文档接口：元数据、bbox 注解、触发抽取并落库（Spec §8.3 子集）。"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from app.api.deps import SessionDep
from app.core.errors import AppError
from app.extraction.ingest import ingest_invoice
from app.extraction.persist import persist_extraction, summarize
from app.extraction.service import extract_document
from app.models import tables as t
from app.schemas.extraction import DocType, ExtractionResult, InvoiceExtract


class ExtractionFailed(AppError):
    status_code = 422
    code = "EXTRACTION_FAILED"
    default_message = "文档抽取失败，无法建档"


def _vision_extractor_if_configured():  # noqa: ANN202 - 可选依赖，避免离线环境导入成本
    """仅在配置了真实视觉端点且未强制 FakeLLM 时启用扫描件 VLM 建档。"""
    from app.core.config import get_settings

    if get_settings().force_fake_llm:
        return None
    try:
        from app.extraction.vlm import LLMVisionExtractor
        from app.llm.router import get_router

        router = get_router()
        role = router.config.role("vision")
        provider = router.config.providers[role.provider]
        if not provider.base_url or not provider.api_key:
            return None
        return LLMVisionExtractor(router, role="vision")
    except Exception:  # noqa: BLE001 - 视觉未配置不应阻塞原生 PDF 建档
        return None

router = APIRouter(tags=["documents"])

PROJECT_ROOT = Path(__file__).resolve().parents[4]


async def _get_doc(session, doc_id: UUID) -> t.Doc:
    doc = await session.get(t.Doc, doc_id)
    if doc is None:
        raise AppError("文档不存在", details={"doc_id": str(doc_id)})
    return doc


def _resolve_file(doc: t.Doc) -> Path:
    path = (PROJECT_ROOT / doc.file_path).resolve()
    data_root = (PROJECT_ROOT / "data").resolve()
    if not path.is_relative_to(data_root):
        raise AppError("文档路径越界，拒绝读取", details={"file_path": doc.file_path})
    if not path.exists():
        raise AppError("文档文件缺失", details={"file_path": doc.file_path})
    return path


@router.get("/documents/{doc_id}")
async def get_document(doc_id: UUID, session: SessionDep) -> dict[str, Any]:
    doc = await _get_doc(session, doc_id)
    return {
        "id": str(doc.id),
        "doc_type": doc.doc_type,
        "file_path": doc.file_path,
        "sha256": doc.sha256,
        "page_count": doc.page_count,
        "has_text_layer": doc.has_text_layer,
        "text_ratio": float(doc.text_ratio) if doc.text_ratio is not None else None,
        "template_hit": doc.template_hit,
        "extractor_used": doc.extractor_used,
    }


@router.get("/documents/{doc_id}/file")
async def get_file(doc_id: UUID, session: SessionDep) -> FileResponse:
    """原始 PDF（浏览器 PDF 阅读器/下载用）。"""
    doc = await _get_doc(session, doc_id)
    path = _resolve_file(doc)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/documents/{doc_id}/page.png")
async def get_page_png(doc_id: UUID, session: SessionDep, page: int = 1) -> Response:
    """单页 PNG（PDF 抽屉用：前端用 <img> 铺满 + 归一化 bbox 覆盖层，天然对齐）。"""
    doc = await _get_doc(session, doc_id)
    path = _resolve_file(doc)

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path.read_bytes())
    if page < 1 or page > len(document):
        raise AppError("页码超出范围", details={"page": page, "page_count": len(document)})
    bitmap = document[page - 1].render(scale=2.0)
    buffer = io.BytesIO()
    bitmap.to_pil().convert("RGB").save(buffer, format="PNG")
    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/documents/{doc_id}/annotations")
async def get_annotations(doc_id: UUID, session: SessionDep) -> dict[str, Any]:
    await _get_doc(session, doc_id)
    rows = (
        await session.scalars(
            select(t.DocAnnotation)
            .where(t.DocAnnotation.doc_id == doc_id)
            .order_by(t.DocAnnotation.id)
        )
    ).all()
    return {
        "items": [
            {
                "field_name": row.field_name,
                "line_ref": row.line_ref,
                "page_no": row.page_no,
                "x0": float(row.x0) if row.x0 is not None else None,
                "y0": float(row.y0) if row.y0 is not None else None,
                "x1": float(row.x1) if row.x1 is not None else None,
                "y1": float(row.y1) if row.y1 is not None else None,
                "text_raw": row.text_raw,
            }
            for row in rows
        ],
        "total": len(rows),
    }


@router.post("/documents/{doc_id}/extract")
async def extract(doc_id: UUID, session: SessionDep) -> dict[str, Any]:
    """对已入库文档执行抽取：路由 → 解析/VLM → 校验 → 落 `extraction_record` + `doc_annotation`。"""
    doc = await _get_doc(session, doc_id)
    pdf_bytes = _resolve_file(doc).read_bytes()
    started = time.perf_counter()
    result: ExtractionResult = await extract_document(pdf_bytes, doc.doc_type)  # type: ignore[arg-type]
    latency_ms = int((time.perf_counter() - started) * 1000)
    record_id = await persist_extraction(
        session,
        doc_id=doc_id,
        result=result,
        model=None,
        latency_ms=latency_ms,
    )
    return {
        "doc_id": str(doc_id),
        "record_id": record_id,
        "doc_type": doc.doc_type,
        "latency_ms": latency_ms,
        "summary": summarize(result),
        "result": result.model_dump(mode="json"),
    }


@router.post("/documents/upload", status_code=201)
async def upload_document(
    request: Request,
    session: SessionDep,
    doc_type: str = Query(..., description="invoice|po|contract"),
    linked_no: str | None = Query(default=None, description="发票号/PO 号/合同号，用于关联业务单据"),
) -> dict[str, Any]:
    """上传 PDF 原件（裸二进制 body，Content-Type: application/pdf）。

    - 以 sha256 去重：重复上传返回已有文档并标记 duplicate=true
    - 可选 linked_no：invoice/po 自动关联到对应业务记录（关联失败不阻塞上传）
    - 上传后调用 POST /documents/{id}/extract 做抽取
    """
    import hashlib

    if doc_type not in {"invoice", "po", "contract"}:
        raise AppError("不支持的 doc_type", details={"doc_type": doc_type})
    raw = await request.body()
    if len(raw) < 8 or not raw.startswith(b"%PDF"):
        raise AppError("只支持 PDF 文件（缺少 %PDF 头）", details={"bytes": len(raw)})
    if len(raw) > 20 * 1024 * 1024:
        raise AppError("PDF 超过 20MB 限制", details={"bytes": len(raw)})
    sha = hashlib.sha256(raw).hexdigest()
    existing = await session.scalar(select(t.Doc).where(t.Doc.sha256 == sha))
    if existing is not None:
        return {**_doc_meta(existing), "duplicate": True}

    # 入库前先确认是能被解析的 PDF；只有 %PDF 头的伪文件直接拒绝，避免脏数据进 doc 表
    try:
        import pypdfium2 as pdfium

        page_count = len(pdfium.PdfDocument(raw))
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            "PDF 无法解析（文件损坏或加密）",
            details={"error": f"{type(exc).__name__}: {exc}"},
        ) from exc

    rel = f"data/files/uploads/{sha[:2]}/{sha}.pdf"
    target = PROJECT_ROOT / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)

    linked_kind: str | None = None
    linked_id: UUID | None = None
    if linked_no:
        if doc_type == "invoice":
            invoice = await session.scalar(select(t.Invoice).where(t.Invoice.invoice_no == linked_no))
            if invoice is not None:
                linked_kind, linked_id = "invoice", invoice.id
        elif doc_type == "po":
            po = await session.scalar(select(t.Po).where(t.Po.po_no == linked_no))
            if po is not None:
                linked_kind, linked_id = "po", po.id

    doc = t.Doc(
        doc_type=doc_type,
        file_path=rel,
        sha256=sha,
        mime="application/pdf",
        page_count=page_count,
        has_text_layer=None,
        linked_kind=linked_kind,
        linked_id=linked_id,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return {**_doc_meta(doc), "duplicate": False}


@router.post("/documents/{doc_id}/ingest", status_code=201)
async def ingest(
    doc_id: UUID,
    session: SessionDep,
    invoice_no: str | None = Query(default=None, description="人工指定/覆盖发票号（可选）"),
) -> dict[str, Any]:
    """抽取并建档：当前支持发票（Invoice + InvoiceLine），供 M3 直接按发票号匹配。

    - 幂等：doc 已关联发票则直接返回；发票号已存在则复用/409；
    - 扫描件在配置了视觉端点时自动走 VLM；
    - 建档成功后发票 id 写入 doc.linked_id，抽取记录写入 extraction_record。
    """
    doc = await _get_doc(session, doc_id)
    if doc.doc_type != "invoice":
        raise AppError("当前仅支持发票建档（invoice）", details={"doc_type": doc.doc_type})

    if doc.linked_kind == "invoice" and doc.linked_id:
        linked = await session.get(t.Invoice, doc.linked_id)
        if linked is not None:
            return {
                **await ingest_invoice(
                    session, doc=doc, payload=InvoiceExtract(invoice_no=linked.invoice_no)
                ),
                "doc_id": str(doc.id),
                "extractor": doc.extractor_used,
                "latency_ms": 0,
                "summary": None,
            }

    pdf_bytes = _resolve_file(doc).read_bytes()
    started = time.perf_counter()
    result: ExtractionResult = await extract_document(
        pdf_bytes,
        "invoice",
        _vision_extractor_if_configured(),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    if result.extract is None:
        raise ExtractionFailed(
            "抽取未产出可建档字段，请检查 PDF 清晰度或抽取问题",
            details={
                "doc_id": str(doc.id),
                "route": result.route.kind,
                "issues": [issue.model_dump() for issue in result.issues],
                "needs_review_fields": result.needs_review_fields,
            },
        )

    payload = InvoiceExtract.model_validate(result.extract)
    await persist_extraction(
        session,
        doc_id=doc.id,
        result=result,
        model=result.extractor,
        latency_ms=latency_ms,
    )
    doc.page_count = result.route.page_count
    doc.has_text_layer = result.route.has_text_layer
    doc.text_ratio = result.route.text_ratio
    doc.template_hit = result.route.template
    doc.extractor_used = result.extractor

    outcome = await ingest_invoice(
        session,
        doc=doc,
        payload=payload,
        invoice_no_override=invoice_no,
    )
    return {
        **outcome,
        "doc_id": str(doc.id),
        "extractor": result.extractor,
        "latency_ms": latency_ms,
        "summary": summarize(result),
    }


def _doc_meta(doc: t.Doc) -> dict[str, Any]:
    return {
        "id": str(doc.id),
        "doc_type": doc.doc_type,
        "sha256": doc.sha256,
        "page_count": doc.page_count,
        "linked_kind": doc.linked_kind,
        "linked_id": str(doc.linked_id) if doc.linked_id else None,
        "extract_url": f"/api/v1/documents/{doc.id}/extract",
        "file_url": f"/api/v1/documents/{doc.id}/file",
    }
