/**
 * 极简 SSE 解析：服务端事件格式为 `id: N\nevent: type\ndata: {...}\n\n`。
 * 不引第三方依赖（native EventSource 不支持自定义请求头，见 Spec §9.6）。
 */

import type { EventType, RunEvent } from "./events";

export interface ParsedChunk {
  events: RunEvent[];
  rest: string;
}

export function parseSseChunk(buffer: string): ParsedChunk {
  let rest = buffer.replace(/\r\n/g, "\n");
  const events: RunEvent[] = [];
  let index = rest.indexOf("\n\n");
  while (index >= 0) {
    const block = rest.slice(0, index);
    rest = rest.slice(index + 2);
    const parsed = parseSseBlock(block);
    if (parsed) events.push(parsed);
    index = rest.indexOf("\n\n");
  }
  return { events, rest };
}

function parseSseBlock(block: string): RunEvent | null {
  let id = 0;
  let type: string | null = null;
  let data = "";
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":") || line.startsWith("retry:")) continue;
    const sep = line.indexOf(":");
    const field = sep === -1 ? line : line.slice(0, sep);
    let value = sep === -1 ? "" : line.slice(sep + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id") {
      const parsedId = Number(value);
      if (Number.isFinite(parsedId)) id = parsedId;
    } else if (field === "event") {
      type = value;
    } else if (field === "data") {
      data = data ? `${data}\n${value}` : value;
    }
  }
  if (!type || !data) return null;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return null;
  }
  return { id, type: type as EventType, data: payload };
}
