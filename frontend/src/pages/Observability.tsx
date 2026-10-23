import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { getEvalSnapshot, getObservabilitySummary, getRunTrace, listRuns } from "../lib/api";
import type { LlmCallItem, RunListItem, TraceEvent } from "../lib/api";

const MODULE_LABEL: Record<string, string> = { m1: "M1 预检", m2: "M2 供应商", m3: "M3 三单" };
const TARGETS: Record<string, string> = { m1: "/precheck", m2: "/supplier", m3: "/matching" };

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function nodeBars(events: TraceEvent[]): Array<{ node: string; start: number; end: number; duration: number }> {
  const starts = new Map<string, number>();
  const bars: Array<{ node: string; start: number; end: number; duration: number }> = [];
  let cursor = 0;
  for (const event of events) {
    const node = String((event.payload as { node?: string }).node ?? "");
    if (!node) continue;
    if (event.type === "node.start") starts.set(node, cursor);
    if (event.type === "node.end") {
      const duration = Number((event.payload as { duration_ms?: number }).duration_ms ?? 0);
      const start = starts.get(node) ?? cursor;
      bars.push({ node, start, end: start + duration, duration });
      cursor = start + duration;
    }
  }
  return bars;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-3">
      <div className="text-[11px] text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 text-[18px] font-semibold">{value}</div>
    </div>
  );
}

export default function Observability() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<"trace" | "evals">("trace");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const summary = useQuery({ queryKey: ["observability-summary"], queryFn: getObservabilitySummary, retry: false });
  const runs = useQuery({ queryKey: ["runs", "obs"], queryFn: () => listRuns({ limit: 30 }), retry: false });
  const evals = useQuery({ queryKey: ["evals"], queryFn: getEvalSnapshot, retry: false });
  const activeRunId = selectedRunId ?? runs.data?.items[0]?.id ?? null;
  const trace = useQuery({
    queryKey: ["trace", activeRunId],
    queryFn: () => getRunTrace(activeRunId as string),
    enabled: Boolean(activeRunId),
    retry: false,
  });

  const bars = useMemo(() => (trace.data ? nodeBars(trace.data.events) : []), [trace.data]);
  const maxBar = Math.max(1, ...bars.map((bar) => bar.duration));
  const stats = summary.data;
  const evalData = (evals.data?.data ?? {}) as Record<string, unknown>;
  const engineering = (evalData.engineering ?? {}) as Record<string, unknown>;

  const evalCards: Array<{ title: string; rows: Array<[string, string]> }> = [
    {
      title: "M1 预检（120 条）",
      rows: [
        ["verdict 一致率", `${(((evalData.m1 as Record<string, unknown>)?.verdict_accuracy as number) ?? 0) * 100}%`],
        ["findings F1", String(((evalData.m1 as Record<string, unknown>)?.finding_micro_f1 as number) ?? "—")],
        ["检索 top-1", `${(((evalData.m1 as Record<string, unknown>)?.retrieval_top1_accuracy as number) ?? 0) * 100}%`],
      ],
    },
    {
      title: "M2 供应商（30 家）",
      rows: [
        ["闸门检出", `${(((evalData.m2 as Record<string, unknown>)?.gate_recall as number) ?? 0) * 100}%`],
        ["闸门误报", `${(((evalData.m2 as Record<string, unknown>)?.gate_false_positive_rate as number) ?? 0) * 100}%`],
        ["冷启动一致", `${(((evalData.m2 as Record<string, unknown>)?.cold_start_consistency as number) ?? 0) * 100}%`],
      ],
    },
    {
      title: "M3 三单（80 组）",
      rows: [
        ["行对齐 F1", String(((evalData.m3 as Record<string, unknown>)?.line_alignment_f1 as number) ?? "—")],
        ["差异 F1", String(((evalData.m3 as Record<string, unknown>)?.diff_micro_f1 as number) ?? "—")],
        ["差异 recall", String(((evalData.m3 as Record<string, unknown>)?.diff_recall as number) ?? "—")],
      ],
    },
    {
      title: "工程验证",
      rows: [
        ["后端单测", String((engineering.backend_unit as Record<string, unknown>)?.passed ?? "—")],
        ["集成测试", String((engineering.backend_integration as Record<string, unknown>)?.passed ?? "—")],
        ["前端单测", String(engineering.frontend_unit ?? "—")],
      ],
    },
  ];

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold">可观测与评测</h1>
        <p className="mt-1 max-w-3xl text-[13px] leading-6 text-[var(--text-secondary)]">
          run_event 回放、节点耗时、LLM 调用记账（tokens/成本/延迟/缓存）与四套离线评测基线；
          每个结论都能追溯到规则、证据与人工决策。
        </p>
      </div>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <Metric label="运行总数" value={String(stats?.runs_total ?? "—")} />
        <Metric label="成功率" value={stats ? `${(stats.success_rate * 100).toFixed(1)}%` : "—"} />
        <Metric label="待复核" value={String(stats?.pending_reviews ?? "—")} />
        <Metric label="P95 耗时" value={formatDuration(stats?.p95_duration_ms)} />
        <Metric label="累计 tokens" value={stats ? (stats.tokens_in + stats.tokens_out).toLocaleString() : "—"} />
        <Metric label="累计成本" value={stats ? `¥${stats.cost_cny.toFixed(4)}` : "—"} />
      </section>

      <div className="flex gap-2">
        {[
          ["trace", "运行 Trace"],
          ["evals", "评测基线"],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key as "trace" | "evals")}
            className={`rounded-md border px-3 py-1.5 text-[12px] ${
              tab === key
                ? "border-[var(--brand-primary)] bg-blue-50 text-[var(--brand-primary)]"
                : "border-[var(--border-default)] text-[var(--text-secondary)]"
            }`}
          >
            {label}
          </button>
        ))}
        {evals.data?.available && (
          <span className="ml-auto self-center text-[11px] text-[var(--text-muted)]">
            evals/latest.json · seed={evals.data.seed} · {evals.data.llm_mode}
          </span>
        )}
      </div>

      {tab === "trace" ? (
        <section className="grid grid-cols-1 gap-4 xl:grid-cols-4">
          <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-3 xl:col-span-1">
            <h2 className="px-1 text-sm font-semibold">运行列表</h2>
            <ul className="mt-2 max-h-[560px] space-y-1 overflow-auto">
              {(runs.data?.items ?? []).map((run: RunListItem) => (
                <li key={run.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedRunId(run.id)}
                    className={`w-full rounded-md px-2 py-1.5 text-left text-[12px] ${
                      activeRunId === run.id ? "bg-blue-50 text-[var(--brand-primary)]" : "hover:bg-[var(--bg-subtle)]"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[11px]">{run.id.slice(0, 8)}</span>
                      <span className="text-[10px] text-[var(--text-muted)]">{formatDuration(run.duration_ms)}</span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-1 text-[10px] text-[var(--text-muted)]">
                      <span>{MODULE_LABEL[run.module] ?? run.module}</span>
                      <span>/</span>
                      <span>{run.status}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="flex flex-col gap-4 xl:col-span-3">
            {trace.isError && (
              <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] text-[var(--semantic-danger)]">
                Trace 加载失败：{String(trace.error)}
              </p>
            )}
            {trace.data && (
              <>
                <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-semibold">节点时间线</h2>
                      <span className="text-[11px] text-[var(--text-muted)]">run {trace.data.run.id.slice(0, 8)}</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => navigate(`${TARGETS[trace.data.run.module]}?run=${trace.data.run.id}`)}
                      className="text-[12px] text-[var(--brand-primary)] underline-offset-2 hover:underline"
                    >
                      打开业务页 →
                    </button>
                  </div>
                  <ul className="mt-3 space-y-2">
                    {bars.map((bar, index) => (
                      <li key={`${bar.node}-${index}`} className="flex items-center gap-2 text-[12px]">
                        <span className="w-40 shrink-0 truncate font-mono text-[11px]">{bar.node}</span>
                        <div className="h-3 flex-1 rounded bg-[var(--bg-subtle)]">
                          <div
                            className="h-3 rounded bg-[var(--brand-primary)]"
                            style={{ width: `${Math.max(2, (bar.duration / maxBar) * 100)}%` }}
                          />
                        </div>
                        <span className="w-16 shrink-0 text-right text-[11px] text-[var(--text-secondary)]">
                          {formatDuration(bar.duration)}
                        </span>
                      </li>
                    ))}
                    {bars.length === 0 && (
                      <li className="text-[12px] text-[var(--text-muted)]">该运行没有节点事件（或尚未产生）。</li>
                    )}
                  </ul>
                </div>

                <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
                  <h2 className="text-sm font-semibold">LLM 调用记账（{trace.data.llm_calls.length}）</h2>
                  <div className="mt-3 overflow-x-auto">
                    <table className="w-full min-w-[820px] border-collapse text-[12px]">
                      <thead>
                        <tr className="border-b border-[var(--border-default)] text-left text-[var(--text-muted)]">
                          <th className="px-2 py-1.5">step</th>
                          <th className="px-2 py-1.5">provider / model</th>
                          <th className="px-2 py-1.5 text-right">tokens in/out</th>
                          <th className="px-2 py-1.5 text-right">成本</th>
                          <th className="px-2 py-1.5 text-right">延迟</th>
                          <th className="px-2 py-1.5 text-right">retry</th>
                          <th className="px-2 py-1.5">状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {trace.data.llm_calls.map((call: LlmCallItem) => (
                          <tr key={call.id} className="border-b border-[var(--border-default)]">
                            <td className="px-2 py-2 font-mono text-[11px]">{call.step}</td>
                            <td className="px-2 py-2">
                              {call.provider} / {call.model}
                            </td>
                            <td className="px-2 py-2 text-right">
                              {call.tokens_in.toLocaleString()} / {call.tokens_out.toLocaleString()}
                            </td>
                            <td className="px-2 py-2 text-right">¥{call.cost_cny.toFixed(5)}</td>
                            <td className="px-2 py-2 text-right">{call.latency_ms}ms</td>
                            <td className="px-2 py-2 text-right">{call.retry_no}</td>
                            <td className="px-2 py-2">
                              {call.ok ? (
                                <span className="text-[var(--semantic-success)]">ok</span>
                              ) : (
                                <span className="text-[var(--semantic-danger)]">{call.error_code ?? "failed"}</span>
                              )}
                              {call.cache_hit && <span className="ml-1 text-[10px] text-[var(--brand-primary)]">cache</span>}
                            </td>
                          </tr>
                        ))}
                        {trace.data.llm_calls.length === 0 && (
                          <tr>
                            <td colSpan={7} className="px-2 py-5 text-center text-[var(--text-muted)]">
                              该运行没有 LLM 调用（确定性链路）。
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                  {trace.data.artifacts.length > 0 && (
                    <p className="mt-3 text-[11px] text-[var(--text-muted)]">
                      artifacts：{trace.data.artifacts.map((artifact) => artifact.kind).join("、")}
                    </p>
                  )}
                </div>
              </>
            )}
          </div>
        </section>
      ) : (
        <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {evals.isError && (
            <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] text-[var(--semantic-danger)]">
              评测快照加载失败：{String(evals.error)}
            </p>
          )}
          {!evals.isLoading && evals.data?.available === false && (
            <p className="text-[13px] text-[var(--text-muted)]">
              尚未生成 evals/latest.json：运行 `python scripts/m2_eval.py` 等基线脚本后刷新。
            </p>
          )}
          {evalCards.map((card) => (
            <div key={card.title} className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
              <h2 className="text-sm font-semibold">{card.title}</h2>
              <dl className="mt-3 space-y-2 text-[12px]">
                {card.rows.map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between">
                    <dt className="text-[var(--text-muted)]">{label}</dt>
                    <dd className="font-medium">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-[11px] leading-5 text-[var(--semantic-warning)] md:col-span-2 xl:col-span-4">
            诚实边界：金标与实现同源（回归一致性，非真实业务精度）；当前为离线 FakeLLM 基线，真实成本/延迟/质量需接入模型后回填。
            详见 docs/EVAL_REPORT.md §7。
          </div>
        </section>
      )}
    </div>
  );
}
