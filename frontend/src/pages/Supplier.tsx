export default function Supplier() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">M2 供应商准入与履约评估</h1>
      <p className="text-[13px] text-[var(--text-secondary)]">
        W3 实现：闸门条 + 五维雷达 + 维度明细表 + LLM 风险综述 + 条件清单 + 敏感性说明。
      </p>
      <div className="rounded-xl border border-dashed border-[var(--border-strong)] bg-[var(--bg-surface)] p-10 text-center text-[13px] text-[var(--text-muted)]">
        画布占位（Spec §9.4 页面 3）
      </div>
    </div>
  );
}
