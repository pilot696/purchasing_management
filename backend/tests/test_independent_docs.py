"""独立文档测试集：非 seed 渲染器 + 手写 gold 的回归测试。"""

from __future__ import annotations

import pytest

from app.evaluation.independent_docs import DOC_CASES, compare_extract, render_case
from app.extraction.pdf_native import PARSERS
from app.extraction.router import route_document


@pytest.mark.parametrize("case", DOC_CASES, ids=[case["name"] for case in DOC_CASES])
def test_independent_document_fields_match_handwritten_gold(case: dict) -> None:
    pdf = render_case(case)
    extract, _ = PARSERS[case["doc_type"]](pdf)
    matched, total, failures = compare_extract(case["doc_type"], extract, case["gold"])
    assert matched == total, failures


@pytest.mark.parametrize("case", DOC_CASES, ids=[case["name"] for case in DOC_CASES])
def test_independent_documents_route_native(case: dict) -> None:
    pdf = render_case(case)
    route = route_document(pdf)
    assert route.kind in {"pdf_native", "hybrid"}
    assert route.has_text_layer is True
