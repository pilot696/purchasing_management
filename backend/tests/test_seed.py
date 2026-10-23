"""数据工厂单测（纯函数，零 DB）：确定性、规模、异常覆盖、算术、PDF 文本层。"""

from __future__ import annotations

import json
import random

import pytest

from app.seed.config import DIFF_PLAN, SeedConfig
from app.seed.dataset import build_dataset
from app.seed.degrade import degrade_pdf
from app.seed.render import render_contract, render_invoice, render_po


@pytest.fixture(scope="module")
def dataset() -> dict:
    return build_dataset(SeedConfig(seed=42))


def test_dataset_is_deterministic(dataset: dict) -> None:
    again = build_dataset(SeedConfig(seed=42))
    first = json.dumps(dataset, ensure_ascii=False, sort_keys=True, default=str)
    second = json.dumps(again, ensure_ascii=False, sort_keys=True, default=str)
    assert first == second, "同一 seed 必须产生完全一致的数据集"


def test_scale_matches_config(dataset: dict) -> None:
    counts = dataset["meta"]["counts"]
    assert counts["suppliers"] == 30
    assert counts["skus"] == 100
    assert counts["policies"] == 60
    assert counts["po_history"] == 300
    assert counts["prs"] == 120
    assert counts["doc_groups"] == 80
    assert counts["rules"] >= 10


def test_every_diff_type_has_enough_positive_samples(dataset: dict) -> None:
    coverage = dataset["meta"]["m3_diff_coverage"]
    for diff_type, minimum in DIFF_PLAN:
        assert coverage.get(diff_type, 0) >= minimum, f"{diff_type} 样本不足: {coverage.get(diff_type, 0)} < {minimum}"


def test_m3_overall_is_consistent_with_diffs(dataset: dict) -> None:
    hard = {"price", "tax_rate", "qty_over", "terms_conflict"}
    for case in dataset["m3_gold"]:
        types = {d["type"] for d in case["expected_diffs"]}
        if types & hard:
            assert case["expected_overall"] == "hold", case["case_id"]
        elif types & {"tail", "date_overdue"}:
            assert case["expected_overall"] == "release_with_note", case["case_id"]
        else:
            assert case["expected_overall"] == "pass", case["case_id"]


def test_invoice_arithmetic(dataset: dict) -> None:
    for group in dataset["doc_groups"]:
        for invoice in group["invoices"]:
            line_sum = round(sum(line["amount_tax_excl"] for line in invoice["lines"]), 2)
            assert abs(line_sum - invoice["amount_tax_excl"]) <= 0.01, group["case_id"]
            expected_total = round(invoice["amount_tax_excl"] + invoice["tax_amount"], 2)
            delta = abs(expected_total - invoice["amount_tax_incl"])
            if "tail" in group["diff_types"]:
                assert delta <= 0.05
            else:
                assert delta <= 0.01


def test_requires_gr_categories(dataset: dict) -> None:
    flags = {item["category_code"]: item["requires_gr"] for item in dataset["master"]["categories"]}
    assert flags["IT_HW"] is True
    assert flags["ADMIN"] is True
    assert flags["SAAS"] is False


def test_ratio_targets_are_close(dataset: dict) -> None:
    groups = dataset["doc_groups"]
    scanned = sum(1 for g in groups if g["scanned"]) / len(groups)
    no_contract = sum(1 for g in groups if g["contract"] is None) / len(groups)
    assert 0.05 <= scanned <= 0.25
    assert 0.03 <= no_contract <= 0.18


def test_pdf_has_text_layer(dataset: dict) -> None:
    group = dataset["doc_groups"][0]
    supplier_name = "北京华信科技有限公司"
    invoice_pdf = render_invoice(group["invoices"][0], group["po"], supplier_name)
    assert len(invoice_pdf) > 1000
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(invoice_pdf)) as pdf:
        text = pdf.pages[0].extract_text() or ""
    assert group["invoices"][0]["invoice_no"] in text
    assert "增值税" in text


def test_scan_degrade_removes_text_layer(dataset: dict) -> None:
    group = dataset["doc_groups"][0]
    native = render_po(group["po"], "北京华信科技有限公司")
    assert group["po"]["po_no"] in pdfplumber_text(native), "数字原生 PO 必须可抽取到订单号"

    from app.seed.persist import _text_stats

    has_text, ratio = _text_stats(native)
    assert has_text is True and ratio >= 0.15, f"原生 PO 的文本层占比应 ≥ 0.15，实际 {ratio}"

    scanned = degrade_pdf(native, random.Random(7))
    assert pdfplumber_text(scanned).strip() == "", "退化后的扫描件不应再有文本层"
    has_text_scanned, ratio_scanned = _text_stats(scanned)
    assert has_text_scanned is False and ratio_scanned == 0


def test_pr_gold_matches_budget_and_verdict(dataset: dict) -> None:
    budgets = {
        (b["department_cost_center"], b["category_code"]): b
        for b in dataset["master"]["budgets"]
    }
    pr_by_no = {pr["pr_no"]: pr for pr in dataset["prs"]}
    for gold in dataset["pr_gold"]:
        pr = pr_by_no[gold["pr_no"]]
        budget = budgets[(pr["department_cost_center"], pr["category_code"])]
        available = budget["amount_total"] - budget["amount_used"] - budget["amount_committed"]
        actually_over = pr["estimated_amount"] > available
        # over_budget 标记必须与"实际可用额"推导一致（否则 M1 评测的 gold 会失真）
        assert gold["expected_over_budget"] == actually_over or gold["anomaly"] == "over_budget"

        if gold["expected_policy_block"]:
            assert gold["expected_verdict"] == "block"
            assert any(f["type"] == "policy_ref" for f in gold["expected_findings"])
        elif gold["expected_findings"]:
            assert gold["expected_verdict"] == "needs_info"
        else:
            assert gold["expected_verdict"] == "pass"

        # owner 存在 spec 槽位里（pr_item 没有独立列）
        spec = pr["items"][0]["spec"]
        assert "owner" in spec or any(f["field"].endswith("spec.owner") for f in gold["expected_findings"])


def test_m1_verdict_distribution_has_all_classes(dataset: dict) -> None:
    distribution = dataset["meta"]["m1_verdict_distribution"]
    for verdict in ("pass", "needs_info", "block"):
        assert distribution[verdict] > 0, f"评测集缺少 {verdict} 样本: {distribution}"


def test_contract_contains_payment_terms(dataset: dict) -> None:
    with_contract = next(g for g in dataset["doc_groups"] if g["contract"])
    pdf = render_contract(with_contract["contract"], "北京华信科技有限公司", with_contract["po"]["po_no"])
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        text = doc.pages[0].extract_text() or ""
    assert "付款条款" in text
    assert with_contract["contract"]["contract_no"] in text


def pdfplumber_text(pdf_bytes: bytes) -> str:
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as doc:
        return doc.pages[0].extract_text() or ""
