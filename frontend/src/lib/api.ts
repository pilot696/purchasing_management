/** API 客户端（P0 手写；W4 由 openapi-typescript 生成后替换，Spec §8.5）。 */

import type { EventType } from "./events";

export type DemoRole = "requester" | "buyer" | "finance" | "admin";
export type Module = "m1" | "m2" | "m3";

const ROLE_KEY = "procurement.demoRole";

function loadRole(): DemoRole {
  const saved = typeof localStorage !== "undefined" ? localStorage.getItem(ROLE_KEY) : null;
  return (saved as DemoRole | null) ?? "buyer";
}

let demoRole: DemoRole = loadRole();

export function getDemoRole(): DemoRole {
  return demoRole;
}

export function setDemoRole(role: DemoRole): void {
  demoRole = role;
  if (typeof localStorage !== "undefined") localStorage.setItem(ROLE_KEY, role);
}

function headers(extra: Record<string, string> = {}): HeadersInit {
  return { "Content-Type": "application/json", "X-Demo-Role": demoRole, ...extra };
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

async function asError(response: Response): Promise<Error> {
  let code = `HTTP_${response.status}`;
  let message = response.statusText || "请求失败";
  try {
    const body = (await response.json()) as ApiErrorBody;
    code = body.error.code;
    message = body.error.message;
  } catch {
    /* 非 JSON 错误体，保持兜底文案 */
  }
  const err = new Error(`${code}: ${message}`) as Error & { code: string };
  err.code = code;
  return err;
}

export interface StartRunResponse {
  run_id: string;
  thread_id: string;
  status: string;
  sse_url: string;
}

export async function startRun(module: Module, subject: Record<string, unknown>): Promise<StartRunResponse> {
  const response = await fetch("/api/v1/runs", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ module, subject }),
  });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as StartRunResponse;
}

export interface ResumePayload {
  review_id: string;
  decision: "accept" | "reject" | "edit";
  edits?: unknown[];
  note?: string;
}

export async function resumeRun(runId: string, payload: ResumePayload): Promise<{ status: string }> {
  const response = await fetch(`/api/v1/runs/${runId}/resume`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as { status: string };
}

export interface Health {
  status: string;
  db: boolean;
  env: string;
  version: string;
}

export async function getHealth(): Promise<Health> {
  const response = await fetch("/healthz");
  if (!response.ok) throw await asError(response);
  return (await response.json()) as Health;
}

export const EVENT_TYPES: EventType[] = [
  "run.start",
  "node.start",
  "node.end",
  "tool.call",
  "tool.result",
  "finding",
  "delta",
  "interrupt",
  "run.end",
  "error",
  "heartbeat",
];

// --------------------------------------------------------------------------- 文档（M3 PDF 抽屉）

export interface DocumentAnnotation {
  field_name: string;
  line_ref: string | null;
  page_no: number;
  x0: number | null;
  y0: number | null;
  x1: number | null;
  y1: number | null;
  text_raw: string | null;
}

export async function getDocumentAnnotations(
  docId: string,
): Promise<{ items: DocumentAnnotation[]; total: number }> {
  const response = await fetch(`/api/v1/documents/${docId}/annotations`, { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as { items: DocumentAnnotation[]; total: number };
}

export interface ExtractionSummary {
  status: "ok" | "needs_review" | "failed" | string;
  extractor: string;
  annotations: number;
  issues: Array<{ code: string; message: string; severity: string }>;
}

export async function extractDocument(
  docId: string,
): Promise<{ doc_id: string; record_id: number; summary: ExtractionSummary }> {
  const response = await fetch(`/api/v1/documents/${docId}/extract`, { method: "POST", headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as { doc_id: string; record_id: number; summary: ExtractionSummary };
}

export function documentPageUrl(docId: string, page = 1): string {
  return `/api/v1/documents/${docId}/page.png?page=${page}`;
}

export interface RunSnapshot {
  run: {
    run_id?: string;
    module?: string;
    status?: string;
    last_seq?: number;
  };
  review: Record<string, unknown> | null;
  artifact: Record<string, unknown> | null;
}

export async function getRunSnapshot(runId: string): Promise<RunSnapshot> {
  const response = await fetch(`/api/v1/runs/${runId}`, { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as RunSnapshot;
}

// --------------------------------------------------------------------------- 运行历史 / 复核收件箱 / Trace / 规则 / 可观测

export interface RunListItem {
  id: string;
  module: Module;
  status: string;
  subject_ref: Record<string, unknown>;
  last_seq: number;
  tokens_in: number;
  tokens_out: number;
  cost_cny: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error: { code?: string; message?: string } | null;
  pending_review_id: string | null;
  pending_review_kind: string | null;
}

export interface RunListResponse {
  items: RunListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface RunListParams {
  module?: Module;
  status?: string;
  limit?: number;
  offset?: number;
}

export async function listRuns(params: RunListParams = {}): Promise<RunListResponse> {
  const query = new URLSearchParams();
  if (params.module) query.set("module", params.module);
  if (params.status) query.set("status", params.status);
  query.set("limit", String(params.limit ?? 20));
  query.set("offset", String(params.offset ?? 0));
  const response = await fetch(`/api/v1/runs?${query.toString()}`, { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as RunListResponse;
}

export interface ReviewInboxItem {
  id: string;
  run_id: string;
  kind: string;
  status: string;
  created_at: string | null;
  run_module: Module;
  run_status: string;
  subject_ref: Record<string, unknown>;
  verdict: string | null;
  summary: string | null;
  details: Record<string, unknown>;
}

export async function listReviews(): Promise<ReviewInboxItem[]> {
  const response = await fetch("/api/v1/reviews", { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as ReviewInboxItem[];
}

export interface TraceEvent {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface LlmCallItem {
  id: number;
  step: string;
  provider: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  cost_cny: number;
  latency_ms: number;
  retry_no: number;
  cache_hit: boolean;
  schema_repair: number;
  ok: boolean;
  error_code: string | null;
  created_at: string | null;
}

export interface RunTrace {
  run: RunListItem;
  events: TraceEvent[];
  llm_calls: LlmCallItem[];
  artifacts: Array<{ id: string; module: string; kind: string }>;
}

export async function getRunTrace(runId: string): Promise<RunTrace> {
  const response = await fetch(`/api/v1/runs/${runId}/trace`, { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as RunTrace;
}

export interface ObservabilitySummary {
  runs_total: number;
  by_status: Record<string, number>;
  by_module: Record<string, number>;
  success_rate: number;
  pending_reviews: number;
  tokens_in: number;
  tokens_out: number;
  cost_cny: number;
  p50_duration_ms: number | null;
  p95_duration_ms: number | null;
  llm_calls: number;
  avg_llm_latency_ms: number | null;
  cache_hit_rate: number | null;
}

export async function getObservabilitySummary(): Promise<ObservabilitySummary> {
  const response = await fetch("/api/v1/observability/summary", { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as ObservabilitySummary;
}

export interface EvalSnapshot {
  available: boolean;
  generated_at: string | null;
  llm_mode: string | null;
  seed: number | null;
  data: Record<string, unknown>;
}

export async function getEvalSnapshot(): Promise<EvalSnapshot> {
  const response = await fetch("/api/v1/observability/evals", { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as EvalSnapshot;
}

export interface CategoryItem {
  code: string;
  name: string;
  version: number;
  requires_gr: boolean;
  required_slots: string[];
}

export interface RuleItem {
  id: string;
  category_code: string;
  category_name: string;
  code: string;
  type: string;
  severity: string;
  field_path: string | null;
  expression: Record<string, unknown>;
  message_template: string;
  policy_clause_ref: string | null;
  enabled: boolean;
  version: number;
}

export interface ToleranceItem {
  id: string;
  category_code: string | null;
  diff_type: string;
  abs_tol: number;
  rel_tol: number;
  action: string;
  priority: number;
  enabled: boolean;
}

export interface RuleCatalog {
  categories: CategoryItem[];
  rules: RuleItem[];
  tolerances: ToleranceItem[];
}

export async function getRules(category?: string): Promise<RuleCatalog> {
  const query = category ? `?category=${encodeURIComponent(category)}` : "";
  const response = await fetch(`/api/v1/rules${query}`, { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as RuleCatalog;
}


export interface SupplierEvalItem {
  id: string;
  run_id: string;
  mode: string;
  total: number | null;
  verdict: string;
  confidence: string;
  gate_hits: Array<Record<string, unknown>>;
  dimensions: Array<Record<string, unknown>>;
  weight_version: string | null;
  created_at: string | null;
}

export interface SupplierListItem {
  code: string;
  name: string;
  status: string;
  category_scope: string[];
  onboarded_at: string | null;
  risk_flags: Record<string, unknown>;
  latest_eval: SupplierEvalItem | null;
  eval_count: number;
}

export interface SupplierDetail extends SupplierListItem {
  evals: SupplierEvalItem[];
}

export async function listSuppliers(q?: string): Promise<{ items: SupplierListItem[]; total: number }> {
  const query = q ? `?q=${encodeURIComponent(q)}` : "";
  const response = await fetch(`/api/v1/suppliers${query}`, { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as { items: SupplierListItem[]; total: number };
}

export async function getSupplier(code: string): Promise<SupplierDetail> {
  const response = await fetch(`/api/v1/suppliers/${encodeURIComponent(code)}`, { headers: headers() });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as SupplierDetail;
}


export interface UploadedDoc {
  id: string;
  doc_type: string;
  sha256: string;
  page_count: number | null;
  linked_kind: string | null;
  linked_id: string | null;
  extract_url: string;
  file_url: string;
  duplicate: boolean;
}

export async function uploadDocument(file: File, docType: string, linkedNo?: string): Promise<UploadedDoc> {
  const params = new URLSearchParams({ doc_type: docType });
  if (linkedNo) params.set("linked_no", linkedNo);
  const response = await fetch(`/api/v1/documents/upload?${params.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/pdf", "X-Demo-Role": demoRole },
    body: file,
  });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as UploadedDoc;
}

export interface IngestInvoiceResult {
  created: boolean;
  already_linked: boolean;
  invoice: {
    id: string;
    invoice_no: string;
    invoice_code: string | null;
    invoice_date: string | null;
    amount_tax_excl: number;
    tax_amount: number;
    amount_tax_incl: number;
    supplier_id: string | null;
    doc_id: string | null;
    status: string;
  };
  lines: number;
  warnings: string[];
  doc_id: string;
  extractor: string | null;
  latency_ms: number;
  summary: unknown;
}

export async function ingestDocument(docId: string, invoiceNo?: string): Promise<IngestInvoiceResult> {
  const query = invoiceNo ? `?invoice_no=${encodeURIComponent(invoiceNo)}` : "";
  const response = await fetch(`/api/v1/documents/${encodeURIComponent(docId)}/ingest${query}`, {
    method: "POST",
    headers: { "X-Demo-Role": demoRole },
  });
  if (!response.ok) throw await asError(response);
  return (await response.json()) as IngestInvoiceResult;
}
