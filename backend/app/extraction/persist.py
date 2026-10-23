"""抽取结果落库：`extraction_record` + `doc_annotation` + `doc` 元数据回写（Spec §3.5）。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import tables as t
from app.schemas.extraction import ExtractionResult


def _status(result: ExtractionResult) -> str:
    if result.extract is None:
        return "failed"
    if any(issue.severity == "high" for issue in result.issues) or result.needs_review_fields:
        return "needs_review"
    return "ok"


async def persist_extraction(
    session: AsyncSession,
    *,
    doc_id: UUID,
    result: ExtractionResult,
    model: str | None = None,
    run_id: UUID | None = None,
    latency_ms: int | None = None,
) -> int:
    """幂等：同一 doc 重复抽取会替换其注解，并追加一条新的 extraction_record。"""
    await session.execute(delete(t.DocAnnotation).where(t.DocAnnotation.doc_id == doc_id))
    if result.annotations:
        await session.execute(
            insert(t.DocAnnotation),
            [
                {
                    "doc_id": doc_id,
                    "field_name": annotation.field_name,
                    "line_ref": annotation.field_name if annotation.field_name.startswith("lines[") else None,
                    "page_no": annotation.page_no,
                    "x0": annotation.x0,
                    "y0": annotation.y0,
                    "x1": annotation.x1,
                    "y1": annotation.y1,
                    "text_raw": annotation.text_raw,
                    "confidence": None,
                }
                for annotation in result.annotations
            ],
        )

    record = t.ExtractionRecord(
        doc_id=doc_id,
        extractor=result.extractor,
        model=model,
        raw_json=result.extract,
        validated_json=result.extract,
        repair_attempts=max(0, result.repair_attempts - 1),
        status=_status(result),
        error=(
            {
                "issues": [issue.model_dump() for issue in result.issues],
                "structured_errors": result.structured_errors,
                "run_id": str(run_id) if run_id else None,
            }
            if result.issues or result.structured_errors
            else None
        ),
        latency_ms=latency_ms,
    )
    session.add(record)
    await session.flush()

    await session.execute(
        update(t.Doc)
        .where(t.Doc.id == doc_id)
        .values(
            extractor_used=result.extractor,
            has_text_layer=result.route.has_text_layer,
            text_ratio=result.route.text_ratio,
            template_hit=result.route.template,
            page_count=result.route.page_count,
        )
    )
    await session.commit()
    return int(record.id)


def summarize(result: ExtractionResult) -> dict[str, Any]:
    return {
        "doc_type": result.doc_type,
        "extractor": result.extractor,
        "route": result.route.model_dump(),
        "status": _status(result),
        "issues": [issue.model_dump() for issue in result.issues],
        "annotations": len(result.annotations),
        "repair_attempts": result.repair_attempts,
        "needs_review_fields": result.needs_review_fields,
    }
