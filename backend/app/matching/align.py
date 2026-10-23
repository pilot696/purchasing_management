"""行项目对齐：L1 精确 / L2 规格桶 / L3 语义近似 + 匈牙利全局最优指派（Spec §7.4）。

`align_greedy()` 是消融基线（"先到先得"），用于证明匈牙利在混排行项目上的收益。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from app.matching.normalize import normalize

ACCEPT_THRESHOLD = 0.90
AMBIGUOUS_THRESHOLD = 0.75


@dataclass(slots=True)
class LineMatch:
    po_line_no: int
    invoice_line_no: int | None = None
    contract_line_no: int | None = None
    method: str = "unmatched"
    score: float = 0.0
    ambiguous: bool = False


@dataclass(slots=True)
class AlignResult:
    matches: list[LineMatch] = field(default_factory=list)
    unmatched_po: list[int] = field(default_factory=list)
    unmatched_invoice: list[int] = field(default_factory=list)


def _norm_text(*parts: str | None) -> str:
    return "".join(str(part or "").lower().replace(" ", "") for part in parts)


def resolve_bucket(line: dict[str, Any]) -> str:
    bucket = line.get("spec_bucket")
    if bucket and bucket != "unknown/unknown":
        return str(bucket)
    normalized = normalize(
        str(line.get("item_desc") or line.get("item_name_raw") or ""),
        spec_text=line.get("spec_text"),
        category_code=line.get("category_code"),
        hints=line.get("spec_hints"),
    )
    return normalized.spec_bucket


def pair_score(po_line: dict[str, Any], invoice_line: dict[str, Any]) -> tuple[float, str]:
    po_sku, inv_sku = po_line.get("sku_code"), invoice_line.get("sku_code")
    if po_sku and inv_sku and po_sku == inv_sku:
        return 1.0, "L1_exact"
    po_bucket = resolve_bucket(po_line)
    inv_bucket = resolve_bucket(invoice_line)
    if po_bucket != "unknown/unknown" and po_bucket == inv_bucket:
        return 0.95, "L2_bucket"
    ratio = difflib.SequenceMatcher(
        None,
        _norm_text(po_line.get("item_desc"), po_line.get("spec_text")),
        _norm_text(invoice_line.get("item_desc"), invoice_line.get("spec_text")),
    ).ratio()
    if ratio >= ACCEPT_THRESHOLD:
        return round(min(0.94, ratio), 3), "L3_semantic"
    return round(ratio, 3), "candidate"


def _score_matrix(po_lines: list[dict], invoice_lines: list[dict]) -> np.ndarray:
    matrix = np.zeros((len(po_lines), len(invoice_lines)), dtype=float)
    for i, po_line in enumerate(po_lines):
        for j, invoice_line in enumerate(invoice_lines):
            matrix[i, j] = pair_score(po_line, invoice_line)[0]
    return matrix


def _materialize(
    po_lines: list[dict],
    invoice_lines: list[dict],
    pairs: list[tuple[int, int]],
    scores: np.ndarray | None = None,
) -> AlignResult:
    matches: list[LineMatch] = []
    used_po: set[int] = set()
    used_inv: set[int] = set()
    for i, j in pairs:
        if scores is not None:
            score, method = float(scores[i, j]), "matrix"
        else:
            score, method = pair_score(po_lines[i], invoice_lines[j])
        if score < AMBIGUOUS_THRESHOLD:
            continue
        matches.append(
            LineMatch(
                po_line_no=int(po_lines[i].get("line_no", i + 1)),
                invoice_line_no=int(invoice_lines[j].get("line_no", j + 1)),
                contract_line_no=po_lines[i].get("contract_line_no"),
                method=method,
                score=score,
                ambiguous=score < ACCEPT_THRESHOLD,
            )
        )
        used_po.add(i)
        used_inv.add(j)
    result = AlignResult(
        matches=sorted(matches, key=lambda m: m.po_line_no),
        unmatched_po=[int(po_lines[i].get("line_no", i + 1)) for i in range(len(po_lines)) if i not in used_po],
        unmatched_invoice=[
            int(invoice_lines[j].get("line_no", j + 1)) for j in range(len(invoice_lines)) if j not in used_inv
        ],
    )
    return result


def align_hungarian(
    po_lines: list[dict[str, Any]],
    invoice_lines: list[dict[str, Any]],
    *,
    score_matrix: np.ndarray | None = None,
) -> AlignResult:
    """全局最优一对一指派：构造得分矩阵 → linear_sum_assignment（负代价最小化）= 总分最大。"""
    if not po_lines or not invoice_lines:
        return AlignResult(
            unmatched_po=[int(line.get("line_no", i + 1)) for i, line in enumerate(po_lines)],
            unmatched_invoice=[int(line.get("line_no", j + 1)) for j, line in enumerate(invoice_lines)],
        )
    scores = score_matrix if score_matrix is not None else _score_matrix(po_lines, invoice_lines)
    rows, cols = linear_sum_assignment(-scores)
    return _materialize(po_lines, invoice_lines, list(zip(rows.tolist(), cols.tolist(), strict=False)), scores)


def align_greedy(
    po_lines: list[dict[str, Any]],
    invoice_lines: list[dict[str, Any]],
    *,
    score_matrix: np.ndarray | None = None,
) -> AlignResult:
    """消融基线：按得分从高到低贪心匹配。"""
    if not po_lines or not invoice_lines:
        return align_hungarian(po_lines, invoice_lines)
    scores = score_matrix if score_matrix is not None else _score_matrix(po_lines, invoice_lines)
    candidates = sorted(
        ((float(scores[i, j]), i, j) for i in range(len(po_lines)) for j in range(len(invoice_lines))),
        reverse=True,
    )
    used_po: set[int] = set()
    used_inv: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, i, j in candidates:
        if i in used_po or j in used_inv:
            continue
        used_po.add(i)
        used_inv.add(j)
        pairs.append((i, j))
    return _materialize(po_lines, invoice_lines, pairs, scores)
