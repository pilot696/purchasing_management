"""对齐 / 容差 / 分批核销的确定性单测（纯函数，无 DB）。"""

from __future__ import annotations

import numpy as np
import pytest

from app.matching.align import align_greedy, align_hungarian, pair_score
from app.matching.normalize import normalize
from app.matching.tolerance import policies_from_rows, reconcile
from app.seed.config import SeedConfig
from app.seed.dataset import build_dataset


@pytest.fixture(scope="module")
def dataset() -> dict:
    return build_dataset(SeedConfig(seed=42))


# ---------- normalizer ----------


def test_normalize_english_and_chinese_names_share_bucket() -> None:
    english = normalize("MacBook Pro 14 M3 16G 512G")
    chinese = normalize("苹果笔记本电脑 14英寸 M3芯片 16GB内存 512GB固态")
    assert english.spec_bucket == chinese.spec_bucket == "laptop/16g/512g"
    assert english.brand == chinese.brand == "苹果"
    assert english.family == chinese.family == "laptop"


def test_normalize_missing_spec_keeps_wildcards() -> None:
    item = normalize("办公电脑")
    assert item.spec_bucket == "laptop/*g/*g"
    assert item.confidence < 0.95


def test_normalize_saas_and_outsource() -> None:
    assert normalize("飞书企业版 20席位 12个月").spec_bucket == "saas/20seats/12m"
    assert normalize("市场活动外包 5 人天").spec_bucket == "outsource/5d"


# ---------- align ----------


def test_pair_score_prefers_sku_then_bucket() -> None:
    po = {"line_no": 1, "sku_code": "SKU-1", "item_desc": "MacBook Pro 14", "spec_bucket": "laptop/16g/512g"}
    assert pair_score(po, {"line_no": 1, "sku_code": "SKU-1", "item_desc": "任意写法"}) == (1.0, "L1_exact")
    score, method = pair_score(po, {"line_no": 1, "item_desc": "MacBook Pro 14 16G/512G"})
    assert (score, method) == (0.95, "L2_bucket")


def test_hungarian_beats_greedy_on_global_conflict() -> None:
    po_lines = [{"line_no": 1}, {"line_no": 2}]
    invoice_lines = [{"line_no": 1}, {"line_no": 2}]
    matrix = np.array([[0.99, 0.98], [0.97, 0.10]])  # 贪心选 (0,0) 后把 PO2 卡死
    hungarian = align_hungarian(po_lines, invoice_lines, score_matrix=matrix)
    greedy = align_greedy(po_lines, invoice_lines, score_matrix=matrix)
    assert len(hungarian.matches) == 2
    assert len(greedy.matches) == 1
    assert {(m.po_line_no, m.invoice_line_no) for m in hungarian.matches} == {(1, 2), (2, 1)}


# ---------- reconcile ----------


def _po_for_reconcile(group: dict) -> dict:
    received: dict[int, float] = {}
    for receipt in group["receipts"]:
        for line in receipt["lines"]:
            if str(line.get("qc_result", "pass")) == "fail":
                continue
            received[int(line["po_line_no"])] = received.get(int(line["po_line_no"]), 0.0) + float(line["qty"])
    po = dict(group["po"])
    po["category_code"] = group["category_code"]
    po["lines"] = [
        {**line, "qty_invoiced": 0.0, "qty_received": received.get(int(line["line_no"]), 0.0)}
        for line in group["po"]["lines"]
    ]
    return po


def _run(group: dict, dataset: dict):
    po = _po_for_reconcile(group)
    invoice = group["invoices"][0]
    align = align_hungarian(po["lines"], invoice["lines"])
    policies = policies_from_rows(dataset["master"]["tolerance"])
    return reconcile(
        po=po,
        invoice=invoice,
        align=align,
        contract=group["contract"],
        receipts=group["receipts"],
        policies=policies,
        requires_gr=group["requires_gr"],
        category_code=group["category_code"],
    )


def _group_with(dataset: dict, diff_type: str) -> dict:
    for group in dataset["doc_groups"]:
        if group["diff_types"] == [diff_type]:
            return group
    for group in dataset["doc_groups"]:
        if diff_type in group["diff_types"]:
            return group
    pytest.skip(f"数据集中没有 {diff_type} 样本")


def test_reconcile_clean_group_passes(dataset: dict) -> None:
    group = next(g for g in dataset["doc_groups"] if not g["diff_types"])
    result = _run(group, dataset)
    assert result.overall == "pass", result.diffs
    assert result.diffs == []
    assert result.line_matches[0].status in {"matched", "partial"}


def test_reconcile_qty_uses_min_ordered_received(dataset: dict) -> None:
    group = _group_with(dataset, "qty_over")
    result = _run(group, dataset)
    assert result.overall == "hold"
    over = [d for d in result.diffs if d.diff_type == "qty_over"]
    assert over, result.diffs
    match = result.line_matches[0]
    ordered = group["po"]["lines"][0]["qty_ordered"]
    received = group["receipts"][0]["lines"][0]["qty"]
    assert match.consumed_qty == pytest.approx(min(ordered, received))


def test_reconcile_partial_invoice_has_no_hard_diff(dataset: dict) -> None:
    """分批收款：可开票上限 = min(ordered, received)，本批足额开票即 matched，无 hard diff。"""
    group = next(g for g in dataset["doc_groups"] if g["partial_invoice"] and not g["diff_types"])
    result = _run(group, dataset)
    assert result.overall == "pass", result.diffs
    assert result.diffs == []
    ordered = group["po"]["lines"][0]["qty_ordered"]
    received = group["receipts"][0]["lines"][0]["qty"]
    assert received < ordered  # 分批到货
    assert result.line_matches[0].consumed_qty == pytest.approx(received)


def test_reconcile_tail_releases_with_note(dataset: dict) -> None:
    group = _group_with(dataset, "tail")
    result = _run(group, dataset)
    assert result.overall == "release_with_note"
    assert any(d.diff_type == "tail" for d in result.diffs), result.diffs


def test_reconcile_price_and_tax_hold(dataset: dict) -> None:
    for diff_type in ("price", "tax_rate"):
        group = _group_with(dataset, diff_type)
        result = _run(group, dataset)
        assert result.overall == "hold", (diff_type, result.diffs)
        assert any(d.diff_type == diff_type for d in result.diffs), (diff_type, result.diffs)


def test_reconcile_terms_conflict_detected(dataset: dict) -> None:
    group = _group_with(dataset, "terms_conflict")
    result = _run(group, dataset)
    assert any(d.diff_type == "terms_conflict" for d in result.diffs), result.diffs
    assert result.overall == "hold"


def test_reconcile_date_overdue_is_note(dataset: dict) -> None:
    group = _group_with(dataset, "date_overdue")
    result = _run(group, dataset)
    assert any(d.diff_type == "date_overdue" for d in result.diffs), result.diffs
    assert result.overall == "release_with_note"


def test_reconcile_does_not_double_report_amount(dataset: dict) -> None:
    """数量/单价类差异已解释金额差时，不应再报一条 amount（避免误报）。"""
    group = _group_with(dataset, "price")
    result = _run(group, dataset)
    types = {d.diff_type for d in result.diffs}
    assert "price" in types and "amount" not in types


