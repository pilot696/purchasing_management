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
