"""文档接口：元数据、bbox 注解、触发抽取并落库（Spec §8.3 子集）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import SessionDep
from app.core.errors import AppError
from app.extraction.persist import persist_extraction, summarize
from app.extraction.service import extract_document
from app.models import tables as t
from app.schemas.extraction import DocType, ExtractionResult

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
