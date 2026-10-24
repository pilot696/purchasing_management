"""抽取基线评测：遍历已入库文档，跑 DocumentRouter + pdf_native，统计字段抽取准确率与延迟。

产出一份可直接写进简历/报告的基线：`evals/extraction_baseline.json`。

用法（需 DB + 已 seed）：
    python scripts/extract_eval.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.loop import ensure_windows_selector_loop  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.extraction.service import extract_document  # noqa: E402
from app.models import tables as t  # noqa: E402
from app.schemas.extraction import InvoiceExtract  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = PROJECT_ROOT / "evals" / "extraction_baseline.json"


def _pct(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


async def run(*, vision=None, doc_limit: int | None = None, include_uploads: bool = False) -> dict:  # noqa: ANN001
    async with get_session_factory()() as session:
        docs = (await session.scalars(select(t.Doc).order_by(t.Doc.doc_type, t.Doc.id))).all()
        invoices = {row.id: row for row in (await session.scalars(select(t.Invoice))).all()}
        suppliers = {row.id: row for row in (await session.scalars(select(t.Supplier))).all()}

    # 基线默认只看 seed 数据；上传文档（含测试伪 PDF）不混入，保证可复现
    if not include_uploads:
        docs = [doc for doc in docs if "/uploads/" not in doc.file_path.replace("\\", "/")]
    if doc_limit:
        docs = docs[:doc_limit]
    if not docs:
        raise SystemExit("没有文档：先运行 scripts/seed.py --seed 42")

    route_counts: dict[str, int] = {}
    extractor_counts: dict[str, int] = {}
    per_doc_type: dict[str, dict[str, list[float]]] = {}
    latencies: list[float] = []
    invoice_fields: dict[str, list[bool]] = {"invoice_no": [], "amount_tax_incl": [], "seller_name": []}
    scanned_ok = 0
    failures: list[dict] = []

    for doc in docs:
        pdf_bytes = (PROJECT_ROOT / doc.file_path).read_bytes()
        started = time.perf_counter()
        result = await extract_document(pdf_bytes, doc.doc_type, vision)  # type: ignore[arg-type]
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        route_counts[result.route.kind] = route_counts.get(result.route.kind, 0) + 1
        extractor_counts[result.extractor] = extractor_counts.get(result.extractor, 0) + 1
        bucket = per_doc_type.setdefault(doc.doc_type, {"ok": [], "needs_review": [], "failed": []})
        if result.extract is None:
            bucket["failed"].append(1.0)
            if result.route.kind == "scanned" and result.needs_review_fields == ["vlm_required"]:
                scanned_ok += 1
            failures.append({"doc_id": str(doc.id), "doc_type": doc.doc_type, "code": "no_extract"})
            continue
        has_high = any(issue.severity == "high" for issue in result.issues)
        bucket["needs_review" if has_high else "ok"].append(1.0)
        if has_high and len(failures) < 5:
            failures.append(
                {
                    "doc_id": str(doc.id),
                    "doc_type": doc.doc_type,
                    "code": ",".join(sorted({issue.code for issue in result.issues if issue.severity == "high"})),
                }
            )

        if doc.doc_type == "invoice":
            invoice = invoices.get(doc.linked_id) if doc.linked_id else None
            if invoice is None:
                failures.append({"doc_id": str(doc.id), "doc_type": "invoice", "code": "linked_invoice_missing"})
                continue
            extract = InvoiceExtract.model_validate(result.extract)
            invoice_fields["invoice_no"].append(extract.invoice_no == invoice.invoice_no)
            invoice_fields["amount_tax_incl"].append(
                extract.amount_tax_incl is not None
                and abs(float(extract.amount_tax_incl) - float(invoice.amount_tax_incl)) <= 0.01
            )
            supplier = suppliers.get(invoice.supplier_id) if invoice.supplier_id else None
            invoice_fields["seller_name"].append(
                supplier is not None and extract.seller_name == supplier.name
            )

    def ratio(values: list[bool]) -> float:
        return round(sum(1 for v in values if v) / len(values), 4) if values else 0.0

    latencies_sorted = sorted(latencies)
    summary = {
        "docs": len(docs),
        "vision_mode": "llm-vision" if vision is not None else "offline (vlm_required)",
        "route_distribution": route_counts,
        "extractor_distribution": extractor_counts,
        "scanned_needs_vlm": scanned_ok,
        "per_doc_type": {
            doc_type: {
                "ok": len(buckets["ok"]),
                "needs_review": len(buckets["needs_review"]),
                "failed": len(buckets["failed"]),
            }
            for doc_type, buckets in per_doc_type.items()
        },
        "invoice_field_accuracy": {field: ratio(values) for field, values in invoice_fields.items()},
        "invoice_field_n": {field: len(values) for field, values in invoice_fields.items()},
        "latency_ms": {
            "p50": round(latencies_sorted[len(latencies_sorted) // 2], 2),
            "p95": round(latencies_sorted[max(0, int(len(latencies_sorted) * 0.95) - 1)], 2),
            "mean": round(_pct(latencies), 2),
        },
        "failures_sample": failures[:5],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return summary


def _clear_prompt_cache() -> None:
    import shutil

    from app.core.config import get_settings

    backend_dir = Path(__file__).resolve().parents[1] / "backend"
    cache_dir = Path(get_settings().prompt_cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = backend_dir / cache_dir
    shutil.rmtree(cache_dir, ignore_errors=True)
    print(f"[eval] cleared prompt cache: {cache_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="抽取基线评测（默认不跑 VLM；--vision 走真实视觉模型）")
    parser.add_argument("--vision", action="store_true", help="扫描件走真实 VLM（需配置 DASHSCOPE_API_KEY/VISION_MODEL）")
    parser.add_argument("--limit-docs", type=int, default=None, help="只评前 N 份文档（试跑控成本）")
    parser.add_argument("--include-uploads", action="store_true", help="把用户上传文档也纳入评测（默认只评 seed 文档）")
    parser.add_argument("--clear-cache", action="store_true", help="评测前清空 prompt 缓存")
    parser.add_argument("--out", type=Path, default=None, help="输出 JSON 路径")
    args = parser.parse_args()

    ensure_windows_selector_loop(force=True)
    if args.clear_cache:
        _clear_prompt_cache()

    vision = None
    if args.vision:
        from app.llm.router import get_router
        from app.extraction.vlm import LLMVisionExtractor

        try:
            router = get_router()
            role = router.config.role("vision")
            provider = router.config.providers[role.provider]
            if not provider.base_url or not provider.api_key:
                raise SystemExit(
                    "VLM 未配置：请在 backend/.env 填 DASHSCOPE_BASE_URL / DASHSCOPE_API_KEY，"
                    "并在 .env 设 VISION_MODEL（OpenAI 兼容的视觉模型名）"
                )
            vision = LLMVisionExtractor(router, role="vision")
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"VLM 初始化失败（检查 DASHSCOPE_BASE_URL/API_KEY/VISION_MODEL）：{exc}") from exc

    ensure_windows_selector_loop(force=True)
    summary = asyncio.run(
        run(vision=vision, doc_limit=args.limit_docs, include_uploads=args.include_uploads)
    )
    out_path = args.out or (
        PROJECT_ROOT / "evals" / ("extraction_baseline_vision.json" if args.vision else "extraction_baseline.json")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"written={out_path}")
    print("EXTRACT_EVAL_OK")


if __name__ == "__main__":
    main()
