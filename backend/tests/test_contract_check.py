"""合同行三单校验单元测试（不依赖 DB）。"""

from __future__ import annotations

from app.matching.contract import check_contract


def _fixtures() -> tuple[dict, dict, dict]:
    contract = {
        "contract_no": "CT-TEST",
        "total_amount": 500.0,
        "delivery_terms": {"lead_time_days": 7},
        "lines": [
            {
                "line_no": 1,
                "item_desc": "ThinkPad T14 Gen5 笔记本",
                "spec_bucket": "it_hw/thinkpad_t14_gen5",
                "qty": 5,
                "unit_price_tax_excl": 100.0,
            }
        ],
    }
    po = {
        "po_no": "PO-TEST",
        "ordered_at": "2026-01-01",
        "total_amount_tax_incl": 678.0,
        "lines": [
            {
                "line_no": 1,
                "item_desc": "ThinkPad T14 Gen5 笔记本",
                "spec_bucket": "it_hw/thinkpad_t14_gen5",
                "qty_ordered": 6,
                "unit_price_tax_excl": 120.0,
                "promised_date": "2026-01-15",
            }
        ],
    }
    invoice = {
        "invoice_no": "INV-TEST",
        "lines": [
            {
                "line_no": 1,
                "item_desc": "ThinkPad T14 Gen5 笔记本",
                "spec_bucket": "it_hw/thinkpad_t14_gen5",
                "unit_price_tax_excl": 130.0,
            }
        ],
    }
    return contract, po, invoice


def test_contract_checks_detect_price_qty_and_delivery() -> None:
    contract, po, invoice = _fixtures()
    po_diffs = check_contract(contract=contract, po=po, invoice=None, category_code="IT_HW")
    types = {diff["diff_type"] for diff in po_diffs}
    assert "contract_price" in types
    assert "contract_qty" in types
    assert "contract_delivery" in types
    assert all(diff["severity"] in {"high", "medium"} for diff in po_diffs)

    invoice_diffs = check_contract(
        contract=contract,
        po={"lines": [], "total_amount_tax_incl": 0},
        invoice=invoice,
        category_code="IT_HW",
    )
    assert any(diff["diff_type"] == "contract_price" and diff["invoice_line_no"] == 1 for diff in invoice_diffs)


def test_contract_checks_clean_when_within_tolerance() -> None:
    contract, po, invoice = _fixtures()
    po["lines"][0].update({"qty_ordered": 5, "unit_price_tax_excl": 100.0, "promised_date": "2026-01-08"})
    po["total_amount_tax_incl"] = 500.0
    invoice["lines"][0]["unit_price_tax_excl"] = 100.0
    assert check_contract(contract=contract, po=po, invoice=None, category_code="IT_HW") == []
    assert (
        check_contract(
            contract=contract,
            po={"lines": [], "total_amount_tax_incl": 0},
            invoice=invoice,
            category_code="IT_HW",
        )
        == []
    )
