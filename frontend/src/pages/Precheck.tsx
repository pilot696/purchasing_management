import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { getDemoRole, getRunSnapshot, resumeRun, startRun } from "../lib/api";
import { isTerminal } from "../lib/events";
import {
  asM1Report,
  budgetBar,
  buildSelectedEdits,
  candidateSourcesText,
  findingTypeLabel,
  formatPatchValue,
  formatSpecValue,
  initialPatchSelections,
  patchPathLabel,
  patchSourceLabel,
  roleCanReviewM1,
  severityClass,
  verdictMeta,
  verdictToneClass,
} from "../lib/m1";
import type { PatchSelection } from "../lib/m1";
import { useRunStream } from "../lib/useRunStream";

const DEFAULT_PR = "PR-2026-0001";

export default function Precheck() {
  const [prNo, setPrNo] = useState(DEFAULT_PR);
  const [searchParams] = useSearchParams();
  const [runId, setRunId] = useState<string | null>(() => searchParams.get("run"));
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [patchSelections, setPatchSelections] = useState<Record<number, PatchSelection>>({});

  const role = getDemoRole();
  const run = useRunStream(runId);
  const review = run.review;
  const snapshot = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRunSnapshot(runId as string),
    enabled: Boolean(runId) && isTerminal(run.status),
  });

  const report = useMemo(() => {
    const fromReview = asM1Report(review?.report);
    if (fromReview) return fromReview;
    const artifact = snapshot.data?.artifact as { report?: unknown } | null | undefined;
    return asM1Report(artifact?.report);
  }, [review?.report, snapshot.data?.artifact]);

  useEffect(() => {
    if (report) setPatchSelections(initialPatchSelections(report.patches));
  }, [report]);

  const selectedEdits = report ? buildSelectedEdits(report.patches, patchSelections) : [];

  const start = useMutation({
    mutationFn: () => startRun("m1", { pr_no: prNo }),
    onSuccess: (response) => {
      setRunId(response.run_id);
      setDecisionError(null);
    },
  });
  const decide = useMutation({
    mutationFn: (decision: "accept" | "reject") => {
      if (!runId || !review) throw new Error("当前没有待复核任务");
      if (decision === "reject") {
        return resumeRun(runId, {
          review_id: review.review_id,
          decision,
          note: "M1 退回申请人补充",
        });
      }
      const hasPatches = (report?.patches.length ?? 0) > 0;
      return resumeRun(runId, {
        review_id: review.review_id,
        decision: hasPatches ? "edit" : "accept",
        edits: selectedEdits,
        note: `M1 采纳 ${selectedEdits.length}/${report?.patches.length ?? 0} 条补丁`,
      });
    },
    onError: (error) => setDecisionError(String(error)),
    onSuccess: () => setDecisionError(null),
  });

  const meta = report ? verdictMeta(report.verdict) : null;
  const budget = report?.budget ? budgetBar(report.budget.available, report.budget.estimated) : null;
  const canReview = roleCanReviewM1(role);
  const awaiting = run.status === "awaiting_review";
  const autoPassed = report != null && report.requires_review === false;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">M1 采购申请预检</h1>
          <p className="mt-1 max-w-3xl text-[13px] leading-6 text-[var(--text-secondary)]">
            规则引擎（可配置 JSONLogic）+ 混合检索（词法 / 精确 / 规格桶 + RRF）→ 结论横幅 → 可采纳补丁 → 写回 PR 明细。
            无问题的申请自动放行，不强制人工确认。
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
            PR 号
            <input
              className="w-48 rounded-md border border-[var(--border-default)] px-2 py-1.5 text-[13px]"
              value={prNo}
              onChange={(event) => setPrNo(event.target.value)}
              disabled={Boolean(runId)}
            />
          </label>
          <button
            type="button"
            disabled={start.isPending || !prNo.trim()}
            onClick={() => start.mutate()}
            className="rounded-md bg-[var(--brand-primary)] px-3 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {start.isPending ? "启动中…" : "启动 M1 预检"}
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
          <span className="text-[12px] text-[var(--text-muted)]">演示数据：{DEFAULT_PR}（可改）</span>
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
                <span className="font-medium">{report.pr_no}</span>
                {report.title && <span className="text-[var(--text-muted)]">{report.title}</span>}
                {report.category_code && (
                  <span className="rounded-md border border-[var(--border-default)] bg-[var(--bg-subtle)] px-2 py-1 text-[12px]">
                    {report.category_code}
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
                  {decide.isPending
                    ? "提交中…"
                    : `采纳并继续（${selectedEdits.length}/${report.patches.length}）`}
                </button>
                <button
                  type="button"
                  disabled={!awaiting || !canReview || decide.isPending}
                  onClick={() => decide.mutate("reject")}
                  className="rounded-md border border-[var(--border-default)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-secondary)] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  退回补充
                </button>
              </div>
            </div>

            {budget && report.budget && (
              <div className="mt-3">
                <div className="flex items-center justify-between text-[12px] text-[var(--text-secondary)]">
                  <span>
                    部门可用预算 ¥{report.budget.available.toFixed(2)} · 本次预估 ¥
                    {report.budget.estimated.toFixed(2)}
                  </span>
                  <span className={report.budget.over_budget ? "text-[var(--semantic-danger)]" : ""}>
                    {report.budget.over_budget ? "超出可用预算" : "预算内"}
                  </span>
                </div>
                <div className="mt-1 h-2 w-full overflow-hidden rounded bg-[var(--bg-subtle)]">
                  <div
                    className={`h-full ${budget.over ? "bg-[var(--semantic-danger)]" : "bg-[var(--brand-primary)]"}`}
                    style={{ width: `${budget.percent}%` }}
                  />
                </div>
              </div>
            )}

            {autoPassed && (
              <p className="mt-2 text-[12px] text-[var(--semantic-success)]">
                无 findings / 补丁，系统已自动放行（未创建人工复核任务）。
              </p>
            )}
            {!canReview && awaiting && (
              <p className="mt-2 text-[12px] text-[var(--semantic-warning)]">
                当前角色 {role} 无 M1 复核权限，请在右上角切换为 buyer（或 admin）。
              </p>
            )}
            {decisionError && (
              <p className="mt-2 text-[12px] text-[var(--semantic-danger)]">决策失败：{decisionError}</p>
            )}
            {report.summary && <p className="mt-2 text-[12px] text-[var(--text-muted)]">{report.summary}</p>}
          </section>

          <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
              <h2 className="text-sm font-semibold">申请明细与检索候选</h2>
              <ul className="mt-3 space-y-3">
                {report.items.map((item) => (
                  <li key={item.line_no} className="rounded-lg border border-[var(--border-default)] p-3">
                    <div className="flex flex-wrap items-center gap-2 text-[13px]">
                      <span className="font-medium">{item.item_name_raw}</span>
                      <span className="rounded bg-[var(--bg-subtle)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)]">
                        {item.spec_bucket ?? "unknown"}
                      </span>
                      <span className="text-[11px] text-[var(--text-muted)]">
                        {item.qty} {item.unit}
                      </span>
                      {item.ambiguous && (
                        <span className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[11px] text-[var(--semantic-warning)]">
                          规格歧义
                        </span>
                      )}
                    </div>
                    <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[12px]">
                      {Object.entries(item.spec).map(([key, value]) => (
                        <div key={key} className="flex justify-between gap-2">
                          <dt className="text-[var(--text-muted)]">{key}</dt>
                          <dd className="truncate">{formatSpecValue(value)}</dd>
                        </div>
                      ))}
                    </dl>
                    {item.candidates.length > 0 && (
                      <div className="mt-2 space-y-1">
                        <div className="text-[11px] text-[var(--text-muted)]">检索候选（RRF 融合）</div>
                        {item.candidates.map((candidate) => (
                          <div key={candidate.sku_code} className="flex flex-wrap justify-between gap-2 text-[11px]">
                            <span className="truncate">
                              {candidate.name} · {candidate.spec_bucket}
                            </span>
                            <span className="text-[var(--text-muted)]">
                              {candidateSourcesText(candidate.sources)} · {candidate.score.toFixed(4)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            <div className="flex flex-col gap-4">
              <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
                <h2 className="text-sm font-semibold">发现的问题（{report.findings.length}）</h2>
                <ul className="mt-3 space-y-2">
                  {report.findings.length === 0 && (
                    <li className="text-[12px] text-[var(--text-muted)]">无问题。</li>
                  )}
                  {report.findings.map((finding, index) => (
                    <li
                      key={`${finding.rule_code ?? finding.type}-${index}`}
                      className={`rounded-lg border px-3 py-2 text-[12px] ${severityClass(finding.severity)}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">
                          {findingTypeLabel(finding.type)} · {finding.rule_code ?? finding.type}
                        </span>
                        <span className="text-[11px]">{finding.severity}</span>
                      </div>
                      <div className="mt-1 text-[11px]">{finding.message}</div>
                      {finding.field && (
                        <div className="mt-1 text-[11px] text-[var(--text-secondary)]">字段 {finding.field}</div>
                      )}
                      {finding.evidence?.length ? (
                        <div className="mt-1 text-[11px] text-[var(--text-secondary)]">
                          证据：{finding.evidence.map((evidence) => evidence.ref).join("、")}
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
                <h2 className="text-sm font-semibold">
                  建议补丁（{report.patches.length}，已选 {selectedEdits.length}）
                </h2>
                <p className="mt-1 text-[11px] text-[var(--text-muted)]">
                  勾选并可直接改值；未勾选的补丁不会写回。finalize 单事务 + 审计。
                </p>
                <ul className="mt-2 space-y-2">
                  {report.patches.length === 0 && (
                    <li className="text-[12px] text-[var(--text-muted)]">无需补丁。</li>
                  )}
                  {report.patches.map((patch, index) => {
                    const selection =
                      patchSelections[index] ?? { checked: true, value: formatPatchValue(patch.value) };
                    return (
                      <li
                        key={`${patch.path}-${index}`}
                        className={`rounded-lg border p-2 text-[12px] ${
                          selection.checked
                            ? "border-[var(--border-default)]"
                            : "border-dashed border-[var(--border-strong)] opacity-60"
                        }`}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <label className="flex items-center gap-2 font-medium">
                            <input
                              type="checkbox"
                              checked={selection.checked}
                              onChange={(event) =>
                                setPatchSelections((prev) => ({
                                  ...prev,
                                  [index]: { ...selection, checked: event.target.checked },
                                }))
                              }
                              disabled={!awaiting || !canReview}
                            />
                            {patchPathLabel(patch.path)}
                          </label>
                          <span className="rounded bg-[var(--bg-subtle)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)]">
                            {patchSourceLabel(patch.source)}
                            {patch.ref ? ` · ${patch.ref}` : ""}
                          </span>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-[var(--text-secondary)]">
                          <span>{formatPatchValue(patch.from)} →</span>
                          <input
                            className="w-32 rounded border border-[var(--border-default)] px-1.5 py-0.5 text-[12px]"
                            value={selection.value}
                            onChange={(event) =>
                              setPatchSelections((prev) => ({
                                ...prev,
                                [index]: { ...selection, value: event.target.value },
                              }))
                            }
                            disabled={!awaiting || !canReview || !selection.checked}
                          />
                          <span className="text-[var(--text-muted)]">
                            （原建议：{formatPatchValue(patch.value)}）
                          </span>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </div>
          </section>
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


