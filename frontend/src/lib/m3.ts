/** M3 匹配报告的 UI 映射层（纯函数，可单测；Spec §9.4 页面 4）。 */

export type Overall = "pass" | "release_with_note" | "hold" | string;

export interface M3Diff {
  diff_type: string;
  severity: "high" | "medium" | "low" | string;
  po_line_no: number | null;
  invoice_line_no: number | null;
  invoice_no?: string | null;
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
  invoice_no?: string | null;
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

export interface M3Invoice {
  invoice_no: string;
  invoice_id?: string;
  invoice_date?: string | null;
  amount_tax_incl?: number | null;
  doc_id?: string | null;
  lines: M3InvoiceLine[];
}

export interface M3Documents {
  po?: string | null;
  invoice?: string | null;
  invoices?: Array<string | null>;
  contract?: string | null;
}

export interface M3Report {
  module: string;
  graph_version?: string;
  po_no: string;
  invoice_no: string;
  invoice_nos?: string[];
  invoices?: M3Invoice[];
  multi_invoice?: boolean;
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
  invoiceNo: string | null;
  match: M3LineMatch | null;
  po: M3PoLine | null;
  invoice: M3InvoiceLine | null;
}

/** 发票号输入：逗号/中文逗号/空格分隔，去重保序。 */
export function parseInvoiceNos(input: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const part of input.split(/[,，\s]+/)) {
    const value = part.trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    result.push(value);
  }
  return result;
}

/** 对齐表行：按 line_matches 连接 PO / 各发票行；未匹配发票行补在末尾。 */
export function alignRows(report: M3Report): AlignRow[] {
  const poByNo = new Map((report.po_lines ?? []).map((line) => [line.line_no, line]));
  const invoices =
    report.invoices && report.invoices.length > 0
      ? report.invoices
      : [{ invoice_no: report.invoice_no, lines: report.invoice_lines ?? [] }];
  const invoiceLineMap = new Map<string, Map<number, M3InvoiceLine>>();
  for (const invoice of invoices) {
    invoiceLineMap.set(invoice.invoice_no, new Map(invoice.lines.map((line) => [line.line_no, line])));
  }
  const fallbackInvoice = invoices[0]?.invoice_no ?? report.invoice_no;
  const rows: AlignRow[] = report.line_matches.map((match) => {
    const invoiceNo = match.invoice_no ?? fallbackInvoice;
    const invoiceLines = invoiceLineMap.get(invoiceNo);
    return {
      key: `match-${invoiceNo}-${match.po_line_no}-${match.invoice_line_no ?? "x"}`,
      invoiceNo,
      match,
      po: poByNo.get(match.po_line_no) ?? null,
      invoice:
        match.invoice_line_no != null ? (invoiceLines?.get(match.invoice_line_no) ?? null) : null,
    };
  });
  const matchedKeys = new Set(
    report.line_matches.map((match) => `${match.invoice_no ?? fallbackInvoice}:${match.invoice_line_no}`),
  );
  for (const invoice of invoices) {
    for (const line of invoice.lines) {
      if (!matchedKeys.has(`${invoice.invoice_no}:${line.line_no}`)) {
        rows.push({
          key: `invoice-${invoice.invoice_no}-${line.line_no}`,
          invoiceNo: invoice.invoice_no,
          match: null,
          po: null,
          invoice: line,
        });
      }
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
