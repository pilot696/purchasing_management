import { describe, expect, it } from "vitest";

import { initialState, isTerminal, reduceEvent } from "./events";
import type { RunEvent, RunUiState } from "./events";

function apply(state: RunUiState, ...events: RunEvent[]): RunUiState {
  return events.reduce((acc, event) => reduceEvent(acc, event), state);
}

describe("reduceEvent", () => {
  it("run.start 将状态置为 running", () => {
    const s = apply(initialState(), { id: 1, type: "run.start", data: {} });
    expect(s.status).toBe("running");
    expect(s.lastSeq).toBe(1);
  });

  it("按 seq 幂等去重（重连回放不重复渲染）", () => {
    const s1 = apply(initialState(), { id: 1, type: "run.start", data: {} });
    const s2 = reduceEvent(s1, { id: 1, type: "run.start", data: {} });
    expect(s2).toBe(s1);
  });

  it("node.start 记录节点开始状态", () => {
    const s = apply(initialState(), { id: 1, type: "node.start", data: { node: "rules_engine" } });
    expect(s.nodes.rules_engine).toEqual({ state: "start" });
  });

  it("node.end 记录耗时并可组成甘特数据", () => {
    const s = apply(
      initialState(),
      { id: 1, type: "node.start", data: { node: "rules_engine" } },
      { id: 2, type: "node.end", data: { node: "rules_engine", duration_ms: 118 } },
    );
    expect(s.nodes.rules_engine).toEqual({ state: "end", durationMs: 118 });
  });

  it("finding 事件按到达顺序累积", () => {
    const s = apply(
      initialState(),
      { id: 1, type: "finding", data: { finding: { type: "required", severity: "high", message: "缺内存" } } },
      { id: 2, type: "finding", data: { finding: { type: "ambiguity", severity: "medium", message: "歧义" } } },
    );
    expect(s.findings.map((f) => f.type)).toEqual(["required", "ambiguity"]);
  });

  it("delta 按 channel 拼接流式文本", () => {
    const s = apply(
      initialState(),
      { id: 1, type: "delta", data: { channel: "summary", text: "该申请" } },
      { id: 2, type: "delta", data: { channel: "summary", text: "缺少规格" } },
      { id: 3, type: "delta", data: { channel: "question", text: "使用人？" } },
    );
    expect(s.narratives.summary).toBe("该申请缺少规格");
    expect(s.narratives.question).toBe("使用人？");
  });

  it("interrupt 事件进入待复核态并保存复核载荷", () => {
    const s = apply(initialState(), {
      id: 5,
      type: "interrupt",
      data: { review_id: "rv_1", kind: "apply_patch", questions: ["使用人？"] },
    });
    expect(s.status).toBe("awaiting_review");
    expect(s.review?.review_id).toBe("rv_1");
    expect(s.review?.questions).toEqual(["使用人？"]);
  });

  it("run.end succeeded 进入终态", () => {
    const s = apply(initialState(), { id: 9, type: "run.end", data: { status: "succeeded" } });
    expect(s.status).toBe("succeeded");
    expect(isTerminal(s.status)).toBe(true);
  });

  it("run.end failed 进入终态", () => {
    const s = apply(initialState(), { id: 9, type: "run.end", data: { status: "failed" } });
    expect(s.status).toBe("failed");
    expect(isTerminal(s.status)).toBe(true);
  });

  it("error 事件记录错误但不改变运行状态", () => {
    const s = apply(initialState(), { id: 3, type: "error", data: { code: "LLM_TIMEOUT", message: "超时" } });
    expect(s.error).toEqual({ code: "LLM_TIMEOUT", message: "超时" });
    expect(s.status).toBe("queued");
  });

  it("heartbeat 只推进 lastSeq，不污染业务状态", () => {
    const before = apply(initialState(), { id: 1, type: "run.start", data: {} });
    const after = reduceEvent(before, { id: 2, type: "heartbeat", data: { ts: 1 } });
    expect(after.lastSeq).toBe(2);
    expect(after.findings).toEqual(before.findings);
    expect(after.status).toBe("running");
  });

  it("未知事件类型不改变业务状态但推进 lastSeq（向前兼容）", () => {
    const before = initialState();
    const after = reduceEvent(before, { id: 7, type: "tool.result" as never, data: {} });
    expect(after.lastSeq).toBe(7);
    expect(after.findings).toEqual([]);
  });
});
