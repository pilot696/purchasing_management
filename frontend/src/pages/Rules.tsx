export default function Rules() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">规则库与容差策略</h1>
      <p className="text-[13px] text-[var(--text-secondary)]">
        W2/W3 实现：品类树 + 规则表 + 表达式编辑 + dry-run 试算 + 容差策略。
      </p>
      <div className="rounded-xl border border-dashed border-[var(--border-strong)] bg-[var(--bg-surface)] p-10 text-center text-[13px] text-[var(--text-muted)]">
        画布占位（Spec §9.4 页面 5）
      </div>
    </div>
  );
}
