"""SSE 序列化契约（Spec §8.2）：前端 parser 依赖这个格式，必须锁死。"""

from __future__ import annotations

from app.observability.runevents import EVENT_TYPES, Event


def test_event_to_sse_format() -> None:
    event = Event(seq=3, type="node.start", payload={"node": "rules_engine"})
    raw = event.to_sse()
    lines = raw.split("\n")
    assert lines[0] == "id: 3"
    assert lines[1] == "event: node.start"
    assert lines[2].startswith("data: {")
    assert raw.endswith("\n\n")


def test_event_types_cover_spec() -> None:
    # 与 docs/PROJECT_SPEC.md §8.2 的事件表一一对应
    expected = {
        "run.start",
        "node.start",
        "node.end",
        "tool.call",
        "tool.result",
        "finding",
        "delta",
        "interrupt",
        "run.end",
        "error",
        "heartbeat",
    }
    assert expected == set(EVENT_TYPES)
