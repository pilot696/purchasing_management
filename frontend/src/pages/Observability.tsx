export default function Observability() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">可观测与评测</h1>
      <p className="text-[13px] text-[var(--text-secondary)]">
        W3 实现：run trace 甘特 + LLM 调用记账表（tokens/cost/latency/cache）+ 评测报告与基线对照。
      </p>
      <div className="rounded-xl border border-dashed border-[var(--border-strong)] bg-[var(--bg-surface)] p-10 text-center text-[13px] text-[var(--text-muted)]">
        画布占位（Spec §9.4 页面 6）
      </div>
    </div>
  );
}
