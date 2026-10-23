import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { getHealth, resumeRun, startRun } from "../lib/api";
import type { Module } from "../lib/api";
import { isTerminal } from "../lib/events";
import { useRunStream } from "../lib/useRunStream";

const MODULES: Array<{ id: Module; name: string; desc: string }> = [
  {
    id: "m1",
    name: "M1 采购申请预检",
    desc: "规则引擎 + 混合检索 + 语义消歧：询价前拦截缺字段、规格歧义、超预算与数量异常。",
  },
  {
    id: "m2",
    name: "M2 供应商准入评估",
    desc: "五维确定性评分 + 6 条硬闸门 + LLM 复核者综述，输出准入/有条件准入/淘汰。",
  },
  {
    id: "m3",
    name: "M3 三单匹配核销",
    desc: "合同/PO/发票多模态抽取 + 三级对齐（精确/规格桶/语义）+ 容差与分批核销。",
  },
];

export default function RunCenter() {
  const navigate = useNavigate();
  const [runId, setRunId] = useState<string | null>(null);
  const run = useRunStream(runId);
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, retry: false });
  const start = useMutation({
    mutationFn: (module: Module) => startRun(module, { demo: true }),
    onSuccess: (response) => setRunId(response.run_id),
  });
  const resume = useMutation({
    mutationFn: () => {
      if (!runId || !run.review) throw new Error("当前没有可复核的任务");
      return resumeRun(runId, { review_id: run.review.review_id, decision: "accept" });
    },
  });

  const dbDown = health.data?.db === false;
  const busy = start.isPending || (run.status === "running" && run.lastSeq > 0);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Run 中心</h1>
        <p className="mt-1 text-[13px] text-[var(--text-secondary)]">
          P0 骨架：演示运行会真实走「建 run → 节点事件 → HITL 中断 → resume → finalize」，
          事件流可断线续传。真实节点在 W1~W3 替换进来（Spec §11）。
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {MODULES.map((module) => (
          <section
            key={module.id}
            className="flex flex-col justify-between rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4 shadow-sm"
          >
            <div>
              <h2 className="text-sm font-semibold">{module.name}</h2>
              <p className="mt-2 text-[13px] leading-6 text-[var(--text-secondary)]">{module.desc}</p>
            </div>
            <button
              type="button"
              disabled={
                module.id === "m1" || module.id === "m3" ? dbDown : dbDown || busy || start.isPending
              }
              onClick={() =>
                module.id === "m3"
                  ? navigate("/matching")
                  : module.id === "m1"
                    ? navigate("/precheck")
                    : start.mutate(module.id)
              }
              className="mt-4 rounded-md bg-[var(--brand-primary)] px-3 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {module.id === "m3"
                ? "去 M3 页面启动"
                : module.id === "m1"
                  ? "去 M1 页面启动"
                  : start.isPending
                    ? "创建中…"
                    : "新建演示运行"}
            </button>
          </section>
        ))}
      </div>

      {start.isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-[13px] text-[var(--semantic-danger)]">
          创建运行失败：{String(start.error)}
        </div>
      )}

      {runId && (
        <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
          <header className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <h2 className="text-sm font-semibold">运行 {runId.slice(0, 8)}</h2>
              <span className={`status-chip status-${run.status}`}>{run.status}</span>
              <span className="text-[12px] text-[var(--text-muted)]">lastSeq={run.lastSeq}</span>
            </div>
            <button
              type="button"
              onClick={() => setRunId(null)}
              className="text-[13px] text-[var(--text-secondary)] underline-offset-2 hover:underline"
            >
              关闭
            </button>
          </header>

          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="rounded-lg border border-[var(--border-default)] p-3">
              <h3 className="text-[13px] font-medium">节点时间线</h3>
              <ul className="mt-2 space-y-1 text-[12px] text-[var(--text-secondary)]">
                {Object.entries(run.nodes).length === 0 && <li>等待第一个节点…</li>}
                {Object.entries(run.nodes).map(([node, info]) => (
                  <li key={node}>
                    {info.state === "start" ? "◉" : "✓"} {node}
                    {info.durationMs !== undefined ? ` · ${info.durationMs}ms` : ""}
                  </li>
                ))}
              </ul>
            </div>

            <div className="rounded-lg border border-[var(--border-default)] p-3">
              <h3 className="text-[13px] font-medium">结构化结论（增量）</h3>
              <p className="mt-2 text-[12px] text-[var(--text-secondary)]">
                findings: {run.findings.length} 条
              </p>
              <ul className="mt-2 space-y-1 text-[12px]">
                {run.findings.map((finding, index) => (
                  <li key={`${finding.type}-${index}`}>
                    <span className="mr-1 font-medium">[{finding.severity}]</span>
                    {finding.message}
                  </li>
                ))}
              </ul>
              {Object.entries(run.narratives).length > 0 && (
                <p className="mt-2 text-[12px] text-[var(--text-secondary)]">
                  {Object.values(run.narratives).join("")}
                </p>
              )}
            </div>

            <div className="rounded-lg border border-[var(--border-default)] p-3">
              <h3 className="text-[13px] font-medium">人工复核（HITL）</h3>
              {run.review ? (
                <div className="mt-2 text-[12px]">
                  <p className="font-medium">{run.review.title ?? run.review.kind}</p>
                  <ul className="mt-1 space-y-1 text-[var(--text-secondary)]">
                    {(run.review.questions ?? []).map((question) => (
                      <li key={question}>· {question}</li>
                    ))}
                  </ul>
                  <button
                    type="button"
                    disabled={resume.isPending}
                    onClick={() => resume.mutate()}
                    className="mt-3 rounded-md bg-[var(--semantic-success)] px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-40"
                  >
                    {resume.isPending ? "提交中…" : "采纳并继续"}
                  </button>
                  {resume.isError && (
                    <p className="mt-2 text-[var(--semantic-danger)]">{String(resume.error)}</p>
                  )}
                </div>
              ) : (
                <p className="mt-2 text-[12px] text-[var(--text-muted)]">
                  {isTerminal(run.status) ? "运行已结束，无待复核项。" : "暂无待复核任务。"}
                </p>
              )}
            </div>
          </div>

          {run.error && (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] text-[var(--semantic-danger)]">
              错误 {run.error.code}: {run.error.message}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
