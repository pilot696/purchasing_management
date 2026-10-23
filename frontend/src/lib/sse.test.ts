import { describe, expect, it } from "vitest";

import { parseSseChunk } from "./sse";

describe("parseSseChunk", () => {
  it("解析单条事件", () => {
    const raw = 'id: 1\nevent: run.start\ndata: {"module":"m1"}\n\n';
    const { events, rest } = parseSseChunk(raw);
    expect(rest).toBe("");
    expect(events).toEqual([{ id: 1, type: "run.start", data: { module: "m1" } }]);
  });

  it("一次解析多条事件", () => {
    const raw =
      'id: 1\nevent: node.start\ndata: {"node":"rules"}\n\n' +
      'id: 2\nevent: node.end\ndata: {"node":"rules","duration_ms":12}\n\n';
    const { events } = parseSseChunk(raw);
    expect(events).toHaveLength(2);
    expect(events[1].data.duration_ms).toBe(12);
  });

  it("半包数据留在 rest 里等待下一块", () => {
    const raw = 'id: 1\nevent: node.start\ndata: {"node":"rul';
    const { events, rest } = parseSseChunk(raw);
    expect(events).toEqual([]);
    expect(rest).toContain("rul");
  });

  it("忽略 retry 行与注释行", () => {
    const raw = ': keep-alive\nretry: 3000\n\nid: 3\nevent: heartbeat\ndata: {"ts":1}\n\n';
    const { events } = parseSseChunk(raw);
    expect(events).toEqual([{ id: 3, type: "heartbeat", data: { ts: 1 } }]);
  });

  it("兼容 CRLF 换行", () => {
    const raw = 'id: 2\r\nevent: run.end\r\ndata: {"status":"succeeded"}\r\n\r\n';
    const { events } = parseSseChunk(raw);
    expect(events[0].data.status).toBe("succeeded");
  });

  it("非法 JSON 事件被丢弃而不是抛错", () => {
    const raw = "id: 4\nevent: finding\ndata: {oops\n\n";
    const { events } = parseSseChunk(raw);
    expect(events).toEqual([]);
  });
});
