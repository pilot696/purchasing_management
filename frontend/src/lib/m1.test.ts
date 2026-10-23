import { describe, expect, it } from "vitest";

import {
  asM1Report,
  budgetBar,
  candidateSourcesText,
  findingTypeLabel,
  formatPatchValue,
  formatSpecValue,
  patchPathLabel,
  patchSourceLabel,
  roleCanReviewM1,
  severityClass,
  verdictMeta,
  verdictToneClass,
} from "./m1";
import type { M1Report } from "./m1";

const REPORT: M1Report = {
  module: "m1",
  pr_no: "PR-2026-0001",
  title: "采购 ThinkPad",
  category_code: "IT_HW",
  verdict: "needs_info",
  findings: [
    {
      rule_code: "IT-SPEC-MEM-01",
      type: "required",
      severity: "high",
      field: "/items/0/spec/memory_gb",
      message: "IT 硬件必须明确内存容量",
      evidence: [{ kind: "policy", ref: "POL-IT-2025-03#4.2" }],
    },
  ],
  patches: [
    {
      op: "add",
      path: "/items/0/spec/memory_gb",
      value: 16,
      from: null,
      source: "catalog_sku",
      ref: "SKU-IT_HW-001",
    },
  ],
  items: [
    {
      line_no: 1,
      item_name_raw: "ThinkPad T14 Gen5 笔记本",
      spec: { storage_gb: 512 },
      spec_bucket: "laptop/16g/512g",
      qty: 2,
      unit: "台",
      candidates: [
        {
          sku_code: "SKU-IT_HW-001",
          name: "ThinkPad T14 Gen5 笔记本",
          spec_bucket: "laptop/16g/512g",
          score: 0.04918,
          sources: ["lexical", "exact", "structured"],
          spec: { memory_gb: 16 },
        },
      ],
      ambiguous: false,
    },
  ],
  budget: { available: 2000, estimated: 10397.92, over_budget: true },
  summary: "PR-2026-0001 预检：needs_info，1 个问题、1 条建议补丁",
  requires_review: true,
  review_kind: "apply_patch",
};

describe("asM1Report", () => {
  it("解析 M1 报告并补齐默认字段", () => {
    const report = asM1Report(REPORT);
    expect(report).not.toBeNull();
    expect(report?.pr_no).toBe("PR-2026-0001");
    expect(report?.verdict).toBe("needs_info");
    expect(report?.findings).toHaveLength(1);
    expect(report?.patches).toHaveLength(1);
  });

  it("非 M1/空值返回 null", () => {
    expect(asM1Report(null)).toBeNull();
    expect(asM1Report({ hello: "world" })).toBeNull();
  });
});

describe("展示映射", () => {
  it("verdict / finding / patch 文案", () => {
    expect(verdictMeta("pass").label).toBe("通过");
    expect(verdictMeta("needs_info").label).toBe("需补充信息");
    expect(verdictMeta("block").tone).toBe("block");
    expect(verdictMeta("unknown").label).toBe("unknown");
    expect(findingTypeLabel("required")).toBe("必填缺失");
    expect(findingTypeLabel("ambiguity")).toBe("规格歧义");
    expect(patchSourceLabel("catalog_sku")).toBe("目录 SKU");
    expect(patchSourceLabel(undefined)).toBe("自动建议");
  });

  it("补丁路径/值格式化", () => {
    expect(patchPathLabel("/items/0/spec/memory_gb")).toBe("行 0 · spec/memory_gb");
    expect(formatPatchValue(null)).toBe("∅");
    expect(formatPatchValue(16)).toBe("16");
    expect(formatSpecValue(512)).toBe("512");
    expect(formatSpecValue(undefined)).toBe("—");
  });

  it("候选来源与严重级样式", () => {
    expect(candidateSourcesText(["lexical", "exact", "structured"])).toBe("词法 + 精确 + 规格桶");
    expect(severityClass("high")).toContain("red");
    expect(verdictToneClass("pass")).toContain("green");
  });

  it("预算条与角色权限", () => {
    expect(budgetBar(2000, 1000)).toEqual({ percent: 50, over: false });
    expect(budgetBar(2000, 4000)).toEqual({ percent: 100, over: true });
    expect(budgetBar(0, 100)).toEqual({ percent: 100, over: true });
    expect(roleCanReviewM1("buyer")).toBe(true);
    expect(roleCanReviewM1("admin")).toBe(true);
    expect(roleCanReviewM1("requester")).toBe(false);
  });
});
