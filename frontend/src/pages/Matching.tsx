export default function Matching() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">M3 合同 / PO / 发票三单匹配</h1>
      <p className="text-[13px] text-[var(--text-secondary)]">
        W1 实现：差异摘要 chips + 三列对齐表 + 差异清单 + PDF 抽屉双向高亮（doc_annotation bbox）。
      </p>
      <div className="rounded-xl border border-dashed border-[var(--border-strong)] bg-[var(--bg-surface)] p-10 text-center text-[13px] text-[var(--text-muted)]">
        画布占位（Spec §9.4 页面 4）
      </div>
    </div>
  );
}
