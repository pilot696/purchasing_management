"""抽取服务：路由 → 数字原生解析 → 校验 → 结果（扫描件留给 VLM 阶段）。"""

from __future__ import annotations

from pathlib import Path

from app.extraction.pdf_native import PARSERS, ParseError
from app.extraction.router import route_document
from app.extraction.validators import VALIDATORS
from app.schemas.extraction import DocType, ExtractionResult, ValidationIssue


def extract_document(pdf_bytes: bytes, doc_type: DocType) -> ExtractionResult:
    route = route_document(pdf_bytes)

    if route.kind == "scanned":
        return ExtractionResult(
            doc_type=doc_type,
            route=route,
            issues=[
                ValidationIssue(
                    code="VLM_REQUIRED",
                    message="扫描件无文本层，需走 VLM 抽取（W1 后续步骤接入）",
                    severity="low",
                )
            ],
            needs_review_fields=["vlm_required"],
        )

    parser = PARSERS[doc_type]
    try:
        extract, annotations = parser(pdf_bytes)
    except ParseError as exc:
        return ExtractionResult(
            doc_type=doc_type,
            route=route,
            issues=[ValidationIssue(code="PARSE_FAILED", message=str(exc), severity="high")],
            needs_review_fields=["*"],
        )
    except Exception as exc:  # noqa: BLE001 - 解析器异常不能静默
        return ExtractionResult(
            doc_type=doc_type,
            route=route,
            issues=[
                ValidationIssue(code="PARSE_EXCEPTION", message=f"{type(exc).__name__}: {exc}", severity="high")
            ],
            needs_review_fields=["*"],
        )

    issues = VALIDATORS[doc_type](extract)
    needs_review = sorted({issue.field for issue in issues if issue.severity == "high" and issue.field})
    return ExtractionResult(
        doc_type=doc_type,
        route=route,
        extract=extract.model_dump(mode="json"),
        issues=issues,
        annotations=annotations,
        needs_review_fields=needs_review,
    )


def extract_file(path: Path, doc_type: DocType) -> ExtractionResult:
    return extract_document(path.read_bytes(), doc_type)
