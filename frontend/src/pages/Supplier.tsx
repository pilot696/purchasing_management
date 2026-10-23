import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { getDemoRole, getRunSnapshot, resumeRun, startRun } from "../lib/api";
import { isTerminal } from "../lib/events";
import {
  asM2Report,
  confidenceLabel,
  dimensionLabel,
  formatMetricValue,
  gateLabel,
  llmStatusLabel,
  metricLabel,
  radarLabelPoints,
  radarPoints,
  roleCanReviewM2,
  severityClass,
  verdictMeta,
  verdictToneClass,
} from "../lib/m2";
import { useRunStream } from "../lib/useRunStream";

const DEFAULT_SUPPLIER = "SUP-001";

function Radar({ dimensions }: { dimensions: Array<{ dimension: string; score: number }> }) {
  const { points, axes } = radarPoints(dimensions);
  const labels = radarLabelPoints(dimensions);
  return (
    <svg viewBox="0 0 220 220" className="h-56 w-56">
      {[20, 40, 60, 80].map((radius) => (
        <circle key={radius} cx={110} cy={110} r={radius} fill="none" stroke="var(--border-default)" strokeWidth={1} />
      ))}
      {axes.map((axis) => (
        <line key={`${axis.x}-${axis.y}`} x1={110} y1={110} x2={axis.x * 0 + 110 + (axis.x - 110) * 1.25} y2={110 + (axis.y - 110) * 1.25} stroke="var(--border-default)" />
      ))}
      <polygon points={points} fill="rgba(29,78,216,0.18)" stroke="var(--brand-primary)" strokeWidth={2} />
      {labels.map((label, index) => (
        <text key={label.label} x={label.x} y={label.y} textAnchor="middle" fontSize={9} fill="var(--text-secondary)">
          {dimensionLabel(label.label)} {dimensions[index]?.score?.toFixed(0)}
        </text>
      ))}
    </svg>
  );
}

export default function Supplier() {
  const [supplierCode, setSupplierCode] = useState(DEFAULT_SUPPLIER);
  const [searchParams] = useSearchParams();
  const [runId, setRunId] = useState<string | null>(() => searchParams.get("run"));
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const role = getDemoRole();
  const run = useRunStream(runId);
  const review = run.review;
  const snapshot = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRunSnapshot(runId as string),
    enabled: Boolean(runId) && isTerminal(run.status),
  });

  const report = useMemo(() => {
    const fromReview = asM2Report(review?.report);
    if (fromReview) return fromReview;
    const artifact = snapshot.data?.artifact as { report?: unknown } | null | undefined;
    return asM2Report(artifact?.report);
  }, [review?.report, snapshot.data?.artifact]);

  const start = useMutation({
    mutationFn: () => startRun("m2", { supplier_code: supplierCode, mode: "review" }),
    onSuccess: (response) => {
      setRunId(response.run_id);
      setDecisionError(null);
    },
  });
  const decide = useMutation({
    mutationFn: (decision: "accept" | "reject") => {
      if (!runId || !review) throw new Error("当前没有待复核任务");
      return resumeRun(runId, {
        review_id: review.review_id,
        decision,
        note: decision === "accept" ? "M2 确认准入结论" : "M2 退回重新评估",
      });
    },
    onError: (error) => setDecisionError(String(error)),
    onSuccess: () => setDecisionError(null),
  });

  const meta = report ? verdictMeta(report.verdict) : null;
  const canReview = roleCanReviewM2(role);
  const awaiting = run.status === "awaiting_review";
  const vetoGates = report?.gate_hits.filter((gate) => gate.veto) ?? [];
  const softGates = report?.gate_hits.filter((gate) => !gate.veto) ?? [];

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">M2 供应商准入与履约评估</h1>
          <p className="mt-1 max-w-3xl text-[13px] leading-6 text-[var(--text-secondary)]">
            6 条硬闸门（一票否决，闸门命中直接跳过 LLM）→ 五维确定性评分 → 权重扰动敏感性 → LLM 复核者叙述
            （schema 无分数字段）→ 人工确认 → 单事务写 supplier_eval + 审计。
          </p>
        </div>
        {runId && (
          <div className="flex items-center gap-2 text-[12px] text-[var(--text-muted)]">
            <span className={`status-chip status-${run.status}`}>{run.status}</span>
            <span>run {runId.slice(0, 8)}</span>
            <span>lastSeq={run.lastSeq}</span>
          </div>
        )}
      </div>

      <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[12px] text-[var(--text-secondary)]">
            供应商编码
            <input
              className="w-44 rounded-md border border-[var(--border-default)] px-2 py-1.5 text-[13px]"
              value={supplierCode}
              onChange={(event) => setSupplierCode(event.target.value)}
              disabled={Boolean(runId)}
            />
          </label>
          <button
            type="button"
            disabled={start.isPending || !supplierCode.trim()}
            onClick={() => start.mutate()}
            className="rounded-md bg-[var(--brand-primary)] px-3 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {start.isPending ? "启动中…" : "启动 M2 评估"}
          </button>
          {runId && (
            <button
              type="button"
              onClick={() => setRunId(null)}
              className="text-[13px] text-[var(--text-secondary)] underline-offset-2 hover:underline"
            >
              关闭本次运行
            </button>
          )}
          <span className="text-[12px] text-[var(--text-muted)]">
            演示：{DEFAULT_SUPPLIER}（干净）/ SUP-004（G1 闸门）/ SUP-025（冷启动）
          </span>
        </div>
        {start.isError && (
          <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-[var(--semantic-danger)]">
            启动失败：{String(start.error)}
          </p>
        )}
        {Object.keys(run.nodes).length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 text-[12px]">
            {Object.entries(run.nodes).map(([node, info]) => (
              <span
                key={node}
                className={`rounded-md border px-2 py-1 ${
                  info.state === "end"
                    ? "border-green-200 bg-green-50 text-[var(--semantic-success)]"
                    : "border-blue-200 bg-blue-50 text-[var(--brand-primary)]"
                }`}
              >
                {info.state === "end" ? "✓" : "◉"} {node}
                {info.durationMs !== undefined ? ` · ${info.durationMs}ms` : ""}
              </span>
            ))}
          </div>
        )}
        {run.error && (
          <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-[var(--semantic-danger)]">
            {run.error.code}: {run.error.message}
          </p>
        )}
      </section>

      {report && meta && (
        <>
          <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className={`rounded-md border px-2.5 py-1 text-[12px] font-medium ${verdictToneClass(meta.tone)}`}>
                  {meta.label}
                </span>
                <span className="font-medium">{report.supplier_name}</span>
                <span className="text-[var(--text-muted)]">{report.supplier_code}</span>
                {report.category_code && (
                  <span className="rounded-md border border-[var(--border-default)] bg-[var(--bg-subtle)] px-2 py-1 text-[12px]">
                    {report.category_code}
                  </span>
                )}
                {report.cold_start && (
                  <span className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[12px] text-[var(--semantic-warning)]">
                    冷启动（无历史）
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={!awaiting || !canReview || decide.isPending}
                  onClick={() => decide.mutate("accept")}
                  className="rounded-md bg-[var(--semantic-success)] px-3 py-1.5 text-[12px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {decide.isPending ? "提交中…" : "确认结论"}
                </button>
                <button
                  type="button"
                  disabled={!awaiting || !canReview || decide.isPending}
                  onClick={() => decide.mutate("reject")}
                  className="rounded-md border border-[var(--border-default)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-secondary)] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  退回重评
                </button>
              </div>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-4 text-[13px]">
              <div>
                <span className="text-[28px] font-semibold">{report.total.toFixed(2)}</span>
                <span className="ml-1 text-[12px] text-[var(--text-muted)]">/ 100 加权总分</span>
              </div>
              <span className="rounded-md border border-[var(--border-default)] bg-[var(--bg-subtle)] px-2 py-1 text-[12px]">
                {confidenceLabel(report.confidence)}
              </span>
              {report.sensitivity?.flip_rate !== undefined && (
                <span className="text-[12px] text-[var(--text-secondary)]">
                  权重扰动翻转率 {(report.sensitivity.flip_rate * 100).toFixed(1)}%（最敏感：
                  {dimensionLabel(report.sensitivity.most_sensitive ?? "none")}）
                </span>
              )}
              <span className="text-[12px] text-[var(--text-muted)]">
                权重版本 {report.weight_version ?? "v1"} · 证据 {report.evidence_pack_ids?.length ?? 0} 条
              </span>
            </div>

            {!canReview && awaiting && (
              <p className="mt-2 text-[12px] text-[var(--semantic-warning)]">
                当前角色 {role} 无 M2 复核权限，请在右上角切换为 buyer（或 admin）。
              </p>
            )}
            {decisionError && (
              <p className="mt-2 text-[12px] text-[var(--semantic-danger)]">决策失败：{decisionError}</p>
            )}
            {report.summary && <p className="mt-2 text-[12px] text-[var(--text-muted)]">{report.summary}</p>}
          </section>

          <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
              <h2 className="text-sm font-semibold">五维得分（雷达）</h2>
              {report.dimensions.length > 0 ? (
                <div className="mt-2 flex justify-center">
                  <Radar dimensions={report.dimensions} />
                </div>
              ) : (
                <p className="mt-3 text-[12px] text-[var(--text-muted)]">
                  闸门命中：跳过评分与 LLM（避免模型美化否决项）。
                </p>
              )}
            </div>

            <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
              <h2 className="text-sm font-semibold">硬闸门（6 条）</h2>
              <ul className="mt-3 space-y-2">
                {report.gate_hits.length === 0 && (
                  <li className="text-[12px] text-[var(--semantic-success)]">6 条硬闸门全部通过。</li>
                )}
                {vetoGates.map((gate) => (
                  <li key={gate.code} className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px]">
                    <div className="font-medium text-[var(--semantic-danger)]">
                      {gateLabel(gate.code)} · 一票否决
                    </div>
                    <div className="mt-1 text-[11px] text-[var(--text-secondary)]">{gate.message}</div>
                    {gate.evidence_refs.length > 0 && (
                      <div className="mt-1 text-[11px] text-[var(--text-muted)]">
                        证据：{gate.evidence_refs.join("、")}
                      </div>
                    )}
                  </li>
                ))}
                {softGates.map((gate) => (
                  <li key={gate.code} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px]">
                    <div className="font-medium text-[var(--semantic-warning)]">{gateLabel(gate.code)} · 条件项</div>
                    <div className="mt-1 text-[11px] text-[var(--text-secondary)]">{gate.message}</div>
                  </li>
                ))}
              </ul>

              <h3 className="mt-4 text-[13px] font-medium">准入条件</h3>
              <ul className="mt-2 space-y-1 text-[12px] text-[var(--text-secondary)]">
                {report.conditions.length === 0 && <li className="text-[var(--text-muted)]">无附加条件。</li>}
                {report.conditions.map((condition) => (
                  <li key={condition}>· {condition}</li>
                ))}
              </ul>
            </div>

            <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
              <h2 className="text-sm font-semibold">LLM 风险复核</h2>
              <p className="mt-1 text-[11px] text-[var(--text-muted)]">{llmStatusLabel(report.llm_status)}</p>
              {report.llm_review ? (
                <div className="mt-2 space-y-2 text-[12px]">
                  <p className="leading-5 text-[var(--text-secondary)]">{report.llm_review.risk_summary}</p>
                  <ul className="space-y-1">
                    {report.llm_review.top_risks.map((risk, index) => (
                      <li key={`${risk.claim}-${index}`} className={`rounded border px-2 py-1 text-[11px] ${severityClass(risk.severity_hint)}`}>
                        <span className="font-medium">{dimensionLabel(risk.dimension)}</span> · {risk.claim}
                        {risk.evidence_refs.length > 0 && (
                          <span className="ml-1 text-[var(--text-muted)]">[{risk.evidence_refs.join(", ")}]</span>
                        )}
                      </li>
                    ))}
                  </ul>
                  {report.llm_review.conditions.length > 0 && (
                    <div>
                      <div className="text-[11px] font-medium">模型建议条件</div>
                      <ul className="mt-1 space-y-0.5 text-[11px] text-[var(--text-secondary)]">
                        {report.llm_review.conditions.map((condition) => (
                          <li key={condition}>· {condition}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <p className="text-[11px] text-[var(--text-muted)]">复核备忘：{report.llm_review.reviewer_memo}</p>
                </div>
              ) : (
                <p className="mt-2 text-[12px] text-[var(--text-muted)]">
                  未生成 LLM 叙述（离线模式或闸门短路）。分数与闸门不受影响。
                </p>
              )}
            </div>
          </section>

          {report.dimensions.length > 0 && (
            <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
              <h2 className="text-sm font-semibold">维度明细（可追溯原始指标）</h2>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[720px] border-collapse text-[12px]">
                  <thead>
                    <tr className="border-b border-[var(--border-default)] text-left text-[var(--text-muted)]">
                      <th className="px-2 py-1.5">维度</th>
                      <th className="px-2 py-1.5">得分</th>
                      <th className="px-2 py-1.5 w-40">进度</th>
                      <th className="px-2 py-1.5 text-right">权重</th>
                      <th className="px-2 py-1.5 text-right">样本</th>
                      <th className="px-2 py-1.5">原始指标</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.dimensions.map((dimension) => (
                      <tr key={dimension.dimension} className="border-b border-[var(--border-default)] align-top">
                        <td className="px-2 py-2 font-medium">{dimensionLabel(dimension.dimension)}</td>
                        <td className="px-2 py-2">{dimension.score.toFixed(2)}</td>
                        <td className="px-2 py-2">
                          <div className="h-2 w-full overflow-hidden rounded bg-[var(--bg-subtle)]">
                            <div
                              className="h-full bg-[var(--brand-primary)]"
                              style={{ width: `${Math.min(100, Math.max(0, dimension.score))}%` }}
                            />
                          </div>
                        </td>
                        <td className="px-2 py-2 text-right">{(dimension.weight * 100).toFixed(0)}%</td>
                        <td className="px-2 py-2 text-right">{dimension.sample_size}</td>
                        <td className="px-2 py-2 text-[11px] text-[var(--text-secondary)]">
                          {Object.entries(dimension.raw_metrics)
                            .filter(([, value]) => value !== null && value !== undefined)
                            .map(([key, value]) => `${metricLabel(key)}=${formatMetricValue(value)}`)
                            .join(" · ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}

      {runId && isTerminal(run.status) && !report && (
        <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4 text-[13px] text-[var(--text-secondary)]">
          运行已结束（{run.status}），但未取到报告。
        </section>
      )}
    </div>
  );
}

