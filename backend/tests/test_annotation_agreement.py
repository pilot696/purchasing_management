"""标注模板/校验/Cohen κ 的离线单元测试（不依赖 DB）。"""

from __future__ import annotations

import json

from app.evaluation.annotation import (
    build_template,
    cohen_kappa,
    compare_annotation_files,
    normalize_value,
    render_markdown,
    validate_annotations,
)

INVOICE_VALUES = {
    "invoice_no": "INV-2026-0001",
    "invoice_date": "2026-01-15",
    "seller_name": "Acme Components Ltd.",
    "seller_tax_id": "91310000MA1K000001",
    "amount_tax_excl": "1000.00",
    "tax_rate": "13%",
    "amount_tax_incl": "1130.00",
    "currency": "CNY",
}


def _row(annotator: str, doc_id: str = "DOC-1", **overrides) -> dict:
    values = {**INVOICE_VALUES, **overrides}
    return {
        "doc_id": doc_id,
        "doc_type": "invoice",
        "annotator": annotator,
        "values": values,
        "notes": "",
    }


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_template_is_blank_and_covers_all_fields() -> None:
    rows = build_template(
        [{"doc_id": "DOC-1", "doc_type": "invoice", "source_path": "/tmp/a.pdf", "page_count": 1}],
        annotator="",
    )
    assert len(rows) == 1
    assert set(rows[0]["values"]) == set(INVOICE_VALUES)
    assert all(value is None for value in rows[0]["values"].values())
    assert rows[0]["source_path"] == "/tmp/a.pdf"


def test_normalize_value_handles_real_world_formats() -> None:
    assert normalize_value("tax_rate", "13%") == normalize_value("tax_rate", "0.13")
    assert normalize_value("tax_rate", "13") == "0.1300"
    assert normalize_value("amount_tax_incl", "¥1,234.5") == "1234.50"
    assert normalize_value("invoice_date", "2026年1月5日") == "2026-01-05"
    assert normalize_value("invoice_date", "2026/1/5") == "2026-01-05"
    assert normalize_value("seller_name", "  Acme   Components  ") == "acme components"
    assert normalize_value("seller_tax_id", None) is None


def test_validation_rejects_missing_or_unknown_fields() -> None:
    missing = _row("alice")
    del missing["values"]["currency"]
    result = validate_annotations([missing])
    assert not result["ok"]
    assert any("缺少字段" in error for error in result["errors"])

    unknown = _row("alice")
    unknown["values"]["buyer_name"] = "x"
    result = validate_annotations([unknown])
    assert not result["ok"]
    assert any("未知字段" in error for error in result["errors"])


def test_validation_rejects_duplicate_doc_and_bad_amount() -> None:
    duplicate = validate_annotations([_row("alice"), _row("alice")])
    assert not duplicate["ok"]
    assert any("重复" in error for error in duplicate["errors"])

    bad = _row("alice", amount_tax_incl="not-a-number")
    invalid = validate_annotations([bad])
    assert not invalid["ok"]
    assert any("非法金额" in error for error in invalid["errors"])


def test_cohen_kappa_known_examples() -> None:
    assert cohen_kappa(["A", "B", "A", "B"], ["A", "B", "A", "B"])["kappa"] == 1.0
    # 4 个样本：3 一致，边际分布 (A=2,B=2)/(A=1,B=3) → po=0.75, pe=0.5, κ=0.5
    result = cohen_kappa(["A", "A", "B", "B"], ["A", "B", "B", "B"])
    assert result["observed_agreement"] == 0.75
    assert result["kappa"] == 0.5
    assert cohen_kappa([], [])["kappa"] is None


def test_compare_two_annotators_reports_field_agreement(tmp_path) -> None:
    a_rows = [
        _row("alice", "DOC-1"),
        _row("alice", "DOC-2", invoice_no="INV-2026-0002"),
    ]
    b_rows = [
        # 名称大小写/空格、日期格式、税率写法不同，但归一一一致
        _row(
            "bob",
            "DOC-1",
            seller_name="  ACME   COMPONENTS LTD.",
            invoice_date="2026/01/15",
            tax_rate="0.13",
            amount_tax_incl="1130.01",  # 真实分歧
        ),
        _row("bob", "DOC-2", invoice_no="INV-2026-0003", seller_tax_id=None),  # 真实分歧 + 单方缺失
    ]
    path_a = tmp_path / "alice.jsonl"
    path_b = tmp_path / "bob.jsonl"
    _write_jsonl(path_a, a_rows)
    _write_jsonl(path_b, b_rows)

    report = compare_annotation_files(path_a, path_b)
    assert report["ok"] is True
    assert report["status"] == "complete"
    assert report["documents"]["common"] == 2
    assert report["annotators"] == ["alice", "bob"]
    assert report["fields"]["seller_name"]["observed_agreement"] == 1.0
    assert report["fields"]["invoice_no"]["agreement_count"] == 1
    assert report["fields"]["invoice_no"]["n"] == 2
    assert report["fields"]["amount_tax_incl"]["agreement_count"] == 1
    assert report["fields"]["seller_tax_id"]["one_missing"] == 1
    assert 0.0 <= report["overall"]["macro_field_agreement"] <= 1.0

    markdown = render_markdown(report)
    assert "Cohen" not in markdown  # 报告里用 κ 符号而非英文名，避免误导
    assert "seller_name" in markdown
    assert "不代表模型准确率" in markdown


def test_compare_requires_two_distinct_annotators(tmp_path) -> None:
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    _write_jsonl(path_a, [_row("alice")])
    _write_jsonl(path_b, [_row("alice")])
    report = compare_annotation_files(path_a, path_b)
    assert report["ok"] is False
    assert any("不同标注者" in error for error in report["errors"])


def test_compare_rejects_no_common_documents(tmp_path) -> None:
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    _write_jsonl(path_a, [_row("alice", "DOC-1")])
    _write_jsonl(path_b, [_row("bob", "DOC-2")])
    report = compare_annotation_files(path_a, path_b)
    assert report["ok"] is False
    assert any("共同 doc_id" in error for error in report["errors"])
