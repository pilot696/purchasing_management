"""物料归一化：别名 + 规格抽取 → `spec_bucket`（Spec §5.4.4，M1/M2/M3 共用）。

设计原则：**确定性优先**——能由规则抽出的规格绝不做语义匹配；`spec_bucket` 是三个模块的公共语义坐标。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALIASES_PATH = PROJECT_ROOT / "data" / "aliases.yaml"

_SPEC_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "memory_gb": (
        re.compile(r"(\d{1,3})\s*(?:GB|G)\s*(?:内存|RAM|内存条)", re.IGNORECASE),
        re.compile(r"(?:内存|RAM)[:：]?\s*(\d{1,3})\s*(?:GB|G)?", re.IGNORECASE),
        re.compile(r"(\d{1,3})\s*G\s*/\s*\d{2,4}\s*G", re.IGNORECASE),
    ),
    "storage_gb": (
        re.compile(r"(\d{1,4})\s*(?:GB|G)\s*(?:固态|SSD|存储|硬盘)", re.IGNORECASE),
        re.compile(r"(?:存储|硬盘|SSD)[:：]?\s*(\d{1,4})\s*(?:GB|G)?", re.IGNORECASE),
        re.compile(r"\d{1,3}\s*G\s*/\s*(\d{2,4})\s*G", re.IGNORECASE),
        re.compile(r"(\d{1,4})\s*(?:GB|G)\b", re.IGNORECASE),
    ),
    "size_inch": (
        re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:英寸|寸|\")"),
        re.compile(r"\b(\d{1,2})\s*inch\b", re.IGNORECASE),
    ),
    "seats": (
        re.compile(r"(\d{1,4})\s*(?:个)?\s*(?:席位|用户数|账号数|seats?)", re.IGNORECASE),
        re.compile(r"(\d{1,4})\s*人版", re.IGNORECASE),
    ),
    "period_months": (
        re.compile(r"(\d{1,2})\s*(?:个月|月)\s*(?:订阅|服务|授权)?", re.IGNORECASE),
        re.compile(r"(\d{1,2})\s*months?", re.IGNORECASE),
    ),
    "duration_days": (
        re.compile(r"(\d{1,3})\s*(?:天|人天|工作日)"),
    ),
}

FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "laptop": ("笔记本", "笔记本电脑", "便携", "办公电脑", "电脑", "laptop", "macbook", "thinkpad", "matebook"),
    "server": ("服务器", "机架", "server"),
    "monitor": ("显示器", "屏幕", "display", "monitor"),
    "peripheral": ("鼠标", "键盘", "扩展坞", "耳机", "dock"),
    "saas": ("saas", "订阅", "席位", "账号", "授权", "license", "软件", "云服务"),
    "admin": ("复印纸", "耗材", "文具", "办公用品", "家具", "椅", "硒鼓", "白板"),
    "outsource": ("外包", "会务", "拍摄", "代运营", "调研", "设计", "活动", "服务"),
}


@dataclass(slots=True)
class NormalizedItem:
    raw_name: str
    spec_bucket: str
    family: str | None = None
    brand: str | None = None
    slots: dict[str, str | int] = field(default_factory=dict)
    method: str = "rule"  # rule | alias | none
    confidence: float = 0.5


def _load_alias_config() -> dict:
    if ALIASES_PATH.exists():
        return yaml.safe_load(ALIASES_PATH.read_text(encoding="utf-8")) or {}
    return {}


_ALIAS_CONFIG = _load_alias_config()


def _brand_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for canonical, aliases in (_ALIAS_CONFIG.get("brands") or {}).items():
        index[str(canonical).lower()] = str(canonical)
        for alias in aliases:
            index[str(alias).lower().replace(" ", "")] = str(canonical)
    return index


_BRAND_INDEX = _brand_index()


def detect_brand(text: str) -> str | None:
    lowered = text.lower().replace(" ", "")
    for alias, canonical in sorted(_BRAND_INDEX.items(), key=lambda item: -len(item[0])):
        if alias and alias in lowered:
            return canonical
    return None


def detect_family(text: str) -> str | None:
    lowered = text.lower()
    for family, keywords in FAMILY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return family
    return None


_MEM_STORAGE_PAIR = re.compile(r"(\d{1,3})\s*(?:GB|G)\s*[/\s]\s*(\d{2,4})\s*(?:GB|G)", re.IGNORECASE)
_STANDALONE_G = re.compile(r"(?<![\d.])(\d{2,4})\s*G\b", re.IGNORECASE)


def extract_slots(text: str) -> dict[str, int]:
    slots: dict[str, int] = {}
    # 先吃 "16G 512G" / "16G/512G" 这种内存+存储连写（单独正则无法区分两者）
    pair = _MEM_STORAGE_PAIR.search(text)
    if pair:
        slots["memory_gb"] = int(pair.group(1))
        slots["storage_gb"] = int(pair.group(2))
    for slot, patterns in _SPEC_PATTERNS.items():
        if slot in slots:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                value = int(float(match.group(1)))
                if slot in {"memory_gb", "storage_gb"} and (value <= 0 or value > 8192):
                    continue
                slots[slot] = value
                break
    # 兜底：孤立的 "NNNG"（如 "512G"）：≥128 视为存储，否则视为内存
    if "memory_gb" not in slots or "storage_gb" not in slots:
        for standalone in _STANDALONE_G.finditer(text):
            value = int(standalone.group(1))
            if value >= 128 and "storage_gb" not in slots:
                slots["storage_gb"] = value
            elif value < 128 and "memory_gb" not in slots:
                slots["memory_gb"] = value
    return slots


def _bucket(family: str | None, slots: dict[str, int]) -> str:
    if family in {"laptop", "server"}:
        return f"{family}/{slots.get('memory_gb', '*')}g/{slots.get('storage_gb', '*')}g"
    if family == "monitor":
        return f"monitor/{slots.get('size_inch', '*')}in"
    if family == "peripheral":
        return "peripheral/generic"
    if family == "saas":
        return f"saas/{slots.get('seats', '*')}seats/{slots.get('period_months', '*')}m"
    if family == "outsource":
        return f"outsource/{slots.get('duration_days', '*')}d"
    if family == "admin":
        return "admin/generic"
    return "unknown/unknown"


def normalize(
    item_name_raw: str,
    *,
    spec_text: str | None = None,
    category_code: str | None = None,
    hints: dict[str, int] | None = None,
) -> NormalizedItem:
    """把任意写法（"MacBook Pro 14 M3 16G 512G" / "苹果笔记本电脑 14寸 16GB内存 512GB固态"）落到同一 bucket。"""
    text = " ".join(part for part in (item_name_raw, spec_text or "") if part)
    family = detect_family(text)
    if family is None and category_code:
        family = {
            "IT_HW": "laptop",
            "SAAS": "saas",
            "ADMIN": "admin",
            "MKT_OUTSOURCE": "outsource",
        }.get(category_code)
    brand = detect_brand(text)
    slots = extract_slots(text)
    for key, value in (hints or {}).items():
        if value:
            slots.setdefault(key, int(value))
    bucket = _bucket(family, slots)
    method = "rule" if family and slots else ("alias" if brand or family else "none")
    confidence = 0.95 if family and len(slots) >= 1 else 0.6 if family else 0.3
    return NormalizedItem(
        raw_name=item_name_raw,
        family=family,
        brand=brand,
        slots={key: str(value) for key, value in slots.items()},
        spec_bucket=bucket,
        method=method,
        confidence=confidence,
    )
