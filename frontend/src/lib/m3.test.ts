import { describe, expect, it } from "vitest";

import {
  alignRows,
  annotationBoxStyle,
  asM3Report,
  diffMeta,
  formatDelta,
  formatMoney,
  formatQty,
  overallMeta,
  roleCanReviewM3,
  severityClass,
} from "./m3";
import type { M3Report } from "./m3";

const REPORT: M3Report = {
  module: "m3",
  po_no: "PO-2026-0001",
  invoice_no: "INV-2026-3000",
  overall: "hold",
  diffs: [
    {
      diff_type: "price",
      severity: "medium",
      po_line_no: 1,
      invoice_line_no: 1,
      expected: 100,
      actual: 120,
      delta: 20,
      rule_ref: "TOL-PRICE-01",
      suggestion: "补签价格变更",
    },
    {
      diff_type: "tail",
      severity: "low",
      po_line_no: null,
      invoice_line_no: null,
      expected: 1000,
      actual: 1000.03,
      delta: 0.03,
      rule_ref: "TOL-TAIL-01",
    },
  ],
  line_matches: [
    {
      po_line_no: 1,
      invoice_line_no: 1,
      method: "L2_bucket",
      score: 0.95,
      consumed_qty: 2,
      remaining_qty: 1,
      status: "partial",
    },
  ],
  documents: { po: null, invoice: "doc-invoice", contract: null },
  po_total: 2000,
  invoice_total: 2400,
  po_lines: [
    {
      line_no: 1,
      item_desc: "MacBook Pro 14",
      qty_ordered: 3,
      unit_price_tax_excl: 100,
    },
  ],
  invoice_lines: [
    { line_no: 1, item_desc: "MacBook Pro 14 16G/512G", qty: 2, unit_price_tax_excl: 120, amount_tax_incl: 276 },
    { line_no: 2, item_desc: "未匹配行", qty: 1, unit_price_tax_excl: 50, amount_tax_incl: 56.5 },
  ],
};

describe("asM3Report", () => {
  it("解析 M3 报告并补齐默认字段", () => {
    const report = asM3Report(REPORT);
    expect(report).not.toBeNull();
    expect(report?.po_no).toBe("PO-2026-0001");
    expect(report?.overall).toBe("hold");
    expect(report?.diffs).toHaveLength(2);
  });

  it("非 M3/空值返回 null", () => {
    expect(asM3Report(null)).toBeNull();
    expect(asM3Report({ hello: "world" })).toBeNull();
  });
});

describe("alignRows", () => {
  it("按 line_matches 连接 PO/发票行，并补未匹配发票行", () => {
    const rows = alignRows(REPORT);
    expect(rows).toHaveLength(2);
    expect(rows[0].po?.item_desc).toBe("MacBook Pro 14");
    expect(rows[0].invoice?.line_no).toBe(1);
    expect(rows[1].match).toBeNull();
    expect(rows[1].invoice?.item_desc).toBe("未匹配行");
  });

  it("无行数据时返回空数组", () => {
    expect(alignRows({ ...REPORT, line_matches: [], po_lines: [], invoice_lines: [] })).toEqual([]);
  });
});

describe("展示映射", () => {
  it("overall / diff 文案与严重级样式", () => {
    expect(overallMeta("hold").label).toBe("挂起");
    expect(overallMeta("unknown_overall").label).toBe("unknown_overall");
    expect(diffMeta("qty_over").label).toBe("超发");
    expect(diffMeta("price").hint).toContain("冲红");
    expect(severityClass("high")).toContain("red");
    expect(severityClass("medium")).toContain("amber");
  });

  it("金额/数量/增量格式化", () => {
    expect(formatMoney(1234.5)).toBe("¥1234.50");
    expect(formatMoney(null)).toBe("—");
    expect(formatQty(2)).toBe("2");
    expect(formatQty(2.5)).toBe("2.50");
    expect(formatDelta({ ...REPORT.diffs[0], delta: 20 })).toBe("+20.00");
    expect(formatDelta({ ...REPORT.diffs[0], delta: null })).toBe("—");
  });

  it("M3 放行权限：finance/admin", () => {
    expect(roleCanReviewM3("finance")).toBe(true);
    expect(roleCanReviewM3("admin")).toBe(true);
    expect(roleCanReviewM3("buyer")).toBe(false);
  });
});

describe("annotationBoxStyle", () => {
  it("归一化 bbox 转百分比并做边界钳制", () => {
    expect(annotationBoxStyle({ x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.7 })).toEqual({
      left: "10%",
      top: "20%",
      width: "50%",
      height: "50%",
    });
    expect(annotationBoxStyle({ x0: -0.2, y0: 1.4, x1: 1.6, y1: 0.5 })).toEqual({
      left: "0%",
      top: "100%",
      width: "100%",
      height: "0%",
    });
  });
});
