/** PDF 抽屉：单页 PNG + 归一化 bbox 覆盖层（Spec §9.4 页面 4）。 */

import type { DocumentAnnotation } from "../lib/api";
import { documentPageUrl } from "../lib/api";
import { annotationBoxStyle } from "../lib/m3";

interface Props {
  docId: string;
  title: string;
  annotations: DocumentAnnotation[];
  loading?: boolean;
  onClose: () => void;
}

function usable(annotation: DocumentAnnotation): boolean {
  return (
    annotation.x0 != null &&
    annotation.y0 != null &&
    annotation.x1 != null &&
    annotation.y1 != null &&
    annotation.x1 > annotation.x0 &&
    annotation.y1 > annotation.y0
  );
}

export default function PdfDrawer({ docId, title, annotations, loading = false, onClose }: Props) {
  const boxes = annotations.filter(usable);

  return (
    <aside className="fixed inset-y-0 right-0 z-30 flex w-[720px] max-w-[92vw] flex-col border-l border-[var(--border-default)] bg-[var(--bg-surface)] shadow-2xl">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--border-default)] px-4">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold">{title}</h2>
          <p className="text-[12px] text-[var(--text-muted)]">
            {loading ? "加载注解…" : `${boxes.length} 处抽取字段高亮`}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-[var(--border-default)] px-2 py-1 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--bg-subtle)]"
        >
          关闭
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-auto bg-[var(--bg-subtle)] p-4">
        <div className="relative mx-auto w-full max-w-[680px]">
          <img
            src={documentPageUrl(docId)}
            alt={`${title} 第 1 页`}
            className="block w-full rounded border border-[var(--border-default)] bg-white"
          />
          {boxes.map((annotation) => (
            <div
              key={`${annotation.field_name}-${annotation.line_ref ?? ""}-${annotation.x0}`}
              style={annotationBoxStyle({
                x0: annotation.x0 as number,
                y0: annotation.y0 as number,
                x1: annotation.x1 as number,
                y1: annotation.y1 as number,
              })}
              className="pointer-events-none absolute rounded-sm border-2 border-[var(--brand-primary)] bg-[rgba(29,78,216,0.08)]"
              title={annotation.text_raw ?? annotation.field_name}
            />
          ))}
        </div>

        {!loading && boxes.length === 0 && (
          <p className="mx-auto mt-4 max-w-[680px] text-[12px] text-[var(--text-muted)]">
            该文档没有可用的 bbox 注解（扫描件需视觉抽取，或尚未执行抽取）。
          </p>
        )}

        {boxes.length > 0 && (
          <ul className="mx-auto mt-4 grid max-w-[680px] grid-cols-1 gap-1 text-[12px]">
            {boxes.map((annotation) => (
              <li key={annotation.field_name} className="flex justify-between gap-3 rounded bg-white px-2 py-1">
                <span className="font-medium">{annotation.field_name}</span>
                <span className="truncate text-[var(--text-secondary)]">{annotation.text_raw ?? "—"}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
