/** M1 预检报告的 UI 映射层（纯函数，可单测；Spec §9.4 页面 2）。 */

export type Verdict = "pass" | "needs_info" | "block" | string;
export type Severity = "high" | "medium" | "low" | "info" | string;

export interface M1Evidence {
  kind: string;
  ref: string;
  snippet?: string | null;
}

export interface M1Finding {
  rule_code?: string;
  type: string;
  severity: Severity;
  field?: string | null;
  message: string;
  evidence?: M1Evidence[];
}

export interface M1Patch {
  op: string;
  path: string;
  value: unknown;
  from?: unknown;
  source?: string;
  ref?: string | null;
}

export interface M1Candidate {
  sku_code: string;
  name: string;
  spec_bucket: string;
  score: number;
  sources: string[];
  spec: Record<string, unknown>;
  ref_price_p50?: number | null;
}

export interface M1Item {
  line_no: number;
  item_name_raw: string;
  spec: Record<string, unknown>;
  spec_bucket?: string | null;
  qty: number;
  unit: string;
  candidates: M1Candidate[];
  ambiguous: boolean;
}

export interface M1Report {
  module: string;
  graph_version?: string;
  pr_no: string;
  title?: string;
  category_code?: string;
  verdict: Verdict;
  findings: M1Finding[];
  patches: M1Patch[];
  items: M1Item[];
  budget?: { available: number; estimated: number; over_budget: boolean };
  inventory?: Array<{
    line_no: number;
    item_name: string;
    qty_required: number;
    qty_on_hand: number;
    qty_reserved: number;
    qty_available: number;
    coverage: number;
    suggestion: string;
  }>;
  purchase_history?: Array<{
    line_no: number;
    item_name: string;
    last_po_no: string;
    last_unit_price_tax_excl: number;
    last_qty: number;
    ordered_at: string | null;
    price_delta_vs_current: number | null;
  }>;
  acceptance?: { points: string[]; enforced: boolean; status: string; note: string };
  summary?: string;
  requires_review?: boolean;
  questions?: string[];
  review_kind?: string;
}

export const VERDICT_META: Record<string, { label: string; tone: "pass" | "note" | "block" }> = {
  pass: { label: "通过", tone: "pass" },
  needs_info: { label: "需补充信息", tone: "note" },
  block: { label: "拦截", tone: "block" },
};

export const FINDING_TYPE_LABEL: Record<string, string> = {
  required: "必填缺失",
  enum: "枚举非法",
  cross_field: "跨字段冲突",
  policy_ref: "政策违规",
  stat_outlier: "统计异常",
  ambiguity: "规格歧义",
};

export const PATCH_SOURCE_LABEL: Record<string, string> = {
  catalog_sku: "目录 SKU",
  requester_profile: "申请人档案",
  department_budget: "部门预算",
  department_profile: "部门信息",
  manual_required: "需人工",
};

export function verdictMeta(verdict: Verdict): { label: string; tone: "pass" | "note" | "block" } {
  return VERDICT_META[verdict] ?? { label: verdict, tone: "note" };
}

export function findingTypeLabel(type: string): string {
  return FINDING_TYPE_LABEL[type] ?? type;
}

export function patchSourceLabel(source?: string): string {
  return (source && PATCH_SOURCE_LABEL[source]) || "自动建议";
}

export function severityClass(severity: Severity): string {
  if (severity === "high") return "bg-red-50 text-[var(--semantic-danger)] border-red-200";
  if (severity === "medium") return "bg-amber-50 text-[var(--semantic-warning)] border-amber-200";
  return "bg-slate-50 text-[var(--text-secondary)] border-[var(--border-default)]";
}

export function verdictToneClass(tone: "pass" | "note" | "block"): string {
  if (tone === "pass") return "bg-green-50 text-[var(--semantic-success)] border-green-200";
  if (tone === "note") return "bg-amber-50 text-[var(--semantic-warning)] border-amber-200";
  return "bg-red-50 text-[var(--semantic-danger)] border-red-200";
}

export function asM1Report(value: unknown): M1Report | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<M1Report>;
  if (candidate.module !== "m1" && !candidate.pr_no) return null;
  if (typeof candidate.pr_no !== "string") return null;
  return {
    module: "m1",
    graph_version: candidate.graph_version,
    pr_no: candidate.pr_no,
    title: candidate.title,
    category_code: candidate.category_code,
    verdict: candidate.verdict ?? "needs_info",
    findings: Array.isArray(candidate.findings) ? candidate.findings : [],
    patches: Array.isArray(candidate.patches) ? candidate.patches : [],
    items: Array.isArray(candidate.items) ? candidate.items : [],
    budget: candidate.budget,
    inventory: candidate.inventory,
    purchase_history: candidate.purchase_history,
    acceptance: candidate.acceptance,
    summary: candidate.summary,
    requires_review: candidate.requires_review,
    questions: candidate.questions,
    review_kind: candidate.review_kind,
  };
}

export function formatPatchValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "∅";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function formatSpecValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return String(value);
}

export function patchPathLabel(path: string): string {
  return path.replace(/^\/items\/(\d+)\//, "行 $1 · ");
}

export function budgetBar(available: number, estimated: number): { percent: number; over: boolean } {
  if (!Number.isFinite(available) || available <= 0) {
    return { percent: 100, over: estimated > 0 };
  }
  return { percent: Math.min(100, Math.max(0, (estimated / available) * 100)), over: estimated > available };
}

export function candidateSourcesText(sources: string[]): string {
  const labels: Record<string, string> = {
    lexical: "词法",
    exact: "精确",
    structured: "规格桶",
  };
  return sources.map((source) => labels[source] ?? source).join(" + ");
}

export function roleCanReviewM1(role: string): boolean {
  return role === "buyer" || role === "admin";
}

export interface PatchSelection {
  checked: boolean;
  value: string;
}

export function initialPatchSelections(patches: M1Patch[]): Record<number, PatchSelection> {
  const selections: Record<number, PatchSelection> = {};
  patches.forEach((patch, index) => {
    selections[index] = {
      checked: true,
      value: patch.value === null || patch.value === undefined ? "" : String(patch.value),
    };
  });
  return selections;
}

function parseEditedValue(original: unknown, text: string): unknown {
  if (typeof original === "number") {
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : original;
  }
  if (typeof original === "boolean") {
    return text === "true";
  }
  return text;
}

export function buildSelectedEdits(
  patches: M1Patch[],
  selections: Record<number, PatchSelection>,
): M1Patch[] {
  return patches
    .map((patch, index) => ({ patch, selection: selections[index] }))
    .filter(({ selection }) => selection?.checked)
    .map(({ patch, selection }) => ({
      ...patch,
      value: parseEditedValue(patch.value, selection?.value ?? ""),
    }));
}
