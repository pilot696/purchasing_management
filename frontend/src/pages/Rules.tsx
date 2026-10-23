import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { getRules } from "../lib/api";
import type { RuleItem } from "../lib/api";

const SEVERITY_CLASS: Record<string, string> = {
  high: "border-red-200 bg-red-50 text-[var(--semantic-danger)]",
  medium: "border-amber-200 bg-amber-50 text-[var(--semantic-warning)]",
  low: "border-[var(--border-default)] bg-[var(--bg-subtle)] text-[var(--text-secondary)]",
};

const TYPE_LABEL: Record<string, string> = {
  required: "必填",
  enum: "枚举",
  cross_field: "跨字段",
  policy_ref: "政策",
  stat_outlier: "统计异常",
  data_scope: "数据范围",
  budget: "预算",
};

export default function Rules() {
  const [category, setCategory] = useState<string | null>(null);
  const catalog = useQuery({ queryKey: ["rules"], queryFn: () => getRules(), retry: false });

  const rules = useMemo<RuleItem[]>(() => {
    const all = catalog.data?.rules ?? [];
    return category ? all.filter((rule) => rule.category_code === category) : all;
  }, [catalog.data?.rules, category]);

  const categories = catalog.data?.categories ?? [];
  const tolerances = catalog.data?.tolerances ?? [];

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold">规则库与容差策略</h1>
        <p className="mt-1 max-w-3xl text-[13px] leading-6 text-[var(--text-secondary)]">
          规则是数据（`rule.expression`），由后端白名单 DSL 求值：表达式为真即命中，未声明算子显式报错。
          容差策略落 `tolerance_policy`，按品类与优先级生效。
        </p>
      </div>

      <section className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setCategory(null)}
          className={`rounded-md border px-3 py-1.5 text-[12px] ${
            category === null
              ? "border-[var(--brand-primary)] bg-blue-50 text-[var(--brand-primary)]"
              : "border-[var(--border-default)] text-[var(--text-secondary)]"
          }`}
        >
          全部 {catalog.data?.rules.length ?? 0}
        </button>
        {categories.map((item) => {
          const count = (catalog.data?.rules ?? []).filter((rule) => rule.category_code === item.code).length;
          return (
            <button
              key={item.code}
              type="button"
              onClick={() => setCategory(item.code)}
              className={`rounded-md border px-3 py-1.5 text-[12px] ${
                category === item.code
                  ? "border-[var(--brand-primary)] bg-blue-50 text-[var(--brand-primary)]"
                  : "border-[var(--border-default)] text-[var(--text-secondary)]"
              }`}
            >
              {item.name}（{item.code}）· {count}
            </button>
          );
        })}
      </section>

      {catalog.isError && (
        <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] text-[var(--semantic-danger)]">
          规则加载失败：{String(catalog.error)}
        </p>
      )}
      {catalog.isLoading && <p className="text-[13px] text-[var(--text-muted)]">加载规则库…</p>}

      <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">规则表（{rules.length}）</h2>
          <span className="text-[12px] text-[var(--text-muted)]">
            {categories.length} 个品类 · 只读视图
          </span>
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[980px] border-collapse text-[12px]">
            <thead>
              <tr className="border-b border-[var(--border-default)] text-left text-[var(--text-muted)]">
                <th className="px-2 py-1.5">规则</th>
                <th className="px-2 py-1.5">品类</th>
                <th className="px-2 py-1.5">类型</th>
                <th className="px-2 py-1.5">严重级</th>
                <th className="px-2 py-1.5">字段</th>
                <th className="px-2 py-1.5">表达式（JSONLogic 子集）</th>
                <th className="px-2 py-1.5">策略引用</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id} className="border-b border-[var(--border-default)] align-top">
                  <td className="px-2 py-2">
                    <div className="font-mono font-medium">{rule.code}</div>
                    <div className="mt-0.5 text-[11px] text-[var(--text-muted)]">{rule.message_template}</div>
                  </td>
                  <td className="px-2 py-2">
                    <div>{rule.category_name}</div>
                    <div className="text-[11px] text-[var(--text-muted)]">{rule.category_code}</div>
                  </td>
                  <td className="px-2 py-2">{TYPE_LABEL[rule.type] ?? rule.type}</td>
                  <td className="px-2 py-2">
                    <span className={`rounded border px-1.5 py-0.5 text-[11px] ${SEVERITY_CLASS[rule.severity] ?? ""}`}>
                      {rule.severity}
                    </span>
                  </td>
                  <td className="px-2 py-2 font-mono text-[11px]">{rule.field_path ?? "—"}</td>
                  <td className="max-w-[320px] px-2 py-2">
                    <code className="block break-all rounded bg-[var(--bg-subtle)] p-1.5 text-[10px] text-[var(--text-secondary)]">
                      {JSON.stringify(rule.expression)}
                    </code>
                  </td>
                  <td className="px-2 py-2 font-mono text-[11px] text-[var(--text-muted)]">
                    {rule.policy_clause_ref ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-surface)] p-4">
        <h2 className="text-sm font-semibold">容差策略（{tolerances.length}）</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse text-[12px]">
            <thead>
              <tr className="border-b border-[var(--border-default)] text-left text-[var(--text-muted)]">
                <th className="px-2 py-1.5">品类</th>
                <th className="px-2 py-1.5">差异类型</th>
                <th className="px-2 py-1.5 text-right">绝对容差</th>
                <th className="px-2 py-1.5 text-right">相对容差</th>
                <th className="px-2 py-1.5">动作</th>
                <th className="px-2 py-1.5 text-right">优先级</th>
              </tr>
            </thead>
            <tbody>
              {tolerances.map((policy) => (
                <tr key={policy.id} className="border-b border-[var(--border-default)]">
                  <td className="px-2 py-2">{policy.category_code ?? "全局"}</td>
                  <td className="px-2 py-2 font-mono text-[11px]">{policy.diff_type}</td>
                  <td className="px-2 py-2 text-right">{policy.abs_tol}</td>
                  <td className="px-2 py-2 text-right">{(policy.rel_tol * 100).toFixed(2)}%</td>
                  <td className="px-2 py-2">{policy.action}</td>
                  <td className="px-2 py-2 text-right">{policy.priority}</td>
                </tr>
              ))}
              {tolerances.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-2 py-5 text-center text-[var(--text-muted)]">
                    暂无容差策略记录。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
