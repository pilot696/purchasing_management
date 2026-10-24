"""真实票据字段标注 CLI：生成空白模板、校验两份标注、计算双人 Cohen's κ。

用法：
    # 1) 从 DB 导出上传文档的空模板（字段全部 null，绝不预填模型结果）
    python scripts/annotation_agreement.py template --doc-type invoice --out data/annotation/template_invoice.jsonl

    # 2) 两位标注者独立填写后，校验并计算一致性
    python scripts/annotation_agreement.py check \
        --a data/annotation/alice.jsonl --b data/annotation/bob.jsonl \
        --out evals/annotation_agreement_invoice.json \
        --markdown docs/ANNOTATION_REPORT.md

    # 3) 没有入库文档时，用外部清单生成模板
    python scripts/annotation_agreement.py template --manifest data/annotation/manifest.jsonl \
        --out data/annotation/template_invoice.jsonl

说明：脚本不会生成/补全任何标注值；数据目录含真实票据信息时请勿提交到公开仓库。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.loop import ensure_windows_selector_loop  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.evaluation.annotation import (  # noqa: E402
    DOC_FIELDS,
    build_template,
    compare_annotation_files,
    read_jsonl,
    render_markdown,
    write_report,
)
from app.models import tables as t  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _docs_from_manifest(path: Path, doc_type: str | None) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    docs: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        doc_id = row.get("doc_id")
        kind = row.get("doc_type")
        if not doc_id or kind not in DOC_FIELDS:
            raise SystemExit(f"{path}:{index} 需要 doc_id 与合法 doc_type（{sorted(DOC_FIELDS)}）")
        if doc_type and kind != doc_type:
            continue
        docs.append(
            {
                "doc_id": str(doc_id),
                "doc_type": kind,
                "source_path": row.get("source_path"),
                "page_count": row.get("page_count"),
            }
        )
    return docs


async def _docs_from_db(*, doc_type: str | None, include_seed: bool, limit: int | None) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        docs = (await session.scalars(select(t.Doc).order_by(t.Doc.created_at, t.Doc.id))).all()
    result: list[dict[str, Any]] = []
    for doc in docs:
        normalized_path = doc.file_path.replace("\\", "/")
        is_upload = "/uploads/" in normalized_path
        if not include_seed and not is_upload:
            continue
        if doc_type and doc.doc_type != doc_type:
            continue
        result.append(
            {
                "doc_id": str(doc.id),
                "doc_type": doc.doc_type,
                "source_path": doc.file_path,
                "page_count": doc.page_count,
            }
        )
    if limit:
        result = result[:limit]
    return result


def cmd_template(args: argparse.Namespace) -> int:
    if args.manifest:
        docs = _docs_from_manifest(Path(args.manifest), args.doc_type)
    else:
        docs = asyncio.run(
            _docs_from_db(doc_type=args.doc_type, include_seed=args.include_seed, limit=args.limit)
        )
    if not docs:
        mode = "上传文档" if not args.include_seed else "文档"
        raise SystemExit(
            f"没有可用的{mode}：先通过 /documents/upload 上传真实票据，"
            "或使用 --manifest 提供外部文档清单；--include-seed 可导出合成文档做流程演练"
        )
    rows = build_template(docs, annotator=args.annotator)
    out = Path(args.out)
    _write_jsonl(out, rows)
    print(f"TEMPLATE_OK {out} docs={len(rows)}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    report = compare_annotation_files(Path(args.a), Path(args.b))
    if args.out:
        write_report(report, out=Path(args.out), markdown=Path(args.markdown) if args.markdown else None)
    elif args.markdown:
        md_path = Path(args.markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report), end="")
    if not report.get("ok"):
        print("ANNOTATION_CHECK_FAILED", file=sys.stderr)
        return 2
    print("ANNOTATION_CHECK_OK")
    return 0


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实票据字段标注：模板/校验/双人 Cohen's κ")
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="生成空白标注模板（所有字段为 null）")
    template.add_argument("--manifest", type=Path, default=None, help="外部文档清单 JSONL；不传则从 DB 导出上传文档")
    template.add_argument("--doc-type", choices=sorted(DOC_FIELDS), default=None)
    template.add_argument("--include-seed", action="store_true", help="包含 seed 合成文档（仅流程演练，不能当真实 gold）")
    template.add_argument("--limit", type=int, default=None)
    template.add_argument("--annotator", default="", help="预填标注者名字；留空由标注人自行填写")
    template.add_argument("--out", required=True, type=Path)
    template.set_defaults(func=cmd_template)

    check = sub.add_parser("check", help="校验两份独立标注并计算一致率/κ")
    check.add_argument("--a", required=True, type=Path, help="标注者 A 的 JSONL")
    check.add_argument("--b", required=True, type=Path, help="标注者 B 的 JSONL")
    check.add_argument("--out", type=Path, default=None, help="JSON 报告输出路径")
    check.add_argument("--markdown", type=Path, default=None, help="Markdown 报告输出路径")
    check.set_defaults(func=cmd_check)
    return parser.parse_args()


def main() -> int:
    ensure_windows_selector_loop(force=True)
    args = _parse()
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"ANNOTATION_ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
