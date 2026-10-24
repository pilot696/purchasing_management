/** M2 供应商评估报告的 UI 映射层（纯函数，可单测；Spec §9.4 页面 3）。 */

export type M2Verdict = "approve" | "conditional" | "reject" | "frozen" | string;

export interface M2Dimension {
  dimension: string;
  score: number;
  weight: number;
  raw_metrics: Record<string, number | boolean | null>;
  sample_size: number;
  note?: string | null;
}

export interface M2Gate {
  code: string;
  message: string;
  evidence_refs: string[];
  veto: boolean;
}

export interface M2RiskItem {
  dimension: string;
  claim: string;
  evidence_refs: string[];
  severity_hint: string;
}

export interface M2LlmReview {
  risk_summary: string;
  top_risks: M2RiskItem[];
  conditions: string[];
  insufficient_evidence: string[];
  reviewer_memo: string;
}

export interface M2Report {
  module: string;
  graph_version?: string;
  supplier_id?: string;
  supplier_code?: string;
  supplier_name?: string;
  mode?: string;
  category_code?: string | null;
  dimensions: M2Dimension[];
  total: number;
  verdict: M2Verdict;
  gate_hits: M2Gate[];
  confidence: string;
  confidence_detail?: Record<string, number | string>;
  conditions: string[];
  llm_review: M2LlmReview | null;
  llm_status: string;
  llm_error?: string | null;
  llm_dropped_refs?: number;
  llm_dropped_refs_note?: string | null;
  sensitivity?: { flip_rate?: number; most_sensitive?: string; samples?: number };
  weight_version?: string;
  evidence_pack_ids?: string[];
  cold_start?: boolean;
  summary?: string;
  requires_review?: boolean;
}

export const VERDICT_META: Record<string, { label: string; tone: "pass" | "note" | "block" }> = {
  approve: { label: "准入通过", tone: "pass" },
  conditional: { label: "有条件准入", tone: "note" },
  reject: { label: "淘汰", tone: "block" },
  frozen: { label: "冻结", tone: "block" },
};

export const DIMENSION_LABELS: Record<string, string> = {
  price: "价格竞争力",
  delivery: "交付履约",
  quality: "质量",
  service: "服务响应",
  compliance: "合规资质",
};

export const GATE_LABELS: Record<string, string> = {
  G1_LICENSE_INVALID: "G1 营业执照失效",
  G2_SCOPE_MISMATCH: "G2 经营范围不匹配（人工确认）",
  G3_DISHONEST: "G3 失信/黑名单",
  G4_DEFAULT: "G4 重大违约",
  G5_TAX_ABNORMAL: "G5 税务异常",
  G6_SECURITY_CERT: "G6 安全资质缺失（可补交）",
};

export const METRIC_LABELS: Record<string, string> = {
  price_index: "价格指数（报价/p50）",
  p50_reference: "参考 p50",
  otd: "准时交付率",
  avg_delay_days: "平均延迟天数",
  partial_ship_rate: "分批到货率",
  qc_pass_rate: "质检通过率",
  defect_rate: "缺陷率",
  first_response_hours: "平均首次响应(h)",
  resolve_hours: "平均解决时长(h)",
  escalation_rate: "升级率",
  cert_expiring: "证照临期",
  scope_partial: "范围部分覆盖",
  missing_security_cert: "缺安全资质",
  abnormal_flag: "异常标记",
};

export function verdictMeta(verdict: M2Verdict): { label: string; tone: "pass" | "note" | "block" } {
  return VERDICT_META[verdict] ?? { label: verdict, tone: "note" };
}

export function verdictToneClass(tone: "pass" | "note" | "block"): string {
  if (tone === "pass") return "bg-green-50 text-[var(--semantic-success)] border-green-200";
  if (tone === "note") return "bg-amber-50 text-[var(--semantic-warning)] border-amber-200";
  return "bg-red-50 text-[var(--semantic-danger)] border-red-200";
}

export function dimensionLabel(dimension: string): string {
  return DIMENSION_LABELS[dimension] ?? dimension;
}

export function gateLabel(code: string): string {
  return GATE_LABELS[code] ?? code;
}

export function confidenceLabel(confidence: string): string {
  if (confidence === "high") return "高置信度";
  if (confidence === "medium") return "中置信度";
  return "低置信度";
}

export function llmStatusLabel(status: string | undefined): string {
  if (status === "ok") return "LLM 复核完成";
  if (status === "skipped_gate_hit") return "闸门命中，跳过 LLM（省 token）";
  if (status === "unavailable") return "LLM 未配置（离线模式，无叙述）";
  return status ?? "未知";
}

export function severityClass(severity: string | undefined): string {
  if (severity === "high") return "border-red-200 bg-red-50 text-[var(--semantic-danger)]";
  if (severity === "low") return "border-[var(--border-default)] bg-[var(--bg-subtle)] text-[var(--text-secondary)]";
  return "border-amber-200 bg-amber-50 text-[var(--semantic-warning)]";
}

export function asM2Report(value: unknown): M2Report | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<M2Report>;
  if (candidate.module !== "m2" && !candidate.supplier_code) return null;
  if (typeof candidate.supplier_code !== "string") return null;
  return {
    module: "m2",
    graph_version: candidate.graph_version,
    supplier_id: candidate.supplier_id,
    supplier_code: candidate.supplier_code,
    supplier_name: candidate.supplier_name,
    mode: candidate.mode,
    category_code: candidate.category_code,
    dimensions: Array.isArray(candidate.dimensions) ? candidate.dimensions : [],
    total: Number(candidate.total ?? 0),
    verdict: candidate.verdict ?? "reject",
    gate_hits: Array.isArray(candidate.gate_hits) ? candidate.gate_hits : [],
    confidence: candidate.confidence ?? "low",
    confidence_detail: candidate.confidence_detail,
    conditions: Array.isArray(candidate.conditions) ? candidate.conditions : [],
    llm_review: candidate.llm_review ?? null,
    llm_status: candidate.llm_status ?? "unavailable",
    llm_error: candidate.llm_error,
    llm_dropped_refs: candidate.llm_dropped_refs,
    llm_dropped_refs_note: candidate.llm_dropped_refs_note,
    sensitivity: candidate.sensitivity,
    weight_version: candidate.weight_version,
    evidence_pack_ids: candidate.evidence_pack_ids,
    cold_start: candidate.cold_start,
    summary: candidate.summary,
    requires_review: candidate.requires_review,
  };
}

/** 五维雷达多边形顶点（SVG 坐标，中心 (cx,cy)，score 0~100）。 */
export function radarPoints(
  dimensions: Array<{ dimension: string; score: number }>,
  { radius = 80, cx = 110, cy = 110 }: { radius?: number; cx?: number; cy?: number } = {},
): { points: string; axes: Array<{ x: number; y: number; label: string }> } {
  const count = dimensions.length || 5;
  const axes = dimensions.map((dimension, index) => {
    const angle = (Math.PI * 2 * index) / count - Math.PI / 2;
    const ratio = Math.max(0, Math.min(100, dimension.score)) / 100;
    return {
      x: cx + Math.cos(angle) * radius * ratio,
      y: cy + Math.sin(angle) * radius * ratio,
      label: dimension.dimension,
      labelX: cx + Math.cos(angle) * (radius + 18),
      labelY: cy + Math.sin(angle) * (radius + 18),
    } as { x: number; y: number; label: string; labelX?: number; labelY?: number };
  });
  return {
    points: axes.map((axis) => `${axis.x.toFixed(2)},${axis.y.toFixed(2)}`).join(" "),
    axes: axes.map((axis) => ({ x: axis.x, y: axis.y, label: axis.label })),
  };
}

export function radarLabelPoints(
  dimensions: Array<{ dimension: string; score: number }>,
  options: { radius?: number; cx?: number; cy?: number } = {},
): Array<{ x: number; y: number; label: string }> {
  const radius = options.radius ?? 80;
  const cx = options.cx ?? 110;
  const cy = options.cy ?? 110;
  const count = dimensions.length || 5;
  return dimensions.map((dimension, index) => {
    const angle = (Math.PI * 2 * index) / count - Math.PI / 2;
    return {
      x: cx + Math.cos(angle) * (radius + 20),
      y: cy + Math.sin(angle) * (radius + 20),
      label: dimension.dimension,
    };
  });
}

export function formatMetricValue(value: number | boolean | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key;
}

export function roleCanReviewM2(role: string): boolean {
  return role === "buyer" || role === "admin";
}
