"""抽取服务：路由 → 数字原生解析（结构优先）→ 失败/扫描件走 VLM（结构化 + repair）→ 校验。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.extraction.pdf_native import PARSERS, ParseError
from app.extraction.render_pages import render_pdf_pages
from app.extraction.router import route_document
from app.extraction.validators import VALIDATORS
from app.extraction.vlm import SCHEMAS, VisionExtractor, structured_vision_extract
from app.schemas.extraction import Annotation, DocRoute, DocType, ExtractionResult, ValidationIssue


def _annotations_from_payload(payload: dict[str, Any]) -> list[Annotation]:
    """VLM 若返回归一化 bbox（左上原点），转成前端可用的注解；没有就返回空。"""
    annotations: list[Annotation] = []
    for index, line in enumerate(payload.get("lines") or []):
        bbox = line.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = (float(v) for v in bbox)
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            continue
        annotations.append(
            Annotation(
                field_name=f"lines[{index}].item_desc",
                page_no=int(line.get("page_no") or 1),
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                text_raw=str(line.get("item_desc") or ""),
            )
        )
    return annotations


async def _extract_with_vision(
    pdf_bytes: bytes,
    doc_type: DocType,
    route: DocRoute,
    vision: VisionExtractor | None,
    *,
    max_attempts: int,
) -> ExtractionResult:
    if vision is None:
        return ExtractionResult(
            doc_type=doc_type,
            route=route,
            extractor="none",
            issues=[
                ValidationIssue(
                    code="VLM_REQUIRED",
                    message="扫描件无文本层，需配置视觉模型后走 VLM 抽取",
                    severity="low",
                )
            ],
            needs_review_fields=["vlm_required"],
        )

    images = render_pdf_pages(pdf_bytes)
    outcome = await structured_vision_extract(vision, images, doc_type, max_attempts=max_attempts)
    if outcome.payload is None:
        return ExtractionResult(
            doc_type=doc_type,
            route=route,
            extractor=vision.name,
            repair_attempts=outcome.attempts,
            structured_errors=outcome.errors,
            issues=[
                ValidationIssue(
                    code="STRUCTURED_OUTPUT_FAILED",
                    message=f"视觉抽取 {outcome.attempts} 次仍未通过 JSON Schema/Pydantic 校验",
                    severity="high",
                )
            ],
            needs_review_fields=["*"],
        )

    model = SCHEMAS[doc_type].model_validate(outcome.payload)
    issues = VALIDATORS[doc_type](model)
    needs_review = sorted({issue.field for issue in issues if issue.severity == "high" and issue.field})
    return ExtractionResult(
        doc_type=doc_type,
        route=route,
        extract=outcome.payload,
        extractor=vision.name,
        repair_attempts=outcome.attempts,
        structured_errors=outcome.errors,
        issues=issues,
        annotations=_annotations_from_payload(outcome.payload),
        needs_review_fields=needs_review,
    )


async def extract_document(
    pdf_bytes: bytes,
    doc_type: DocType,
    vision: VisionExtractor | None = None,
    *,
    max_attempts: int = 3,
) -> ExtractionResult:
    try:
        route = route_document(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 - 损坏/非法 PDF 不能把抽取接口打挂
        return ExtractionResult(
            doc_type=doc_type,
            route=DocRoute(kind="unsupported", confidence=0.0),
            extractor="none",
            issues=[
                ValidationIssue(
                    code="ROUTE_FAILED",
                    message=f"无法解析 PDF（{type(exc).__name__}: {exc}）",
                    severity="high",
                )
            ],
            needs_review_fields=["*"],
        )

    if route.kind in {"pdf_native", "hybrid"}:
        try:
            extract, annotations = PARSERS[doc_type](pdf_bytes)
        except ParseError as exc:
            if vision is None:
                return ExtractionResult(
                    doc_type=doc_type,
                    route=route,
                    extractor="pdf_native",
                    issues=[ValidationIssue(code="PARSE_FAILED", message=str(exc), severity="high")],
                    needs_review_fields=["*"],
                )
            return await _extract_with_vision(pdf_bytes, doc_type, route, vision, max_attempts=max_attempts)
        except Exception as exc:  # noqa: BLE001 - 解析器异常不能静默
            return ExtractionResult(
                doc_type=doc_type,
                route=route,
                extractor="pdf_native",
                issues=[
                    ValidationIssue(
                        code="PARSE_EXCEPTION", message=f"{type(exc).__name__}: {exc}", severity="high"
                    )
                ],
                needs_review_fields=["*"],
            )
        issues = VALIDATORS[doc_type](extract)
        needs_review = sorted({issue.field for issue in issues if issue.severity == "high" and issue.field})
        return ExtractionResult(
            doc_type=doc_type,
            route=route,
            extract=extract.model_dump(mode="json"),
            extractor="pdf_native",
            issues=issues,
            annotations=annotations,
            needs_review_fields=needs_review,
        )

    return await _extract_with_vision(pdf_bytes, doc_type, route, vision, max_attempts=max_attempts)


async def extract_file(
    path: Path, doc_type: DocType, vision: VisionExtractor | None = None, *, max_attempts: int = 3
) -> ExtractionResult:
    return await extract_document(path.read_bytes(), doc_type, vision, max_attempts=max_attempts)
