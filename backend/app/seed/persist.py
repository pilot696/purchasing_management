"""把 `build_dataset()` 的纯数据落成：PostgreSQL 行 + PDF 文件 + gold JSON。

幂等策略：`cfg.clean=True`（默认）时先 TRUNCATE 种子表并清空 `data/files` 与 `data/gold`；
同一 seed 重新生成得到完全一致的数据集（PDF 因 `rl_config.invariant` 也字节稳定）。
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pdfplumber
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models import tables as t
from app.seed.config import PROJECT_ROOT, SeedConfig
from app.seed.degrade import degrade_pdf
from app.seed.quotes import generate_quotes
from app.seed.render import render_contract, render_invoice, render_po

TRUNCATE_SQL = text(
    "TRUNCATE TABLE "
    "invoice_line, invoice, receipt_line, goods_receipt, po_line, po, contract_line, contract, "
    "doc_annotation, extraction_record, doc, pr_item, pr, quote_line, quote, "
    "match_diff, match_line, match_run, tail_diff_pool, "
    "budget, supplier_cert, supplier_weight, service_ticket, inventory_item, "
    "po_line_history, policy_chunk, sku_catalog, rule, category_template, supplier, "
    "app_user, department, tolerance_policy "
    "RESTART IDENTITY CASCADE"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_stats(pdf_bytes: bytes) -> tuple[bool, float]:
    """文本层占比，与 Spec §7.2 `DocumentRouter` 同量纲：每 1e3 pt² 面积的有效字符数。

    数字原生 PO ≈ 0.5、发票 ≈ 0.8~1.4、合同 ≈ 1.2（≥0.15 判 native），扫描件 = 0。
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        text = (page.extract_text() or "").strip()
        area = max(1.0, float(page.width) * float(page.height))
    ratio = min(9.99, len(text) / area * 1000)
    return len(text) >= 40, round(ratio, 4)


async def _insert(session: AsyncSession, model: Any, rows: list[dict[str, Any]]) -> None:
    if rows:
        await session.execute(insert(model), rows)


async def persist_dataset(dataset: dict[str, Any], cfg: SeedConfig) -> dict[str, Any]:
    if cfg.clean:
        for directory in (cfg.files_dir, cfg.gold_dir):
            if directory.exists():
                resolved = directory.resolve()
                assert resolved.is_relative_to(PROJECT_ROOT.resolve()), f"refuse to delete outside repo: {resolved}"
                shutil.rmtree(resolved)
    cfg.files_dir.mkdir(parents=True, exist_ok=True)
    (cfg.gold_dir / "m1").mkdir(parents=True, exist_ok=True)
    (cfg.gold_dir / "m3").mkdir(parents=True, exist_ok=True)

    master = dataset["master"]
    files_written = 0
    file_bytes = 0

    async with get_session_factory()() as session:
        if cfg.clean:
            await session.execute(TRUNCATE_SQL)

        # ---------- 主数据 ----------
        dept_rows = [{**{k: v for k, v in d.items()}, "id": uuid.uuid4()} for d in master["departments"]]
        await _insert(session, t.Department, dept_rows)
        dept_id = {row["cost_center"]: row["id"] for row in dept_rows}

        user_rows = [
            {
                "id": uuid.uuid4(),
                "name": u["name"],
                "email": u["email"],
                "role": u["role"],
                "department_id": dept_id[u["department_cost_center"]],
            }
            for u in master["users"]
        ]
        await _insert(session, t.AppUser, user_rows)
        user_id = {row["email"]: row["id"] for row in user_rows}

        await _insert(
            session,
            t.Budget,
            [
                {
                    "id": uuid.uuid4(),
                    "department_id": dept_id[b["department_cost_center"]],
                    "fiscal_year": b["fiscal_year"],
                    "category_code": b["category_code"],
                    "amount_total": b["amount_total"],
                    "amount_committed": b["amount_committed"],
                    "amount_used": b["amount_used"],
                    "currency": b["currency"],
                }
                for b in master["budgets"]
            ],
        )

        cat_rows = [{**c, "id": uuid.uuid4()} for c in master["categories"]]
        await _insert(session, t.CategoryTemplate, cat_rows)
        cat_id = {row["category_code"]: row["id"] for row in cat_rows}

        await _insert(
            session,
            t.Rule,
            [
                {
                    "id": uuid.uuid4(),
                    "category_template_id": cat_id[r["category_code"]],
                    "code": r["code"],
                    "type": r["type"],
                    "severity": r["severity"],
                    "field_path": r["field_path"],
                    "expression": r["expression"],
                    "message_template": r["message_template"],
                    "policy_clause_ref": r["policy_clause_ref"],
                    "enabled": r.get("enabled", True),
                    "version": r.get("version", 1),
                }
                for r in master["rules"]
            ],
        )

        supplier_rows = [
            {
                "id": uuid.uuid4(),
                "code": s["code"],
                "name": s["name"],
                "unified_social_credit_code": s["unified_social_credit_code"],
                "category_scope": s["category_scope"],
                "status": s["status"],
                "risk_flags": s["risk_flags"],
                "onboarded_at": s["onboarded_at"],
            }
            for s in master["suppliers"]
        ]
        await _insert(session, t.Supplier, supplier_rows)
        supplier_id = {row["code"]: row["id"] for row in supplier_rows}

        await _insert(
            session,
            t.SupplierCert,
            [
                {
                    "id": uuid.uuid4(),
                    "supplier_id": supplier_id[c["supplier_code"]],
                    "cert_type": c["cert_type"],
                    "cert_no": c["cert_no"],
                    "issuer": c["issuer"],
                    "valid_from": c["valid_from"],
                    "valid_to": c["valid_to"],
                }
                for c in master["certs"]
            ],
        )

        await _insert(
            session,
            t.SupplierWeight,
            [
                {
                    "id": uuid.uuid4(),
                    "category_code": w["category_code"],
                    "dimension": w["dimension"],
                    "weight": w["weight"],
                    "version": w["version"],
                }
                for w in master["supplier_weights"]
            ],
        )

        sku_rows = [{**sku, "id": uuid.uuid4()} for sku in master["skus"]]
        for row in sku_rows:
            row.pop("aliases", None)
        sku_payload = [
            {
                "id": row["id"],
                "category_code": row["category_code"],
                "sku_code": row["sku_code"],
                "name": row["name"],
                "brand": row["brand"],
                "spec": row["spec"],
                "spec_bucket": row["spec_bucket"],
                "unit": row["unit"],
                "ref_price_p50": row["ref_price_p50"],
                "aliases": sku["aliases"],
            }
            for row, sku in zip(sku_rows, master["skus"], strict=True)
        ]
        await _insert(session, t.SkuCatalog, sku_payload)
        sku_id = {row["sku_code"]: row["id"] for row in sku_rows}

        await _insert(
            session,
            t.InventoryItem,
            [
                {
                    "id": uuid.uuid4(),
                    "category_code": item["category_code"],
                    "spec_bucket": item["spec_bucket"],
                    "sku_id": sku_id.get(item["sku_code"]),
                    "qty_on_hand": item["qty_on_hand"],
                    "qty_reserved": item["qty_reserved"],
                    "warehouse": item["warehouse"],
                }
                for item in master["inventory"]
            ],
        )

        await _insert(
            session,
            t.PolicyChunk,
            [
                {
                    "id": uuid.uuid4(),
                    "category_code": p["category_code"],
                    "clause_no": p["clause_no"],
                    "title": p["title"],
                    "content": p["content"],
                    "effective_from": p["effective_from"],
                }
                for p in master["policies"]
            ],
        )

        await _insert(
            session,
            t.PoLineHistory,
            [
                {
                    "id": uuid.uuid4(),
                    "po_no": h["po_no"],
                    "line_no": h["line_no"],
                    "item_name_raw": h["item_name_raw"],
                    "spec_text": h["spec_text"],
                    "spec_bucket": h["spec_bucket"],
                    "supplier_id": supplier_id[h["supplier_code"]],
                    "unit_price_tax_excl": h["unit_price_tax_excl"],
                    "tax_rate": h["tax_rate"],
                    "qty": h["qty"],
                    "order_date": h["order_date"],
                    "category_code": h["category_code"],
                }
                for h in master["po_history"]
            ],
        )

        await _insert(
            session,
            t.ServiceTicket,
            [
                {
                    "id": uuid.uuid4(),
                    "supplier_id": supplier_id[ticket["supplier_code"]],
                    "po_id": None,
                    "severity": ticket["severity"],
                    "opened_at": ticket["opened_at"],
                    "first_response_at": ticket["first_response_at"],
                    "resolved_at": ticket["resolved_at"],
                    "escalated": ticket["escalated"],
                    "reopened": ticket["reopened"],
                    "note": ticket["note"],
                }
                for ticket in master["tickets"]
            ],
        )

        await _insert(
            session,
            t.TolerancePolicy,
            [{**row, "id": uuid.uuid4()} for row in master["tolerance"]],
        )

        # ---------- PR（M1 用） ----------
        pr_rows: list[dict[str, Any]] = []
        pr_item_rows: list[dict[str, Any]] = []
        for pr in dataset["prs"]:
            pr_id = uuid.uuid4()
            pr_rows.append(
                {
                    "id": pr_id,
                    "pr_no": pr["pr_no"],
                    "requester_id": user_id[pr["requester_email"]],
                    "department_id": dept_id[pr["department_cost_center"]],
                    "category_code": pr["category_code"],
                    "title": pr["title"],
                    "needed_by": pr["needed_by"],
                    "estimated_amount": pr["estimated_amount"],
                    "currency": pr["currency"],
                    "status": pr["status"],
                    "created_at": pr["created_at"],
                }
            )
            for item in pr["items"]:
                pr_item_rows.append(
                    {
                        "id": uuid.uuid4(),
                        "pr_id": pr_id,
                        "line_no": item["line_no"],
                        "item_name_raw": item["item_name_raw"],
                        "spec": item["spec"],
                        "qty": item["qty"],
                        "unit": item["unit"],
                        "unit_price_est": item["unit_price_est"],
                        "amount_est": item["amount_est"],
                        "sku_id": sku_id.get(item["sku_code"]),
                        "spec_bucket": item["spec_bucket"],
                    }
                )
        await _insert(session, t.Pr, pr_rows)
        await _insert(session, t.PrItem, pr_item_rows)

        # ---------- M3 单据 + 文件 ----------
        supplier_name = {s["code"]: s["name"] for s in master["suppliers"]}
        doc_rows: list[dict[str, Any]] = []
        contract_rows: list[dict[str, Any]] = []
        contract_line_rows: list[dict[str, Any]] = []
        po_rows: list[dict[str, Any]] = []
        po_line_rows: list[dict[str, Any]] = []
        receipt_rows: list[dict[str, Any]] = []
        receipt_line_rows: list[dict[str, Any]] = []
        invoice_rows: list[dict[str, Any]] = []
        invoice_line_rows: list[dict[str, Any]] = []

        import random as _random

        for group in dataset["doc_groups"]:
            case_dir = cfg.files_dir / group["case_id"]
            case_dir.mkdir(parents=True, exist_ok=True)
            rng = _random.Random(
                int.from_bytes(
                    hashlib.sha256(f"{cfg.seed}:{group['case_id']}".encode()).digest()[:8], "big"
                )
            )
            group_requires_gr = group["requires_gr"]
            po_id = uuid.uuid4()
            contract_id = uuid.uuid4() if group["contract"] else None
            receipt_id = uuid.uuid4() if group_requires_gr else None
            invoice_ids = [uuid.uuid4() for _ in group["invoices"]]

            # 合同
            if group["contract"]:
                pdf = render_contract(group["contract"], supplier_name[group["supplier_code"]], group["po"]["po_no"])
                scanned = group["scanned"]
                if scanned:
                    pdf = degrade_pdf(pdf, rng)
                rel = _write_pdf(cfg, case_dir / ("contract.scan.pdf" if scanned else "contract.pdf"), pdf)
                files_written += 1
                file_bytes += len(pdf)
                doc_id = uuid.uuid4()
                doc_rows.append(
                    _doc_row(
                        doc_id,
                        "contract",
                        rel,
                        pdf,
                        template="cn_contract_v1",
                        scanned=scanned,
                        linked_kind="contract",
                        linked_id=contract_id,
                    )
                )
                contract_rows.append(
                    {
                        "id": contract_id,
                        "contract_no": group["contract"]["contract_no"],
                        "supplier_id": supplier_id[group["supplier_code"]],
                        "type": group["contract"]["type"],
                        "total_amount": group["contract"]["total_amount"],
                        "currency": group["contract"]["currency"],
                        "payment_terms": group["contract"]["payment_terms"],
                        "delivery_terms": group["contract"]["delivery_terms"],
                        "valid_from": group["contract"]["valid_from"],
                        "valid_to": group["contract"]["valid_to"],
                        "doc_id": doc_id,
                    }
                )
                for line in group["contract"]["lines"]:
                    contract_line_rows.append({**line, "id": uuid.uuid4(), "contract_id": contract_id})

            # PO
            po_pdf = render_po(group["po"], supplier_name[group["supplier_code"]])
            rel = _write_pdf(cfg, case_dir / "po.pdf", po_pdf)
            files_written += 1
            file_bytes += len(po_pdf)
            po_doc_id = uuid.uuid4()
            doc_rows.append(
                _doc_row(
                    po_doc_id,
                    "po",
                    rel,
                    po_pdf,
                    template="erp_po_v1",
                    scanned=False,
                    linked_kind="po",
                    linked_id=po_id,
                )
            )
            po_rows.append(
                {
                    "id": po_id,
                    "po_no": group["po"]["po_no"],
                    "contract_id": contract_id,
                    "supplier_id": supplier_id[group["supplier_code"]],
                    "department_id": dept_id["CC-IT-001"],
                    "currency": group["po"]["currency"],
                    "total_amount_tax_incl": group["po"]["total_amount_tax_incl"],
                    "status": group["po"]["status"],
                    "ordered_at": group["po"]["ordered_at"],
                }
            )
            for line in group["po"]["lines"]:
                po_line_rows.append({**line, "id": uuid.uuid4(), "po_id": po_id})

            # 收货
            if receipt_id is not None:
                receipt_rows.append(
                    {
                        "id": receipt_id,
                        "po_id": po_id,
                        "received_at": group["receipts"][0]["received_at"],
                        "doc_id": None,
                    }
                )
                po_line_id = po_line_rows[-1]["id"]
                for receipt in group["receipts"]:
                    for line in receipt["lines"]:
                        receipt_line_rows.append(
                            {
                                "id": uuid.uuid4(),
                                "receipt_id": receipt_id,
                                "po_line_id": po_line_id,
                                "qty": line["qty"],
                                "qc_result": line["qc_result"],
                                "defect_note": None,
                            }
                        )

            # 发票
            for index, invoice in enumerate(group["invoices"]):
                inv_pdf = render_invoice(invoice, group["po"], supplier_name[group["supplier_code"]])
                scanned = group["scanned"]
                if scanned:
                    inv_pdf = degrade_pdf(inv_pdf, rng)
                name = f"invoice_{index + 1}{'.scan' if scanned else ''}.pdf"
                rel = _write_pdf(cfg, case_dir / name, inv_pdf)
                files_written += 1
                file_bytes += len(inv_pdf)
                inv_id = invoice_ids[index]
                doc_id = uuid.uuid4()
                doc_rows.append(
                    _doc_row(
                        doc_id,
                        "invoice",
                        rel,
                        inv_pdf,
                        template="cn_vat_invoice_v1",
                        scanned=scanned,
                        linked_kind="invoice",
                        linked_id=inv_id,
                    )
                )
                invoice_rows.append(
                    {
                        "id": inv_id,
                        "invoice_no": invoice["invoice_no"],
                        "invoice_code": invoice["invoice_code"],
                        "supplier_id": supplier_id[group["supplier_code"]],
                        "invoice_date": invoice["invoice_date"],
                        "amount_tax_excl": invoice["amount_tax_excl"],
                        "tax_amount": invoice["tax_amount"],
                        "amount_tax_incl": invoice["amount_tax_incl"],
                        "doc_id": doc_id,
                        "status": "new",
                        "remark": invoice.get("remark") or None,
                    }
                )
                for line in invoice["lines"]:
                    invoice_line_rows.append(
                        {
                            "id": uuid.uuid4(),
                            "invoice_id": inv_id,
                            "line_no": line["line_no"],
                            "item_desc": line["item_desc"],
                            "spec_bucket": line["spec_bucket"],
                            "qty": line["qty"],
                            "unit_price_tax_excl": line["unit_price_tax_excl"],
                            "tax_rate": line["tax_rate"],
                            "amount_tax_excl": line["amount_tax_excl"],
                            "amount_tax_incl": line["amount_tax_incl"],
                        }
                    )

        # po_line.qty_received 必须与收货行一致（M3 可开票上限 = min(ordered, received)，Spec §7.5）
        received_by_line: dict[uuid.UUID, float] = {}
        for line in receipt_line_rows:
            received_by_line[line["po_line_id"]] = received_by_line.get(line["po_line_id"], 0.0) + float(
                line["qty"]
            )
        for row in po_line_rows:
            row["qty_received"] = received_by_line.get(row["id"], 0.0)
            row["qty_invoiced"] = 0.0

        # doc 先插（供 FK），业务行随后
        await _insert(session, t.Doc, doc_rows)
        await _insert(session, t.Contract, contract_rows)
        await _insert(session, t.ContractLine, contract_line_rows)
        await _insert(session, t.Po, po_rows)
        await _insert(session, t.PoLine, po_line_rows)
        await _insert(session, t.GoodsReceipt, receipt_rows)
        await _insert(session, t.ReceiptLine, receipt_line_rows)
        await _insert(session, t.Invoice, invoice_rows)
        await _insert(session, t.InvoiceLine, invoice_line_rows)

        # 多供应商报价（M2 横向比价）：基于已插入的 PR/供应商生成
        await generate_quotes(session)
        await session.commit()

    # ---------- gold ----------
    _write_json(cfg.gold_dir / "meta.json", dataset["meta"])
    for case in dataset["pr_gold"]:
        _write_json(cfg.gold_dir / "m1" / f"{case['case_id']}.json", case)
    for case in dataset["m3_gold"]:
        _write_json(cfg.gold_dir / "m3" / f"{case['case_id']}.json", case)
    _write_json(
        cfg.gold_dir / "index.json",
        {
            "m1_cases": len(dataset["pr_gold"]),
            "m3_cases": len(dataset["m3_gold"]),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "seed": cfg.seed,
        },
    )

    return {
        "counts": dataset["meta"]["counts"],
        "m3_diff_coverage": dataset["meta"]["m3_diff_coverage"],
        "m3_overall_distribution": dataset["meta"]["m3_overall_distribution"],
        "files_written": files_written,
        "file_mb": round(file_bytes / 1024 / 1024, 2),
        "gold_m1": len(dataset["pr_gold"]),
        "gold_m3": len(dataset["m3_gold"]),
    }


def _doc_row(
    doc_id: uuid.UUID,
    doc_type: str,
    rel_path: str,
    pdf_bytes: bytes,
    *,
    template: str | None,
    scanned: bool,
    linked_kind: str,
    linked_id: uuid.UUID,
) -> dict[str, Any]:
    has_text, ratio = _text_stats(pdf_bytes)
    if scanned:
        has_text, ratio = False, 0.0
    return {
        "id": doc_id,
        "doc_type": doc_type,
        "file_path": rel_path,
        "sha256": _sha256(pdf_bytes),
        "mime": "application/pdf",
        "page_count": 1,
        "has_text_layer": has_text,
        "text_ratio": ratio,
        "template_hit": template if has_text else None,
        "extractor_used": "pdf_native" if has_text else "vlm",
        "linked_kind": linked_kind,
        "linked_id": linked_id,
    }


def _write_pdf(cfg: SeedConfig, path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
