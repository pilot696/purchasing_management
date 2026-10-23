import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { getHealth, getObservabilitySummary, listReviews, listRuns } from "../lib/api";
import type { Module, ReviewInboxItem, RunListItem } from "../lib/api";

const MODULES: Array<{ id: Module; name: string; desc: string }> = [
  {
    id: "m1",
    name: "M1 采购申请预检",
    desc: "规则 DSL + 混合检索 + 语义消歧：拦截缺字段、规格歧义、超预算与数量异常。",
  },
  {
    id: "m2",
    name: "M2 供应商准入评估",
    desc: "五维确定性评分 + 6 条硬闸门 + LLM 复核者综述，输出准入/有条件准入/淘汰。",
  },
  {
    id: "m3",
    name: "M3 三单匹配核销",
    desc: "PO/发票/合同行级对齐 + 五类容差 + 分批核销 + PDF 原件联动。",
  },
];

const TARGETS: Record<Module, string> = { m1: "/precheck", m2: "/supplier", m3: "/matching" };
const MODULE_LABEL: Record<Module, string> = { m1: "M1 预检", m2: "M2 供应商", m3: "M3 三单" };
const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  awaiting_review: "待复核",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
};
const VERDICT_TONE: Record<string, string> = {
  pass: "bg-green-50 text-[var(--semantic-success)]",
  approve: "bg-green-50 text-[var(--semantic-success)]",
  matched: "bg-green-50 text-[var(--semantic-success)]",
  needs_info: "bg-amber-50 text-[var(--semantic-warning)]",
  conditional: "bg-amber-50 text-[var(--semantic-warning)]",
  hold: "bg-amber-50 text-[var(--semantic-warning)]",
  block: "bg-red-50 text-[var(--semantic-danger)]",
  reject: "bg-red-50 text-[var(--semantic-danger)]",
  frozen: "bg-red-50 text-[var(--semantic-danger)]",
};

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function subjectLabel(run: RunListItem): string {
  const subject = run.subject_ref ?? {};
  if (run.module === "m1") return String(subject.pr_no ?? subject.pr_id ?? "—");
  if (run.module === "m2") return String(subject.supplier_code ?? subject.supplier_id ?? "—");
  return [subject.po_no, subject.invoice_no].filter(Boolean).join(" / ") || "—";
}

function moduleBadge(module: Module): string {
  if (module === "m1") return "border-blue-200 bg-blue-50 text-[var(--brand-primary)]";
  if (module === "m2") return "border-purple-200 bg-purple-50 text-purple-700";
  return "border-teal-200 bg-teal-50 text-teal-700";
}

export default function RunCenter() {
  const navigate = useNavigate();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, retry: false });
  const summary = useQuery({ queryKey: ["observability-summary"], queryFn: getObservabilitySummary, retry: false, refetchInterval: 5000 });
  const inbox = useQuery({ queryKey: ["review-inbox"], queryFn: listReviews, retry: false, refetchInterval: 5000 });
  const runs = useQuery({ queryKey: ["runs", "recent"], queryFn: () => listRuns({ limit: 12 }), retry: false, refetchInterval: 5000 });

  const dbDown = health.data?.db === false;
  const stats = summary.data;

  const openRun = (run: RunListItem) => {
    navigate(`${TARGETS[run.module]}?run=${run.id}`);
  };
  const openReview = (review: ReviewInboxItem) => {
    navigate(`${TARGETS[review.run_module]}?run=${review.run_id}`);
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Run 中心</h1>
          <p className="mt-1 max-w-3xl text-[13px] leading-6 text-[var(--text-secondary)]">
            三条 Agent 链路的历史运行、待复核收件箱与成本概览；点击任意运行回放 SSE 事件并接续 HITL。
          </p>
        </div>
        {dbDown && (
          <span className="rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-[12px] text-[var(--semantic-danger)]">
            数据库不可用：{health.data?.env ?? "unknown"}
          </span>
        )}
      </div>

      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          ["运行总数", String(stats?.runs_total ?? "—")],
          ["待复核", String(inbox.data?.length ?? "—")],
          ["成功率", stats ? `${(stats.success_rate * 100).toFixed(1)}%` : "—"],
          ["LLM 调用 / 成本", stats ? `${stats.llm_calls} / ¥${stats.cost_cny.toFixed(4)}` : "—"],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
            <div className="text-[12px] text-[var(--text-muted)]">{label}</div>
            <div className="mt-1 text-[22px] font-semibold">{value}</div>
          </div>
        ))}
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {MODULES.map((module) => (
          <div
            key={module.id}
            className="flex flex-col justify-between rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4 shadow-sm"
          >
            <div>
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">{module.name}</h2>
                <span className={`rounded border px-2 py-0.5 text-[11px] ${moduleBadge(module.id)}`}>
                  {MODULE_LABEL[module.id]}
                </span>
              </div>
              <p className="mt-2 text-[13px] leading-6 text-[var(--text-secondary)]">{module.desc}</p>
            </div>
            <button
              type="button"
              disabled={dbDown}
              onClick={() => navigate(TARGETS[module.id])}
              className="mt-4 w-fit rounded-md bg-[var(--brand-primary)] px-3 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              新建运行
            </button>
          </div>
        ))}
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">待复核收件箱</h2>
            <span className="text-[12px] text-[var(--text-muted)]">{inbox.data?.length ?? 0} 条</span>
          </div>
          {inbox.isError && (
            <p className="mt-2 text-[12px] text-[var(--semantic-danger)]">加载失败：{String(inbox.error)}</p>
          )}
          {inbox.data?.length === 0 && (
            <p className="mt-3 text-[12px] text-[var(--text-muted)]">暂无待复核任务。M1/M2/M3 命中人工决策时会出现在这里。</p>
          )}
          <ul className="mt-3 space-y-2">
            {(inbox.data ?? []).map((review) => (
              <li key={review.id} className="rounded-lg border border-[var(--border-default)] p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded border px-1.5 py-0.5 text-[11px] ${moduleBadge(review.run_module)}`}>
                    {MODULE_LABEL[review.run_module]}
                  </span>
                  {review.verdict && (
                    <span className={`rounded px-1.5 py-0.5 text-[11px] ${VERDICT_TONE[review.verdict] ?? "bg-[var(--bg-subtle)] text-[var(--text-secondary)]"}`}>
                      {review.verdict}
                    </span>
                  )}
                  <span className="text-[11px] text-[var(--text-muted)]">{formatTime(review.created_at)}</span>
                </div>
                <p className="mt-1.5 text-[12px] leading-5 text-[var(--text-secondary)]">{review.summary}</p>
                <div className="mt-2 flex items-center justify-between">
                  <span className="text-[11px] text-[var(--text-muted)]">{review.kind}</span>
                  <button
                    type="button"
                    onClick={() => openReview(review)}
                    className="rounded-md border border-[var(--border-default)] px-2.5 py-1 text-[12px] font-medium text-[var(--brand-primary)] hover:bg-[var(--bg-subtle)]"
                  >
                    去处理
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="xl:col-span-2 rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">近期运行</h2>
            <button
              type="button"
              onClick={() => navigate("/observability")}
              className="text-[12px] text-[var(--brand-primary)] underline-offset-2 hover:underline"
            >
              查看可观测与评测 →
            </button>
          </div>
          {runs.isError && (
            <p className="mt-2 text-[12px] text-[var(--semantic-danger)]">加载失败：{String(runs.error)}</p>
          )}
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-[12px]">
              <thead>
                <tr className="border-b border-[var(--border-default)] text-left text-[var(--text-muted)]">
                  <th className="px-2 py-1.5">模块</th>
                  <th className="px-2 py-1.5">单据</th>
                  <th className="px-2 py-1.5">状态</th>
                  <th className="px-2 py-1.5 text-right">耗时</th>
                  <th className="px-2 py-1.5 text-right">tokens</th>
                  <th className="px-2 py-1.5">开始时间</th>
                  <th className="px-2 py-1.5">操作</th>
                </tr>
              </thead>
              <tbody>
                {(runs.data?.items ?? []).map((run) => (
                  <tr
                    key={run.id}
                    className="cursor-pointer border-b border-[var(--border-default)] hover:bg-[var(--bg-subtle)]"
                    onClick={() => openRun(run)}
                  >
                    <td className="px-2 py-2">
                      <span className={`rounded border px-1.5 py-0.5 text-[11px] ${moduleBadge(run.module)}`}>
                        {MODULE_LABEL[run.module]}
                      </span>
                    </td>
                    <td className="px-2 py-2 font-mono text-[11px]">{subjectLabel(run)}</td>
                    <td className="px-2 py-2">
                      <span className={`status-chip status-${run.status}`}>{STATUS_LABEL[run.status] ?? run.status}</span>
                      {run.pending_review_kind && (
                        <span className="ml-1 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-[var(--semantic-warning)]">
                          待复核
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-2 text-right">{formatDuration(run.duration_ms)}</td>
                    <td className="px-2 py-2 text-right">{(run.tokens_in + run.tokens_out).toLocaleString()}</td>
                    <td className="px-2 py-2 text-[11px] text-[var(--text-muted)]">{formatTime(run.started_at)}</td>
                    <td className="px-2 py-2">
                      <span className="text-[11px] text-[var(--brand-primary)]">回放 →</span>
                    </td>
                  </tr>
                ))}
                {(runs.data?.items ?? []).length === 0 && !runs.isLoading && (
                  <tr>
                    <td colSpan={7} className="px-2 py-6 text-center text-[var(--text-muted)]">
                      暂无运行记录，点击上方模块卡片新建一次。
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
}
