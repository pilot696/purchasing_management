"""真实票据字段标注：模板、校验与双人一致性（Cohen's κ）。

设计原则：
- 这里只提供**流程与计算**，不内置任何真实票据的 gold 值，也不生成"看起来像真数据"的标注；
- 标注文件是一行一个文档的 JSONL，两位标注人各自独立填写，脚本只读取文件；
- 金额/日期/税率先归一化再比较，避免格式差异被误判为分歧；
- 缺失值用 ``null`` 表示，并在报告中单列 both_missing / one_missing，不把缺失当成"抽取得对"。

CLI 见 ``scripts/annotation_agreement.py``；流程说明见 ``docs/ANNOTATION_GUIDE.md``。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

# 真实票据最小字段集：只保留能人工核对、且对业务判断有影响的字段。
DOC_FIELDS: dict[str, tuple[str, ...]] = {
    "invoice": (
        "invoice_no",
        "invoice_date",
        "seller_name",
        "seller_tax_id",
        "amount_tax_excl",
        "tax_rate",
        "amount_tax_incl",
        "currency",
    ),
    "po": ("po_no", "po_date", "supplier_name", "amount_tax_incl", "currency"),
    "contract": (
        "contract_no",
        "contract_date",
        "supplier_name",
        "amount_tax_incl",
        "currency",
        "lead_time_days",
    ),
}

FIELD_TYPES: dict[str, str] = {
    "invoice_no": "str",
    "invoice_date": "date",
    "seller_name": "str",
    "seller_tax_id": "str",
    "amount_tax_excl": "amount",
    "tax_rate": "rate",
    "amount_tax_incl": "amount",
    "currency": "str",
    "po_no": "str",
    "po_date": "date",
    "supplier_name": "str",
    "contract_no": "str",
    "contract_date": "date",
    "lead_time_days": "int",
}

MISSING_LABEL = "<missing>"
_AMOUNT_CHARS = re.compile(r"[¥￥,，\s]")
_DATE_PATTERNS = (
    re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$"),
    re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$"),
)


def fields_for(doc_type: str) -> tuple[str, ...]:
    try:
        return DOC_FIELDS[doc_type]
    except KeyError as exc:
        raise ValueError(f"未知文档类型: {doc_type}") from exc


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text or text.lower() in {"null", "none", "n/a", "na", "-", "—", "?"}:
        return None
    return text


def _normalize_date(value: Any) -> str | None:
    text = _clean_str(value)
    if text is None:
        return None
    for pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
            except ValueError as exc:
                raise ValueError(f"非法日期: {value!r}") from exc
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"无法识别的日期: {value!r}") from exc


def _normalize_amount(value: Any) -> str | None:
    text = _clean_str(value)
    if text is None:
        return None
    cleaned = _AMOUNT_CHARS.sub("", text)
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"非法金额: {value!r}") from exc
    return format(amount.quantize(Decimal("0.01")), "f")


def _normalize_rate(value: Any) -> str | None:
    text = _clean_str(value)
    if text is None:
        return None
    is_percent = text.endswith("%")
    cleaned = _AMOUNT_CHARS.sub("", text[:-1] if is_percent else text)
    try:
        rate = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"非法税率: {value!r}") from exc
    if is_percent or rate > 1:
        rate = rate / Decimal(100)
    return format(rate.quantize(Decimal("0.0001")), "f")


def _normalize_int(value: Any) -> str | None:
    text = _clean_str(value)
    if text is None:
        return None
    try:
        return str(int(Decimal(text)))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"非法整数: {value!r}") from exc


def normalize_value(field: str, value: Any) -> str | None:
    """把人工填写值归一化为可比较的字符串；None 表示明确缺失。"""
    field_type = FIELD_TYPES.get(field, "str")
    if field_type == "date":
        return _normalize_date(value)
    if field_type == "amount":
        return _normalize_amount(value)
    if field_type == "rate":
        return _normalize_rate(value)
    if field_type == "int":
        return _normalize_int(value)
    text = _clean_str(value)
    return text.casefold() if text else None


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        raise ValueError(f"文件不存在: {file_path}")
    rows: list[dict[str, Any]] = []
    # Windows 编辑器/PowerShell 常写 UTF-8 BOM；读取时兼容，避免把 BOM 当成坏 JSON
    for line_no, line in enumerate(file_path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{file_path}:{line_no} 不是合法 JSON: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{file_path}:{line_no} 必须是 JSON 对象")
        rows.append(row)
    return rows


def validate_annotations(rows: Iterable[dict[str, Any]], *, source: str = "<rows>") -> dict[str, Any]:
    """校验标注文件结构/字段完整性/值可归一化；不修改输入。"""
    errors: list[str] = []
    warnings: list[str] = []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        where = f"{source}:{index}"
        doc_id = row.get("doc_id")
        doc_type = row.get("doc_type")
        annotator = row.get("annotator")
        values = row.get("values")
        if not doc_id or not isinstance(doc_id, str):
            errors.append(f"{where} 缺少 doc_id")
            continue
        if doc_id in seen:
            errors.append(f"{where} doc_id 重复: {doc_id}")
            continue
        seen.add(doc_id)
        if doc_type not in DOC_FIELDS:
            errors.append(f"{where} doc_type 必须是 {sorted(DOC_FIELDS)}")
            continue
        if not annotator or not isinstance(annotator, str):
            errors.append(f"{where} 缺少 annotator")
            continue
        if not isinstance(values, dict):
            errors.append(f"{where} values 必须是对象")
            continue
        expected_fields = set(fields_for(doc_type))
        unknown = set(values) - expected_fields
        if unknown:
            errors.append(f"{where} 未知字段: {sorted(unknown)}")
            continue
        missing = expected_fields - set(values)
        if missing:
            errors.append(f"{where} 缺少字段: {sorted(missing)}（无法识别请显式填 null）")
            continue
        normalized_values: dict[str, str | None] = {}
        for field in fields_for(doc_type):
            try:
                normalized_values[field] = normalize_value(field, values[field])
            except ValueError as exc:
                errors.append(f"{where} 字段 {field}: {exc}")
        normalized.append(
            {
                "doc_id": doc_id,
                "doc_type": doc_type,
                "annotator": annotator,
                "values": normalized_values,
                "notes": row.get("notes"),
            }
        )
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "documents": normalized,
        "count": len(normalized),
    }


def cohen_kappa(labels_a: Iterable[str], labels_b: Iterable[str]) -> dict[str, Any]:
    """两标注者、多分类的 Cohen's κ；返回 kappa 与观察/期望一致率。"""
    a = list(labels_a)
    b = list(labels_b)
    if len(a) != len(b):
        raise ValueError("两组标签长度不一致")
    n = len(a)
    if n == 0:
        return {"n": 0, "observed_agreement": None, "expected_agreement": None, "kappa": None}
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    count_a = Counter(a)
    count_b = Counter(b)
    expected = sum((count_a[label] / n) * (count_b[label] / n) for label in set(a) | set(b))
    if expected >= 1.0:
        # 双方都只用一个类别：完全一致时 κ 无定义，按 1.0 报告并在 note 中说明
        kappa = 1.0 if observed >= 1.0 else 0.0
        note = "双方标签无变异，κ 无定义；按完全一致=1 处理"
    else:
        kappa = (observed - expected) / (1 - expected)
        note = ""
    return {
        "n": n,
        "observed_agreement": round(observed, 6),
        "expected_agreement": round(expected, 6),
        "kappa": round(kappa, 6),
        "note": note,
    }


def compare_annotation_files(
    path_a: str | Path,
    path_b: str | Path,
) -> dict[str, Any]:
    """校验两份独立标注并输出字段级一致率 + κ（不写任何标注结果）。"""
    result_a = validate_annotations(read_jsonl(path_a), source=str(path_a))
    result_b = validate_annotations(read_jsonl(path_b), source=str(path_b))
    errors = list(result_a["errors"]) + list(result_b["errors"])
    if errors:
        return {
            "ok": False,
            "status": "invalid",
            "errors": errors,
            "files": {"a": str(path_a), "b": str(path_b)},
        }

    docs_a = {row["doc_id"]: row for row in result_a["documents"]}
    docs_b = {row["doc_id"]: row for row in result_b["documents"]}
    annotators = {row["annotator"] for row in docs_a.values()} | {row["annotator"] for row in docs_b.values()}
    if len(annotators) < 2:
        errors.append("两份文件必须来自不同标注者（annotator 至少两个不同值）")
    common = sorted(set(docs_a) & set(docs_b))
    if not common:
        errors.append("两份标注没有共同 doc_id，无法计算一致性")
    mismatched_types = [
        doc_id
        for doc_id in common
        if docs_a[doc_id]["doc_type"] != docs_b[doc_id]["doc_type"]
    ]
    if mismatched_types:
        errors.append(f"同一 doc_id 的 doc_type 不一致: {mismatched_types[:5]}")
    if errors:
        return {
            "ok": False,
            "status": "invalid",
            "errors": errors,
            "files": {"a": str(path_a), "b": str(path_b)},
        }

    field_pairs: dict[str, list[tuple[str, str]]] = {}
    both_missing: dict[str, int] = {}
    one_missing: dict[str, int] = {}
    for doc_id in common:
        row_a = docs_a[doc_id]
        row_b = docs_b[doc_id]
        for field in fields_for(row_a["doc_type"]):
            value_a = row_a["values"].get(field)
            value_b = row_b["values"].get(field)
            if value_a is None and value_b is None:
                both_missing[field] = both_missing.get(field, 0) + 1
            elif value_a is None or value_b is None:
                one_missing[field] = one_missing.get(field, 0) + 1
            field_pairs.setdefault(field, []).append(
                (value_a if value_a is not None else MISSING_LABEL, value_b if value_b is not None else MISSING_LABEL)
            )

    per_field: dict[str, dict[str, Any]] = {}
    all_labels_a: list[str] = []
    all_labels_b: list[str] = []
    for field, pairs in sorted(field_pairs.items()):
        labels_a = [pair[0] for pair in pairs]
        labels_b = [pair[1] for pair in pairs]
        all_labels_a.extend(labels_a)
        all_labels_b.extend(labels_b)
        stats = cohen_kappa(labels_a, labels_b)
        per_field[field] = {
            **stats,
            "both_missing": both_missing.get(field, 0),
            "one_missing": one_missing.get(field, 0),
            "agreement_count": sum(1 for a, b in pairs if a == b),
        }

    micro = cohen_kappa(all_labels_a, all_labels_b)
    kappas = [stats["kappa"] for stats in per_field.values() if stats["kappa"] is not None]
    agreements = [stats["observed_agreement"] for stats in per_field.values() if stats["observed_agreement"] is not None]
    return {
        "ok": True,
        "status": "complete",
        "files": {"a": str(path_a), "b": str(path_b)},
        "annotators": sorted(annotators),
        "documents": {
            "common": len(common),
            "only_a": sorted(set(docs_a) - set(docs_b)),
            "only_b": sorted(set(docs_b) - set(docs_a)),
            "doc_types": sorted({docs_a[doc_id]["doc_type"] for doc_id in common}),
        },
        "fields": per_field,
        "overall": {
            "slot_agreement": micro["observed_agreement"],
            "slot_kappa": micro["kappa"],
            "macro_field_agreement": round(sum(agreements) / len(agreements), 6) if agreements else None,
            "macro_field_kappa": round(sum(kappas) / len(kappas), 6) if kappas else None,
            "fields": len(per_field),
        },
        "note": "κ 基于两位标注者的独立标注；同源/单人/事后对稿的标注不能当作 gold。",
    }


def build_template(docs: Iterable[dict[str, Any]], *, annotator: str = "") -> list[dict[str, Any]]:
    """生成空模板：字段全部为 null，等待人工填写；绝不预填模型抽取结果。"""
    rows: list[dict[str, Any]] = []
    for doc in docs:
        doc_type = doc["doc_type"]
        rows.append(
            {
                "doc_id": doc["doc_id"],
                "doc_type": doc_type,
                "annotator": annotator,
                "source_path": doc.get("source_path"),
                "page_count": doc.get("page_count"),
                "values": {field: None for field in fields_for(doc_type)},
                "notes": "",
            }
        )
    return rows


def render_markdown(report: dict[str, Any]) -> str:
    """把一致性报告渲染成可贴进简历/评测文档的 Markdown。"""
    lines = [
        "# 真实票据字段标注一致性报告",
        "",
        "> 数据来源：两位标注者独立填写的 JSONL；本报告未做任何自动补标。",
        "",
        f"- 状态: **{report.get('status', 'unknown')}**",
    ]
    if not report.get("ok"):
        lines.extend(["", "## 校验错误", ""])
        lines.extend(f"- {err}" for err in report.get("errors", []))
        return "\n".join(lines) + "\n"
    documents = report["documents"]
    overall = report["overall"]
    lines.extend(
        [
            f"- 文件 A: `{report['files']['a']}`",
            f"- 文件 B: `{report['files']['b']}`",
            f"- 标注者: {', '.join(report['annotators'])}",
            f"- 共同文档: {documents['common']}，仅 A: {len(documents['only_a'])}，仅 B: {len(documents['only_b'])}",
            f"- 字段级宏平均一致率: {overall['macro_field_agreement']}",
            f"- 字段级宏平均 κ: {overall['macro_field_kappa']}",
            f"- 槽位级一致率 / κ: {overall['slot_agreement']} / {overall['slot_kappa']}",
            "",
            "| 字段 | n | 一致数 | 一致率 | κ | 双方缺失 | 单方缺失 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for field, stats in report["fields"].items():
        lines.append(
            f"| {field} | {stats['n']} | {stats['agreement_count']} | "
            f"{stats['observed_agreement']} | {stats['kappa']} | {stats['both_missing']} | {stats['one_missing']} |"
        )
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 统计只代表**标注者之间**的一致性，不代表模型准确率；要计算抽取准确率，还需第三位裁决者或共识标签。",
            "- 样本量小（<30 份/字段）时 κ 置信区间很宽，不应单独作为简历卖点。",
            "- 真实票据含企业信息，标注文件与原始 PDF 不得提交到公开仓库。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], *, out: str | Path, markdown: str | Path | None = None) -> None:
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if markdown:
        md_path = Path(markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(report), encoding="utf-8")
