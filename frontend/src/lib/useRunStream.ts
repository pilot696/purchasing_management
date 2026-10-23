/**
 * SSE 订阅 hook：fetch + ReadableStream + 自写解析器。
 * 原生 EventSource 无法携带 X-Demo-Role 请求头（Spec §9.6），因此必须用 fetch。
 */

import { useEffect, useReducer, useRef } from "react";

import { getDemoRole } from "./api";
import { initialState, reduceEvent } from "./events";
import type { RunUiState } from "./events";
import { parseSseChunk } from "./sse";

export function useRunStream(runId: string | null): RunUiState {
  const [state, dispatch] = useReducer(reduceEvent, initialState());
  const lastSeq = useRef(0);

  useEffect(() => {
    if (!runId) return;
    let stopped = false;
    const controller = new AbortController();

    async function connect(): Promise<void> {
      let buffer = "";
      try {
        const response = await fetch(`/api/v1/runs/${runId}/events?after_seq=${lastSeq.current}`, {
          headers: { Accept: "text/event-stream", "X-Demo-Role": getDemoRole() },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(`SSE HTTP ${response.status}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        while (!stopped) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parsed = parseSseChunk(buffer);
          buffer = parsed.rest;
          for (const event of parsed.events) {
            lastSeq.current = Math.max(lastSeq.current, event.id);
            dispatch(event);
          }
        }
      } catch (error) {
        if (!stopped) {
          dispatch({
            id: -1,
            type: "error",
            data: { code: "SSE_DISCONNECTED", message: String(error) },
          });
        }
      }
    }

    void connect();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [runId]);

  return state;
}
