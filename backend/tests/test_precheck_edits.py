"""HITL edits 规整的纯函数单测（不依赖 DB）。"""

from __future__ import annotations

from app.agents.precheck import sanitize_reviewer_edits


def test_sanitize_keeps_valid_patches() -> None:
    edits = [
        {"path": "/items/0/spec/memory_gb", "value": 32, "source": "reviewer"},
        {"path": "/items/1/qty", "value": 5},
    ]
    cleaned = sanitize_reviewer_edits(edits, item_count=2)
    assert [patch["value"] for patch in cleaned] == [32, 5]
    assert all(patch["op"] == "replace" for patch in cleaned)
    assert cleaned[0]["source"] == "reviewer"
    assert cleaned[1]["source"] == "reviewer"


def test_sanitize_drops_invalid_edits() -> None:
    edits = [
        {"path": "/items/9/spec/memory_gb", "value": 32},  # 越界
        {"path": "/other/0/spec/memory_gb", "value": 32},  # 非 items
        {"path": "/items/0/spec/memory_gb", "value": None},  # 空值
        {"path": "/items/0/spec", "value": 1},  # 路径层级不足
        "not-a-dict",  # type: ignore[list-item]
    ]
    assert sanitize_reviewer_edits(edits, item_count=2) == []  # type: ignore[arg-type]


def test_sanitize_empty_and_limit() -> None:
    assert sanitize_reviewer_edits([], item_count=3) == []
    many = [{"path": f"/items/{index % 2}/spec/x{index}", "value": index} for index in range(200)]
    assert len(sanitize_reviewer_edits(many, item_count=2)) == 100
