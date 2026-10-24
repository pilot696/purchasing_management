import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import PdfDrawer from "../components/PdfDrawer";
import {
  extractDocument,
  getDemoRole,
  getDocumentAnnotations,
  getRunSnapshot,
  ingestDocument,
  resumeRun,
  startRun,
  uploadDocument,
} from "../lib/api";
import type { DocumentAnnotation, UploadedDoc } from "../lib/api";
import { isTerminal } from "../lib/events";
import {
  alignRows,
  asM3Report,
  diffMeta,
  formatDelta,
  formatMoney,
  formatQty,
  overallMeta,
  overallToneClass,
  parseInvoiceNos,
  roleCanReviewM3,
  severityClass,
} from "../lib/m3";
import { useRunStream } from "../lib/useRunStream";

const DEFAULT_PO = "PO-2026-0001";
const DEFAULT_INVOICE = "INV-2026-3000";

interface DrawerState {
  docId: string;
  title: string;
}

export default function Matching() {
  const [poNo, setPoNo] = useState(DEFAULT_PO);
  const [invoiceNo, setInvoiceNo] = useState(DEFAULT_INVOICE);
  const [searchParams] = useSearchParams();
  const [runId, setRunId] = useState<string | null>(() => searchParams.get("run"));
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<DrawerState | null>(null);
  const [annotations, setAnnotations] = useState<DocumentAnnotation[]>([]);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadDocType, setUploadDocType] = useState("invoice");
  const [uploadLinkedNo, setUploadLinkedNo] = useState("");
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [uploadedDoc, setUploadedDoc] = useState<UploadedDoc | null>(null);
  const [ingestMessage, setIngestMessage] = useState<string | null>(null);

  const role = getDemoRole();
  const run = useRunStream(runId);
  const review = run.review;
  const snapshot = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRunSnapshot(runId as string),
    enabled: Boolean(runId) && isTerminal(run.status),
  });

  const report = useMemo(() => {
    const fromReview = asM3Report(review?.report);
    if (fromReview) return fromReview;
    const artifact = snapshot.data?.artifact as { report?: unknown } | null | undefined;
    return asM3Report(artifact?.report);
  }, [review?.report, snapshot.data?.artifact]);

  const rows = useMemo(() => (report ? alignRows(report) : []), [report]);

  const start = useMutation({
    mutationFn: () => startRun("m3", { po_no: poNo, invoice_nos: parseInvoiceNos(invoiceNo) }),
    onSuccess: (response) => {
      setRunId(response.run_id);
      setDecisionError(null);
      setDrawer(null);
    },
  });

  const decide = useMutation({
    mutationFn: (decision: "accept" | "reject") => {
      if (!runId || !review) throw new Error("当前没有待复核任务");
      return resumeRun(runId, {
        review_id: review.review_id,
        decision,
        note: decision === "accept" ? "M3 财务放行" : "M3 挂起待处理",
      });
    },
    onError: (error) => setDecisionError(String(error)),
    onSuccess: () => setDecisionError(null),
  });

  const upload = useMutation({
    mutationFn: () => {
      if (!uploadFile) throw new Error("请选择 PDF 文件");
      return uploadDocument(uploadFile, uploadDocType, uploadLinkedNo.trim() || undefined);
    },
    onSuccess: (doc) => {
      setUploadMessage(
        `${doc.duplicate ? "已存在（sha256 去重）" : "上传成功"}：doc ${doc.id.slice(0, 8)} · ${doc.doc_type}` +
          (doc.linked_kind ? ` · 已关联 ${doc.linked_kind}` : ""),
      );
      setUploadFile(null);
      setUploadedDoc(doc);
      setIngestMessage(null);
    },
    onError: (error) => setUploadMessage(`上传失败：${String(error)}`),
  });

  const ingest = useMutation({
    mutationFn: () => {
      if (!uploadedDoc) throw new Error("请先上传 PDF");
      return ingestDocument(uploadedDoc.id, uploadLinkedNo.trim() || undefined);
    },
    onSuccess: (result) => {
      setInvoiceNo(result.invoice.invoice_no);
      setIngestMessage(
        result.already_linked
          ? `已建档：${result.invoice.invoice_no}（文档此前已关联）`
          : `${result.created ? "建档成功" : "复用已有发票"}：${result.invoice.invoice_no} · ${result.lines} 行` +
              (result.warnings.length ? `；${result.warnings.join("；")}` : ""),
      );
    },
    onError: (error) => setIngestMessage(`建档失败：${String(error)}`),
  });

  const openDrawer = async (docId: string, title: string) => {    setDrawer({ docId, title });
    setDrawerLoading(true);
    setAnnotations([]);
    try {
      await extractDocument(docId).catch(() => undefined);
      const payload = await getDocumentAnnotations(docId);
      setAnnotations(payload.items);
    } finally {
      setDrawerLoading(false);
    }
  };

  const overall = report ? overallMeta(report.overall) : null;
  const canReview = roleCanReviewM3(role);
  const awaiting = run.status === "awaiting_review";
  const invoiceDocId = report?.documents?.invoice ?? null;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">M3 合同 / PO / 发票三单匹配</h1>
          <p className="mt-1 max-w-3xl text-[13px] leading-6 text-[var(--text-secondary)]">
            LangGraph 图实时运行：对齐 → 容差 → 差异判定 → HITL 财务放行 → 单事务核销。
            对齐表点行可打开发票 PDF 抽屉，查看抽取字段的 bbox 高亮。
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
          <div>
            <h2 className="text-sm font-semibold">上传单据 PDF</h2>
            <p className="mt-1 text-[11px] text-[var(--text-muted)]">
              裸二进制上传（sha256 去重），可选关联发票号/PO 号；发票上传后可直接抽取并建档，M3 按发票号匹配。
            </p>
          </div>
          <label className="flex flex-col gap-1 text-[12px] text-[var(--text-secondary)]">
            类型
            <select
              className="rounded-md border border-[var(--border-default)] px-2 py-1.5 text-[13px]"
              value={uploadDocType}
              onChange={(event) => setUploadDocType(event.target.value)}
            >
              <option value="invoice">invoice</option>
              <option value="po">po</option>
              <option value="contract">contract</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[12px] text-[var(--text-secondary)]">
            关联单号（可选）
            <input
              className="w-44 rounded-md border border-[var(--border-default)] px-2 py-1.5 text-[13px]"
              value={uploadLinkedNo}
              onChange={(event) => setUploadLinkedNo(event.target.value)}
              placeholder="INV-... / PO-..."
            />
          </label>
          <label className="flex flex-col gap-1 text-[12px] text-[var(--text-secondary)]">
            PDF 文件
            <input
              type="file"
              accept="application/pdf,.pdf"
              className="text-[12px]"
              onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <button
            type="button"
            disabled={upload.isPending || !uploadFile}
            onClick={() => upload.mutate()}
            className="rounded-md bg-[var(--brand-primary)] px-3 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {upload.isPending ? "上传中…" : "上传"}
          </button>
          {uploadMessage && <span className="text-[11px] text-[var(--text-secondary)]">{uploadMessage}</span>}
          {uploadedDoc?.doc_type === "invoice" && (
            <button
              type="button"
              disabled={ingest.isPending}
              onClick={() => ingest.mutate()}
              className="rounded-md border border-[var(--brand-primary)] px-3 py-2 text-[13px] font-medium text-[var(--brand-primary)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              {ingest.isPending ? "抽取建档中…" : "抽取并建档为发票"}
            </button>
          )}
          {ingestMessage && <span className="text-[11px] text-[var(--text-secondary)]">{ingestMessage}</span>}
        </div>
      </section>

      <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[12px] text-[var(--text-secondary)]">
            PO 号
            <input
              className="w-44 rounded-md border border-[var(--border-default)] px-2 py-1.5 text-[13px]"
              value={poNo}
              onChange={(event) => setPoNo(event.target.value)}
              disabled={Boolean(runId)}
            />
          </label>
          <label className="flex flex-col gap-1 text-[12px] text-[var(--text-secondary)]">
            发票号（多张用逗号/空格分隔）
            <input
              className="w-64 rounded-md border border-[var(--border-default)] px-2 py-1.5 text-[13px]"
              value={invoiceNo}
              onChange={(event) => setInvoiceNo(event.target.value)}
              disabled={Boolean(runId)}
              placeholder="INV-2026-3000, INV-2026-3001"
            />
          </label>
          <button
            type="button"
            disabled={start.isPending || !poNo.trim() || parseInvoiceNos(invoiceNo).length === 0}
            onClick={() => start.mutate()}
            className="rounded-md bg-[var(--brand-primary)] px-3 py-2 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {start.isPending ? "启动中…" : "启动 M3 匹配"}
          </button>
          {runId && (
            <button
              type="button"
              onClick={() => {
                setRunId(null);
                setDrawer(null);
              }}
              className="text-[13px] text-[var(--text-secondary)] underline-offset-2 hover:underline"
            >
              关闭本次运行
            </button>
          )}
          <span className="text-[12px] text-[var(--text-muted)]">
            演示数据：{DEFAULT_PO} / {DEFAULT_INVOICE}（可改）
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

      {report && overall && (
        <>
          <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span
                  className={`rounded-md border px-2.5 py-1 text-[12px] font-medium ${overallToneClass(overall.tone)}`}
                >
                  {overall.label}
                </span>
                <span className="font-medium">
                  {report.po_no} ↔ {(report.invoice_nos ?? [report.invoice_no]).join("、")}
                  {report.multi_invoice ? `（${report.invoice_nos?.length ?? 1} 张）` : ""}
                </span>
                <span className="text-[var(--text-muted)]">
                  PO {formatMoney(report.po_total)} / 发票 {formatMoney(report.invoice_total)}
                </span>
                {report.invoices?.map((invoice) => (
                  <span
                    key={invoice.invoice_no}
                    className="rounded-md border border-[var(--border-default)] bg-[var(--bg-subtle)] px-2 py-1 text-[11px]"
                  >
                    {invoice.invoice_no} · {formatMoney(invoice.amount_tax_incl)}
                  </span>
                ))}
                {report.proposed_tail !== undefined && Math.abs(report.proposed_tail) >= 0.005 && (
                  <span className="rounded-md border border-[var(--border-default)] bg-[var(--bg-subtle)] px-2 py-1 text-[12px]">
                    尾差 {formatMoney(report.proposed_tail)}
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
                  {decide.isPending ? "提交中…" : "放行核销"}
                </button>
                <button
                  type="button"
                  disabled={!awaiting || !canReview || decide.isPending}
                  onClick={() => decide.mutate("reject")}
                  className="rounded-md border border-[var(--border-default)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-secondary)] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  挂起
                </button>
              </div>
            </div>
            {!canReview && awaiting && (
              <p className="mt-2 text-[12px] text-[var(--semantic-warning)]">
                当前角色 {role} 无 M3 放行权限，请在右上角切换为 finance（或 admin）。
              </p>
            )}
            {decisionError && (
              <p className="mt-2 text-[12px] text-[var(--semantic-danger)]">决策失败：{decisionError}</p>
            )}
            {report.summary && <p className="mt-2 text-[12px] text-[var(--text-muted)]">{report.summary}</p>}
          </section>

          <section className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4 xl:col-span-2">
              <h2 className="text-sm font-semibold">行项目对齐（匈牙利指派）</h2>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[720px] border-collapse text-[12px]">
                  <thead>
                    <tr className="border-b border-[var(--border-default)] text-left text-[var(--text-muted)]">
                      <th className="px-2 py-1.5">PO 行</th>
                      <th className="px-2 py-1.5">发票行</th>
                      <th className="px-2 py-1.5">方法</th>
                      <th className="px-2 py-1.5 text-right">拟核销</th>
                      <th className="px-2 py-1.5 text-right">剩余</th>
                      <th className="px-2 py-1.5">状态</th>
                      <th className="px-2 py-1.5">文档</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.key} className="border-b border-[var(--border-default)] align-top">
                        <td className="px-2 py-2">
                          <div className="font-medium">{row.po?.item_desc ?? "—"}</div>
                          <div className="text-[var(--text-muted)]">
                            {row.po
                              ? `#${row.po.line_no} · ${formatQty(row.po.qty_ordered)} 件 · ${formatMoney(row.po.unit_price_tax_excl)}`
                              : ""}
                            {row.po?.qty_received != null ? ` · 收货 ${formatQty(row.po.qty_received)}` : ""}
                          </div>
                        </td>
                        <td className="px-2 py-2">
                          <div className="font-medium">{row.invoice?.item_desc ?? "—"}</div>
                          <div className="text-[var(--text-muted)]">
                            {row.invoice
                              ? `#${row.invoice.line_no} · ${formatQty(row.invoice.qty)} 件 · ${formatMoney(row.invoice.unit_price_tax_excl)}`
                              : ""}
                          </div>
                        </td>
                        <td className="px-2 py-2">{row.match?.method ?? "—"}</td>
                        <td className="px-2 py-2 text-right">{formatQty(row.match?.consumed_qty)}</td>
                        <td className="px-2 py-2 text-right">{formatQty(row.match?.remaining_qty)}</td>
                        <td className="px-2 py-2">{row.match?.status ?? "unmatched"}</td>
                        <td className="px-2 py-2">
                          {(() => {
                            const docId =
                              report.invoices?.find((item) => item.invoice_no === row.invoiceNo)?.doc_id ??
                              (row.invoiceNo === report.invoice_no ? invoiceDocId : null);
                            return docId ? (
                              <button
                                type="button"
                                onClick={() => void openDrawer(docId, `发票 ${row.invoiceNo ?? ""}`)}
                                className="text-[12px] text-[var(--brand-primary)] underline-offset-2 hover:underline"
                              >
                                查看发票
                              </button>
                            ) : null;
                          })()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
              <h2 className="text-sm font-semibold">差异清单（{report.diffs.length}）</h2>
              <ul className="mt-3 space-y-2">
                {report.diffs.length === 0 && (
                  <li className="text-[12px] text-[var(--text-muted)]">无差异，三单一致。</li>
                )}
                {report.diffs.map((diff, index) => {
                  const meta = diffMeta(diff.diff_type);
                  return (
                    <li
                      key={`${diff.diff_type}-${index}`}
                      className={`rounded-lg border px-3 py-2 text-[12px] ${severityClass(diff.severity)}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">
                          {diff.invoice_no ? `${diff.invoice_no} · ` : ""}
                          {meta.label} · {diff.diff_type}
                        </span>
                        <span className="text-[11px]">Δ {formatDelta(diff)}</span>
                      </div>
                      <div className="mt-1 text-[11px] text-[var(--text-secondary)]">
                        期望 {diff.expected ?? "—"} / 实际 {diff.actual ?? "—"} · {diff.rule_ref}
                      </div>
                      {diff.suggestion && <div className="mt-1 text-[11px]">{diff.suggestion}</div>}
                    </li>
                  );
                })}
              </ul>
            </div>
          </section>
        </>
      )}

      {runId && isTerminal(run.status) && !report && (
        <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4 text-[13px] text-[var(--text-secondary)]">
          运行已结束（{run.status}），但未取到报告。可查看可观测页或重新运行。
        </section>
      )}

      {drawer && (
        <PdfDrawer
          docId={drawer.docId}
          title={drawer.title}
          annotations={annotations}
          loading={drawerLoading}
          onClose={() => setDrawer(null)}
        />
      )}
    </div>
  );
}

