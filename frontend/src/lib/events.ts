/**
 * SSE 事件 -> UI 状态归约器（Spec §9.6）。
 * 纯函数，不依赖 React，便于单测；12+ 条用例见 events.test.ts。
 */

export type Severity = "high" | "medium" | "low" | "info";

export type RunStatus =
  | "queued"
  | "running"
  | "awaiting_review"
  | "succeeded"
  | "failed"
  | "cancelled";

export type EventType =
  | "run.start"
  | "node.start"
  | "node.end"
  | "tool.call"
  | "tool.result"
  | "finding"
  | "delta"
  | "interrupt"
  | "run.end"
  | "error"
  | "heartbeat";

export interface Finding {
  rule_id?: string | null;
  type: string;
  severity: Severity;
  field?: string | null;
  message: string;
  evidence?: Array<{ kind: string; ref: string; snippet?: string | null }>;
}

export interface FieldPatch {
  op: "replace" | "add" | "remove";
  path: string;
  value?: unknown;
  source?: string;
}

export interface ReviewTask {
  review_id: string;
  kind: string;
  title?: string;
  patch?: FieldPatch[];
  questions?: string[];
  editable?: boolean;
}

export interface RunEvent {
  /** run_event.seq；合成事件（如 SSE 断线）用 -1 */
  id: number;
  type: EventType;
  data: Record<string, unknown>;
}

export interface RunUiState {
  status: RunStatus;
  lastSeq: number;
  nodes: Record<string, { state: "start" | "end"; durationMs?: number }>;
  findings: Finding[];
  narratives: Record<string, string>;
  review: ReviewTask | null;
  error: { code: string; message: string } | null;
}

export const TERMINAL_STATUS: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "succeeded",
  "failed",
  "cancelled",
]);

export function initialState(): RunUiState {
  return {
    status: "queued",
    lastSeq: 0,
    nodes: {},
    findings: [],
    narratives: {},
    review: null,
    error: null,
  };
}

export function isTerminal(status: RunStatus): boolean {
  return TERMINAL_STATUS.has(status);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function reduceEvent(state: RunUiState, event: RunEvent): RunUiState {
  // 断线重连会重复回放事件：按 seq 幂等去重（Spec §9.6）
  if (event.id >= 0 && event.id <= state.lastSeq) {
    return state;
  }
  const next: RunUiState = { ...state, lastSeq: event.id >= 0 ? event.id : state.lastSeq };

  switch (event.type) {
    case "run.start":
      next.status = "running";
      break;
    case "node.start": {
      const node = str(event.data.node);
      if (node) next.nodes = { ...next.nodes, [node]: { state: "start" } };
      break;
    }
    case "node.end": {
      const node = str(event.data.node);
      if (node) {
        const durationMs = Number(event.data.duration_ms ?? 0);
        next.nodes = { ...next.nodes, [node]: { state: "end", durationMs } };
      }
      break;
    }
    case "finding": {
      const raw = event.data.finding ?? event.data;
      if (raw && typeof raw === "object") {
        next.findings = [...next.findings, raw as Finding];
      }
      break;
    }
    case "delta": {
      const channel = str(event.data.channel, "summary");
      const text = str(event.data.text);
      next.narratives = { ...next.narratives, [channel]: (next.narratives[channel] ?? "") + text };
      break;
    }
    case "interrupt": {
      next.review = { ...(event.data as unknown as ReviewTask) };
      next.status = "awaiting_review";
      break;
    }
    case "run.end": {
      next.status = (str(event.data.status, "succeeded") as RunStatus) ?? "succeeded";
      break;
    }
    case "error": {
      next.error = {
        code: str(event.data.code, "UNKNOWN"),
        message: str(event.data.message, "未知错误"),
      };
      break;
    }
    case "heartbeat":
      break;
    default:
      break;
  }
  return next;
}
