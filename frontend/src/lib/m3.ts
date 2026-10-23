/** M3 匹配报告的 UI 映射层（纯函数，可单测；Spec §9.4 页面 4）。 */

export type Overall = "pass" | "release_with_note" | "hold" | string;

export interface M3Diff {
  diff_type: string;
  severity: "high" | "medium" | "low" | string;
  po_line_no: number | null;
  invoice_line_no: number | null;
  expected: number | null;
  actual: number | null;
  delta: number | null;
  rule_ref: string;
  suggestion?: string | null;
  detail?: string | null;
}

export interface M3LineMatch {
  po_line_no: number;
  invoice_line_no: number | null;
  method: string;
  score: number;
  consumed_qty: number;
  remaining_qty: number;
  status: string;
  ambiguous?: boolean;
}

export interface M3PoLine {
  line_no: number;
  item_desc: string;
  spec_bucket?: string | null;
  qty_ordered: number;
  qty_received?: number | null;
  qty_invoiced?: number | null;
  unit_price_tax_excl: number;
  tax_rate?: number | null;
  amount_tax_incl?: number | null;
  promised_date?: string | null;
}

export interface M3InvoiceLine {
  line_no: number;
  item_desc: string;
  spec_bucket?: string | null;
  qty: number;
  unit_price_tax_excl?: number | null;
  tax_rate?: number | null;
  amount_tax_excl?: number | null;
  amount_tax_incl?: number | null;
}

export interface M3Documents {
  po?: string | null;
  invoice?: string | null;
  contract?: string | null;
}

export interface M3Report {
  module: string;
  graph_version?: string;
  po_no: string;
  invoice_no: string;
  overall: Overall;
  summaries?: string[];
  diffs: M3Diff[];
  line_matches: M3LineMatch[];
  proposed_tail?: number;
  proposed_changes?: Array<{ target: string; field: string; delta: number }>;
  documents?: M3Documents;
  po_total?: number | null;
  invoice_total?: number | null;
  po_lines?: M3PoLine[];
  invoice_lines?: M3InvoiceLine[];
  summary?: string;
  diff_count?: number;
  requires_gr?: boolean;
}

export const OVERALL_META: Record<string, { label: string; tone: "pass" | "note" | "hold" }> = {
  pass: { label: "可放行", tone: "pass" },
  release_with_note: { label: "放行（留痕）", tone: "note" },
  hold: { label: "挂起", tone: "hold" },
};

export const DIFF_META: Record<string, { label: string; hint: string }> = {
  price: { label: "单价不一致", hint: "发票单价超出容差，建议冲红重开或补签变更" },
  amount: { label: "金额不一致", hint: "合计金额差异无法由行级差异解释" },
  tax_rate: { label: "税率错误", hint: "税率与合同/PO 不一致，硬错误" },
  qty_over: { label: "超发", hint: "发票数量超过可开票上限 min(订购, 收货) − 已开票" },
  date_overdue: { label: "逾期到货", hint: "超过承诺交期且超出 7 天容差" },
  terms_conflict: { label: "付款条款冲突", hint: "合同付款方式与发票备注冲突" },
  tail: { label: "尾差", hint: "≤¥0.05 的尾差，自动平账并留痕" },
};

export function overallMeta(overall: Overall): { label: string; tone: "pass" | "note" | "hold" } {
  return OVERALL_META[overall] ?? { label: overall, tone: "note" };
}

export function diffMeta(diffType: string): { label: string; hint: string } {
  return DIFF_META[diffType] ?? { label: diffType, hint: "" };
}

export function severityClass(severity: string): string {
  if (severity === "high") return "bg-red-50 text-[var(--semantic-danger)] border-red-200";
  if (severity === "medium") return "bg-amber-50 text-[var(--semantic-warning)] border-amber-200";
  return "bg-slate-50 text-[var(--text-secondary)] border-[var(--border-default)]";
}

export function overallToneClass(tone: "pass" | "note" | "hold"): string {
  if (tone === "pass") return "bg-green-50 text-[var(--semantic-success)] border-green-200";
  if (tone === "note") return "bg-amber-50 text-[var(--semantic-warning)] border-amber-200";
  return "bg-red-50 text-[var(--semantic-danger)] border-red-200";
}

export function asM3Report(value: unknown): M3Report | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<M3Report> & { module?: string };
  if (candidate.module !== "m3" && !candidate.po_no) return null;
  if (typeof candidate.po_no !== "string" || typeof candidate.invoice_no !== "string") return null;
  return {
    module: candidate.module ?? "m3",
    graph_version: candidate.graph_version,
    po_no: candidate.po_no,
    invoice_no: candidate.invoice_no,
    overall: candidate.overall ?? "hold",
    diffs: Array.isArray(candidate.diffs) ? candidate.diffs : [],
    line_matches: Array.isArray(candidate.line_matches) ? candidate.line_matches : [],
    proposed_tail: candidate.proposed_tail,
    proposed_changes: candidate.proposed_changes,
    documents: candidate.documents,
    po_total: candidate.po_total,
    invoice_total: candidate.invoice_total,
    po_lines: candidate.po_lines,
    invoice_lines: candidate.invoice_lines,
    summary: candidate.summary,
    diff_count: candidate.diff_count,
    requires_gr: candidate.requires_gr,
  };
}

export interface AlignRow {
  key: string;
  match: M3LineMatch | null;
  po: M3PoLine | null;
  invoice: M3InvoiceLine | null;
}

/** 对齐表行：按 line_matches 连接 PO / 发票行；未匹配发票行补在末尾。 */
export function alignRows(report: M3Report): AlignRow[] {
  const poByNo = new Map((report.po_lines ?? []).map((line) => [line.line_no, line]));
  const invoiceByNo = new Map((report.invoice_lines ?? []).map((line) => [line.line_no, line]));
  const rows: AlignRow[] = report.line_matches.map((match) => ({
    key: `match-${match.po_line_no}-${match.invoice_line_no ?? "x"}`,
    match,
    po: poByNo.get(match.po_line_no) ?? null,
    invoice: match.invoice_line_no != null ? (invoiceByNo.get(match.invoice_line_no) ?? null) : null,
  }));
  const matchedInvoiceNos = new Set(report.line_matches.map((match) => match.invoice_line_no));
  for (const line of report.invoice_lines ?? []) {
    if (!matchedInvoiceNos.has(line.line_no)) {
      rows.push({ key: `invoice-${line.line_no}`, match: null, po: null, invoice: line });
    }
  }
  return rows;
}

export function formatMoney(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `¥${value.toFixed(2)}`;
}

export function formatQty(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function formatDelta(diff: M3Diff): string {
  if (diff.delta == null) return "—";
  const sign = diff.delta > 0 ? "+" : "";
  return `${sign}${diff.delta.toFixed(2)}`;
}

/** bbox（左上原点归一化 0~1）→ 覆盖层 inline style 百分比。 */
export function annotationBoxStyle(box: { x0: number; y0: number; x1: number; y1: number }): {
  left: string;
  top: string;
  width: string;
  height: string;
} {
  const clamp = (value: number) => Math.max(0, Math.min(1, value));
  const pct = (value: number) => `${parseFloat((clamp(value) * 100).toFixed(4))}%`;
  return {
    left: pct(box.x0),
    top: pct(box.y0),
    width: pct(clamp(box.x1) - clamp(box.x0)),
    height: pct(clamp(box.y1) - clamp(box.y0)),
  };
}

export function roleCanReviewM3(role: string): boolean {
  return role === "finance" || role === "admin";
}
