import { describe, expect, it } from "vitest";

import {
  asM2Report,
  confidenceLabel,
  dimensionLabel,
  formatMetric,
  formatMetricValue,
  gateLabel,
  llmStatusLabel,
  metricLabel,
  radarPoints,
  roleCanReviewM2,
  verdictMeta,
} from "./m2";
import type { M2Report } from "./m2";

const REPORT: M2Report = {
  module: "m2",
  supplier_code: "SUP-001",
  supplier_name: "北京华信科技有限公司",
  category_code: "IT_HW",
  dimensions: [
    { dimension: "price", score: 88, weight: 0.25, raw_metrics: { price_index: 1.1, sample_size: 3 }, sample_size: 3 },
    { dimension: "delivery", score: 62, weight: 0.3, raw_metrics: { otd: 0.62, sample_size: 5 }, sample_size: 5 },
    { dimension: "quality", score: 95, weight: 0.2, raw_metrics: { qc_pass_rate: 0.98 }, sample_size: 5 },
    { dimension: "service", score: 80, weight: 0.15, raw_metrics: { first_response_hours: 12.4 }, sample_size: 2 },
    { dimension: "compliance", score: 100, weight: 0.1, raw_metrics: { cert_expiring: false }, sample_size: 3 },
  ],
  total: 80.0,
  verdict: "conditional",
  gate_hits: [{ code: "G6_SECURITY_CERT", message: "缺少 ISO27001", evidence_refs: ["营业执照-1"], veto: false }],
  confidence: "medium",
  confidence_detail: { value: 0.62, level: "medium" },
  conditions: ["30 天内补交 ISO27001"],
  llm_review: {
    risk_summary: "交付存在波动",
    top_risks: [
      { dimension: "delivery", claim: "准时率偏低", evidence_refs: ["PO-1"], severity_hint: "high" },
    ],
    conditions: ["设置交付周报"],
    insufficient_evidence: [],
    reviewer_memo: "关注交付",
  },
  llm_status: "ok",
  sensitivity: { flip_rate: 0.05, most_sensitive: "delivery", samples: 300 },
  weight_version: "v1",
  evidence_pack_ids: ["PO-1", "营业执照-1"],
  cold_start: false,
  summary: "SUP-001 判定 conditional",
  requires_review: true,
};

describe("asM2Report", () => {
  it("解析 M2 报告并补齐默认字段", () => {
    const report = asM2Report(REPORT);
    expect(report).not.toBeNull();
    expect(report?.supplier_code).toBe("SUP-001");
    expect(report?.dimensions).toHaveLength(5);
    expect(report?.gate_hits).toHaveLength(1);
  });

  it("非 M2/空值返回 null", () => {
    expect(asM2Report(null)).toBeNull();
    expect(asM2Report({ hello: "world" })).toBeNull();
  });
});

describe("展示映射", () => {
  it("verdict / gate / dimension 文案", () => {
    expect(verdictMeta("approve").label).toBe("准入通过");
    expect(verdictMeta("conditional").tone).toBe("note");
    expect(verdictMeta("frozen").tone).toBe("block");
    expect(verdictMeta("unknown").label).toBe("unknown");
    expect(gateLabel("G1_LICENSE_INVALID")).toContain("营业执照");
    expect(gateLabel("GX")).toBe("GX");
    expect(dimensionLabel("delivery")).toBe("交付履约");
    expect(metricLabel("otd")).toBe("准时交付率");
    expect(confidenceLabel("high")).toBe("高置信度");
    expect(llmStatusLabel("skipped_gate_hit")).toContain("跳过 LLM");
    expect(llmStatusLabel("unavailable")).toContain("离线");
  });

  it("原始指标格式化", () => {
    expect(formatMetricValue(0.95)).toBe("0.95");
    expect(formatMetricValue(3)).toBe("3");
    expect(formatMetricValue(true)).toBe("是");
    expect(formatMetricValue(null)).toBe("—");
    expect(formatMetricValue(undefined)).toBe("—");
  });

  it("账期资金占用指标有中文标签和专用格式", () => {
    expect(metricLabel("payment_days")).toContain("账期");
    expect(metricLabel("financing_factor")).toContain("资金占用");
    expect(metricLabel("financing_annual_rate")).toContain("年化");
    expect(formatMetric("financing_annual_rate", 0.08)).toBe("8.0%");
    expect(formatMetric("financing_factor", 1.0123)).toBe("1.0123×");
    expect(formatMetric("payment_days", 45)).toBe("45 天");
    expect(formatMetric("financing_baseline_days", 30)).toBe("30 天");
    expect(formatMetric("price_index_raw", 1.03456)).toBe("1.035");
    expect(formatMetric("otd", 0.62)).toBe("62.0%");
  });

  it("角色权限", () => {
    expect(roleCanReviewM2("buyer")).toBe(true);
    expect(roleCanReviewM2("admin")).toBe(true);
    expect(roleCanReviewM2("requester")).toBe(false);
  });
});

describe("radarPoints", () => {
  it("生成 5 个顶点且分数归一化", () => {
    const { points, axes } = radarPoints(
      [
        { dimension: "price", score: 100 },
        { dimension: "delivery", score: 50 },
        { dimension: "quality", score: 0 },
        { dimension: "service", score: 100 },
        { dimension: "compliance", score: 100 },
      ],
      { radius: 80, cx: 110, cy: 110 },
    );
    expect(axes).toHaveLength(5);
    expect(points.split(" ")).toHaveLength(5);
    // score=0 的顶点落在圆心附近
    const qualityPoint = points.split(" ")[2].split(",").map(Number);
    expect(qualityPoint[0]).toBeCloseTo(110, 0);
    expect(qualityPoint[1]).toBeCloseTo(110, 0);
    // score=100 的顶点距离圆心约 radius
    const pricePoint = points.split(" ")[0].split(",").map(Number);
    const distance = Math.hypot(pricePoint[0] - 110, pricePoint[1] - 110);
    expect(distance).toBeCloseTo(80, 0);
  });

  it("空维度也返回空轴", () => {
    const { axes, points } = radarPoints([]);
    expect(axes).toHaveLength(0);
    expect(points).toBe("");
  });
});
