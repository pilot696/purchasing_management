export default function Precheck() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">M1 采购申请预检</h1>
      <p className="text-[13px] text-[var(--text-secondary)]">
        W2 实现：左 PR 字段树 / 中 findings 与 patch 采纳卡 / 右证据面板（规则、条款、历史成交、价格分布）。
      </p>
      <div className="rounded-xl border border-dashed border-[var(--border-strong)] bg-[var(--bg-surface)] p-10 text-center text-[13px] text-[var(--text-muted)]">
        画布占位（Spec §9.4 页面 2）
      </div>
    </div>
  );
}
