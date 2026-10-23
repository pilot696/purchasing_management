"""生成 pen.dev 设计稿（design/design.pen）与静态预览（design/preview.html）。

设计源：`frontend/src/styles/tokens.css`（同一套 CSS 变量）、`docs/PROJECT_SPEC.md` §9.4 六页信息架构。
D13：pen.dev 只做设计稿，代码 UI 手写；本脚本保证设计稿可重复生成（不手改 .pen）。

用法：
    python scripts/gen_design_pen.py
产物：
    design/tokens.json      设计令牌（三处同源之一）
    design/design.pen       pen.dev schema 2.14 文档（6 个 artboard）
    design/preview.html     静态预览（无需 pen.dev 即可查看布局）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DESIGN_DIR = ROOT / "design"

TOKENS: dict[str, Any] = {
    "meta": {
        "name": "Procurement AI Workbench",
        "version": "1.0.0",
        "source": "frontend/src/styles/tokens.css",
        "pen_schema": "2.14",
    },
    "colors": {
        "bgCanvas": "#f6f7f9",
        "bgSurface": "#ffffff",
        "bgSubtle": "#eef1f5",
        "borderDefault": "#e3e7ed",
        "borderStrong": "#c9d1db",
        "textPrimary": "#0f172a",
        "textSecondary": "#475569",
        "textMuted": "#94a3b8",
        "brandPrimary": "#1d4ed8",
        "semanticSuccess": "#15803d",
        "semanticWarning": "#b45309",
        "semanticDanger": "#b91c1c",
        "semanticInfo": "#0369a1",
    },
    "radius": {"sm": 6, "md": 8, "lg": 12, "xl": 16},
    "spacing": {"1": 4, "2": 8, "3": 12, "4": 16, "5": 20, "6": 24, "8": 32},
    "fontSize": {"caption": 11, "label": 12, "body": 13, "h3": 14, "h2": 16, "h1": 20},
    "fontFamily": "Inter",
}

_ids: dict[str, int] = {}


def nid(slug: str) -> str:
    count = _ids.get(slug, 0) + 1
    _ids[slug] = count
    return f"{slug}-{count}"


FONT_SIZES = {"$caption": 11, "$label": 12, "$body": 13, "$h3": 14, "$h2": 16, "$h1": 20}


def text(content: str, *, size: int | str = 13, color: str = "$textPrimary", weight: str = "400", width: int | None = None, name: str | None = None) -> dict[str, Any]:
    if isinstance(size, str):
        size = FONT_SIZES.get(size, 13)
    node: dict[str, Any] = {
        "id": nid("text"),
        "type": "text",
        "name": name or content[:16],
        "content": content,
        "fontFamily": "$fontFamily",
        "fontSize": size,
        "fill": color,
        "fontWeight": weight,
        "textGrowth": "fixed-width" if width else "auto",
    }
    if width:
        node["width"] = width
    return node


def rect(width: int | str, height: int | str, *, fill: str = "$bgSurface", radius: int = 0, name: str = "rect", stroke: str | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": nid("rect"),
        "type": "rectangle",
        "name": name,
        "width": width,
        "height": height,
        "fill": fill,
        "cornerRadius": radius,
    }
    if stroke:
        node["stroke"] = stroke
        node["strokeWidth"] = 1
    return node


def card(*children: dict[str, Any], w: int | str = "fill_container", h: int | str = "fit_content", name: str = "card", gap: int = 10, padding: int = 16, direction: str = "vertical") -> dict[str, Any]:
    return {
        "id": nid("frame"),
        "type": "frame",
        "name": name,
        "width": w,
        "height": h,
        "layout": direction,
        "gap": gap,
        "padding": padding,
        "fill": "$bgSurface",
        "stroke": "$borderDefault",
        "strokeWidth": 1,
        "cornerRadius": 12,
        "children": list(children),
    }


def chip(label: str, *, status: str = "info") -> dict[str, Any]:
    fills = {
        "info": ("#eff6ff", "$semanticInfo"),
        "success": ("#f0fdf4", "$semanticSuccess"),
        "warning": ("#fffbeb", "$semanticWarning"),
        "danger": ("#fef2f2", "$semanticDanger"),
    }
    bg, fg = fills[status]
    frame = {
        "id": nid("chip"),
        "type": "frame",
        "name": f"chip-{label}",
        "width": "fit_content",
        "height": "fit_content",
        "layout": "horizontal",
        "padding": [4, 8],
        "gap": 4,
        "fill": bg,
        "cornerRadius": 6,
        "children": [text(label, size=12, color=fg, weight="500")],
    }
    return frame


def button(label: str, *, primary: bool = True) -> dict[str, Any]:
    return {
        "id": nid("button"),
        "type": "frame",
        "name": f"btn-{label}",
        "width": "fit_content",
        "height": "fit_content",
        "layout": "horizontal",
        "padding": [8, 14],
        "fill": "$brandPrimary" if primary else "$bgSubtle",
        "cornerRadius": 8,
        "children": [text(label, size=12, color="#FFFFFF" if primary else "$textSecondary", weight="500")],
    }


def row(*children: dict[str, Any], name: str = "row", gap: int = 12) -> dict[str, Any]:
    return {
        "id": nid("row"),
        "type": "frame",
        "name": name,
        "width": "fill_container",
        "height": "fit_content",
        "layout": "horizontal",
        "gap": gap,
        "children": list(children),
    }


def col(*children: dict[str, Any], name: str = "col", gap: int = 10, w: int | str = "fill_container") -> dict[str, Any]:
    return {
        "id": nid("col"),
        "type": "frame",
        "name": name,
        "width": w,
        "height": "fit_content",
        "layout": "vertical",
        "gap": gap,
        "children": list(children),
    }


def page_header(title: str, subtitle: str) -> dict[str, Any]:
    return col(text(title, size="$h1", weight="600"), text(subtitle, size="$body", color="$textSecondary"), name="page-header", gap=6)


def sidebar(active: str) -> dict[str, Any]:
    nav = ["Run 中心", "M1 预检", "M2 供应商", "M3 三单", "规则库", "可观测"]
    items = []
    for label in nav:
        is_active = label == active
        items.append(
            {
                "id": nid("nav"),
                "type": "frame",
                "name": f"nav-{label}",
                "width": "fill_container",
                "height": "fit_content",
                "layout": "horizontal",
                "padding": [8, 12],
                "fill": "$bgSubtle" if is_active else "$bgSurface",
                "cornerRadius": 8,
                "children": [text(label, size=13, color="$brandPrimary" if is_active else "$textSecondary", weight="500" if is_active else "400")],
            }
        )
    return {
        "id": nid("sidebar"),
        "type": "frame",
        "name": "sidebar",
        "width": 220,
        "height": "fill_container",
        "layout": "vertical",
        "gap": 4,
        "padding": 12,
        "fill": "$bgSurface",
        "children": [
            row(rect(24, 24, fill="$brandPrimary", radius=6, name="logo"), text("Procurement AI", size=14, weight="600")),
            rect("fill_container", 1, fill="$borderDefault", name="nav-divider"),
            *items,
            rect("fill_container", "fill_container", fill="$bgSurface", name="sidebar-spacer"),
            text("演示角色：buyer", size=11, color="$textMuted"),
        ],
    }


def page(title: str, subtitle: str, active: str, content: list[dict[str, Any]], index: int) -> dict[str, Any]:
    main = {
        "id": nid("main"),
        "type": "frame",
        "name": "main",
        "width": "fill_container",
        "height": "fill_container",
        "layout": "vertical",
        "gap": 16,
        "padding": 24,
        "children": [page_header(title, subtitle), *content],
    }
    return {
        "id": nid("page"),
        "type": "frame",
        "name": title,
        "x": index * 1520,
        "y": 0,
        "width": 1440,
        "height": 900,
        "layout": "horizontal",
        "fill": "$bgCanvas",
        "children": [sidebar(active), main],
    }


def table(headers: list[str], rows: list[list[str]], *, widths: list[int] | None = None) -> dict[str, Any]:
    def cell_width(index: int) -> int | None:
        return widths[index] if widths and index < len(widths) else None

    head_cells = []
    for i, header in enumerate(headers):
        head_cells.append(text(header, size=11, color="$textMuted", weight="500", width=cell_width(i)))
    body = []
    for values in rows:
        cells = [text(value, size=12, color="$textSecondary", width=cell_width(i)) for i, value in enumerate(values)]
        body.append(row(*cells, name="table-row", gap=12))
    return card(
        row(*head_cells, name="table-head", gap=12),
        rect("fill_container", 1, fill="$borderDefault", name="table-divider"),
        *body,
        name="table",
    )


def build_pages() -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []

    # 页面 1：运行中心
    modules = []
    for name, desc, status in [
        ("M1 采购申请预检", "规则 DSL + 混合检索，输出 verdict 与可采纳补丁", "可启动"),
        ("M2 供应商评估", "6 条硬闸门 + 五维确定性评分 + LLM 复核者", "可启动"),
        ("M3 三单匹配", "PO/发票/合同行级对齐 + 容差 + 分批核销", "可启动"),
    ]:
        modules.append(
            card(
                text(name, size=14, weight="600"),
                text(desc, size=12, color="$textSecondary", width=260),
                row(chip(status, status="success"), button("新建运行")),
                name=f"module-{name[:2]}",
                w="fill_container",
            )
        )
    pages.append(
        page(
            "Run 中心",
            "三条 Agent 链路的状态、事件与成本；SSE 可回放，HITL 中断可恢复",
            "Run 中心",
            [
                row(*modules, name="module-grid", gap=16),
                table(
                    ["运行", "模块", "状态", "tokens", "成本", "创建时间"],
                    [
                        ["run 8f2a·4e1c", "M2", "succeeded", "12,480", "¥0.00", "18:42"],
                        ["run 5c91·ab03", "M1", "awaiting_review", "0", "¥0.00", "18:35"],
                        ["run 71de·9c44", "M3", "succeeded", "0", "¥0.00", "18:20"],
                    ],
                ),
                text("提示：本地演示默认 FORCE_FAKE_LLM=1，cost=0；接入真实模型后成本按 llm_call 归因。", size=11, color="$textMuted"),
            ],
            0,
        )
    )

    # 页面 2：M1 预检
    pages.append(
        page(
            "M1 采购申请预检",
            "输入 PR 号 → 字段树/规格 → findings → 可采纳补丁 → 写回 PR 明细（HITL）",
            "M1 预检",
            [
                card(
                    row(
                        col(text("PR 号", size=11, color="$textMuted"), rect(180, 30, fill="$bgSubtle", radius=6, name="pr-input"), gap=4, w=180),
                        button("启动预检"),
                        chip("verdict: needs_info", status="warning"),
                        name="precheck-form",
                    ),
                    w="fill_container",
                ),
                row(
                    card(
                        text("申请明细与检索候选", size=14, weight="600"),
                        table(
                            ["行", "品名", "规格桶", "候选（RRF）"],
                            [["1", "ThinkPad T14", "laptop/16g/512g", "SKU-IT-001 · 0.049"], ["2", "27 寸显示器", "monitor/27", "SKU-IT-002 · 0.047"]],
                            widths=[40, 160, 130],
                        ),
                        name="precheck-items",
                    ),
                    card(
                        text("发现的问题（2）", size=14, weight="600"),
                        row(chip("必填缺失", status="warning"), text("IT-SPEC-MEM-01 · 内存容量未填", size=12, color="$textSecondary")),
                        row(chip("政策引用", status="danger"), text("POL-IT-2025-03#4.2 · 需附说明", size=12, color="$textSecondary")),
                        rect("fill_container", 1, fill="$borderDefault", name="divider"),
                        text("建议补丁（1）", size=14, weight="600"),
                        text("行 0 · memory_gb：∅ → 16（来源 catalog_sku）", size=12, color="$textSecondary"),
                        row(button("采纳并继续", primary=True), button("退回补充", primary=False)),
                        name="precheck-findings",
                    ),
                    name="precheck-grid",
                    gap=16,
                ),
            ],
            1,
        )
    )

    # 页面 3：M2 供应商
    pages.append(
        page(
            "M2 供应商评估",
            "6 条硬闸门短路 → 五维评分 → 权重敏感性 → LLM 复核者 → 人工确认",
            "M2 供应商",
            [
                card(
                    row(
                        col(text("供应商编码", size=11, color="$textMuted"), rect(160, 30, fill="$bgSubtle", radius=6, name="supplier-input"), gap=4, w=160),
                        button("启动评估"),
                        chip("verdict: conditional", status="warning"),
                        chip("confidence: medium", status="info"),
                        chip("flip_rate: 5.5%", status="success"),
                        name="supplier-form",
                    ),
                ),
                row(
                    card(
                        text("五维得分（雷达）", size=14, weight="600"),
                        {"id": nid("radar"), "type": "ellipse", "name": "radar-placeholder", "width": 220, "height": 220, "fill": "$bgSubtle", "innerRadius": 0.85, "stroke": "$borderDefault", "strokeWidth": 1},
                        text("价格 88 · 交付 62 · 质量 95 · 服务 80 · 合规 100", size=11, color="$textMuted"),
                        name="supplier-radar",
                    ),
                    card(
                        text("硬闸门（6 条）", size=14, weight="600"),
                        row(chip("G2 范围（人工确认）", status="warning"), text("需核对证照原文", size=11, color="$textSecondary")),
                        row(chip("G6 安全资质", status="warning"), text("30 天内补交 ISO27001", size=11, color="$textSecondary")),
                        text("准入条件", size=13, weight="600"),
                        text("· 设置 3 个月试用期\n· 首单 100% 抽检", size=12, color="$textSecondary"),
                        name="supplier-gates",
                    ),
                    card(
                        text("LLM 风险复核", size=14, weight="600"),
                        text("LLM 未配置（离线模式，无叙述）", size=12, color="$textMuted"),
                        text("分数与闸门由确定性模型计算，模型无权修改。", size=11, color="$textMuted"),
                        name="supplier-llm",
                    ),
                    name="supplier-grid",
                    gap=16,
                ),
                table(
                    ["维度", "得分", "权重", "样本", "原始指标"],
                    [
                        ["价格竞争力", "88.00", "25%", "3", "price_index=1.10"],
                        ["交付履约", "62.00", "30%", "5", "otd=0.62"],
                        ["质量", "95.00", "20%", "5", "qc_pass_rate=0.98"],
                    ],
                ),
            ],
            2,
        )
    )

    # 页面 4：M3 三单
    pages.append(
        page(
            "M3 三单匹配",
            "PO / 发票 / 合同三列对齐 → 差异卡片 → 查看发票（PNG + bbox 高亮）→ 放行/挂起",
            "M3 三单",
            [
                card(
                    row(
                        col(text("PO 号", size=11, color="$textMuted"), rect(160, 30, fill="$bgSubtle", radius=6, name="po-input"), gap=4, w=160),
                        col(text("发票号", size=11, color="$textMuted"), rect(180, 30, fill="$bgSubtle", radius=6, name="invoice-input"), gap=4, w=180),
                        button("启动匹配"),
                        chip("overall: hold", status="danger"),
                        button("查看发票", primary=False),
                        name="matching-form",
                    ),
                ),
                table(
                    ["PO 行", "发票行", "方法", "拟核销", "剩余", "状态"],
                    [
                        ["L1 ThinkPad ×2", "L1 ×1", "exact", "1", "1", "matched"],
                        ["L2 显示器 ×1", "L2 ×1", "spec_bucket", "1", "0", "qty_over"],
                        ["L3 扩展坞 ×3", "—", "—", "0", "3", "unmatched"],
                    ],
                    widths=[180, 180, 110, 80, 60],
                ),
                row(
                    card(text("差异：数量超开", size=13, weight="600"), text("发票数量 3 > 可核销 1；建议挂起。", size=12, color="$textSecondary")),
                    card(text("差异：尾差", size=13, weight="600"), text("金额差 ¥0.03，计入尾差池。", size=12, color="$textSecondary")),
                    card(text("差异：条款冲突", size=13, weight="600"), text("账期 60 天 vs 合同 30 天。", size=12, color="$textSecondary")),
                    name="diff-cards",
                    gap=16,
                ),
                row(button("财务放行", primary=True), button("挂起", primary=False)),
            ],
            3,
        )
    )

    # 页面 5：规则库
    pages.append(
        page(
            "规则库",
            "17 条声明式规则按品类分类；表达式在白名单 DSL 中执行，未声明算子直接报错",
            "规则库",
            [
                row(chip("IT_HW 5", status="info"), chip("SAAS 4", status="info"), chip("ADMIN 4", status="info"), chip("MKT 4", status="info")),
                table(
                    ["规则", "品类", "类型", "严重级", "表达式（JSONLogic 子集）"],
                    [
                        ["IT-SPEC-MEM-01", "IT_HW", "required", "high", "{\"required\": [{\"var\": \"items[*].spec.memory_gb\"}]}"],
                        ["IT-QTY-01", "IT_HW", "stat_outlier", "medium", "{ \">\": [{\"var\": \"qty\"}, {\"fn\": \"p95_qty\"}] }"],
                        ["POLICY-DATA-01", "SAAS", "policy_ref", "high", "{\"in\": [{\"var\": \"data_scope\"}, [\"内部数据\"]]}"],
                    ],
                ),
                text("表达式只允许白名单算子；执行失败会抛 RuleEvalError 而不是静默放过。", size=11, color="$textMuted"),
            ],
            4,
        )
    )

    # 页面 6：可观测
    pages.append(
        page(
            "可观测与评测",
            "run_event 事件回放、llm_call 记账、评测基线；每个结论都能追溯到规则、证据与人工决策",
            "可观测",
            [
                row(
                    card(text("运行总数", size=11, color="$textMuted"), text("128", size=24, weight="600")),
                    card(text("成功率", size=11, color="$textMuted"), text("96.1%", size=24, weight="600")),
                    card(text("累计 tokens", size=11, color="$textMuted"), text("0（离线）", size=24, weight="600")),
                    card(text("累计成本", size=11, color="$textMuted"), text("¥0.00", size=24, weight="600")),
                    name="observability-stats",
                    gap=16,
                ),
                table(
                    ["run", "模块", "事件数", "中断", "复核角色", "耗时"],
                    [["8f2a·4e1c", "M2", "16", "1", "buyer", "1.2s"], ["5c91·ab03", "M1", "12", "1", "buyer", "0.9s"]],
                ),
                card(
                    text("评测摘要（seed=42，合成数据）", size=14, weight="600"),
                    text("M1 verdict 100%（120）· M2 闸门检出 100%（9/9）· M3 行对齐 F1 1.0（80 组）", size=12, color="$textSecondary"),
                    text("重要：金标与实现同源，1.0 是回归一致性而非真实业务精度。", size=11, color="$semanticWarning"),
                    name="observability-eval",
                ),
            ],
            5,
        )
    )
    return pages


def variables() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in TOKENS["colors"].items():
        out[key] = {"type": "color", "value": value}
    for key, value in TOKENS["radius"].items():
        out[f"radius{key.upper()}"] = {"type": "number", "value": value}
    for key, value in TOKENS["spacing"].items():
        out[f"space{key}"] = {"type": "number", "value": value}
    for key, value in TOKENS["fontSize"].items():
        out[f"font{key[0].upper()}{key[1:]}"] = {"type": "number", "value": value}
    out["fontFamily"] = {"type": "string", "value": TOKENS["fontFamily"]}
    return out


# --------------------------------------------------------------------------- 预览渲染


def _resolve(value: Any, var_map: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return var_map.get(value[1:], value)
    return value


def _css(node: dict[str, Any], var_map: dict[str, Any]) -> str:
    styles: list[str] = []
    fill = node.get("fill")
    if fill:
        styles.append(f"background:{_resolve(fill, var_map)}")
    stroke = node.get("stroke")
    if stroke:
        styles.append(f"border:1px solid {_resolve(stroke, var_map)}")
    radius = node.get("cornerRadius")
    if isinstance(radius, int) and radius:
        styles.append(f"border-radius:{radius}px")
    padding = node.get("padding")
    if padding is not None:
        if isinstance(padding, int):
            styles.append(f"padding:{padding}px")
        elif isinstance(padding, list):
            styles.append("padding:" + " ".join(f"{value}px" for value in padding))
    gap = node.get("gap")
    layout = node.get("layout", "none")
    if layout in {"horizontal", "vertical"}:
        styles.append("display:flex")
        styles.append("flex-direction:row" if layout == "horizontal" else "flex-direction:column")
        if gap:
            styles.append(f"gap:{gap}px")
    width = node.get("width")
    height = node.get("height")
    if layout in {"horizontal", "vertical"} and width == "fill_container":
        styles.append("flex:1 1 0;min-width:0")
    elif isinstance(width, int):
        styles.append(f"width:{width}px")
    if layout in {"horizontal", "vertical"} and height == "fill_container":
        styles.append("flex:1 1 0")
    elif isinstance(height, int):
        styles.append(f"height:{height}px")
    if node.get("type") == "text":
        styles.append("white-space:pre-wrap")
        styles.append(f"font-size:{node.get('fontSize', 13)}px")
        styles.append(f"font-weight:{node.get('fontWeight', '400')}")
        styles.append(f"color:{_resolve(node.get('fill', '#000000'), var_map)}")
        styles.append("font-family:Inter,'PingFang SC','Microsoft YaHei',sans-serif")
        styles.append("line-height:1.45")
    if layout == "none":
        x = node.get("x", 0)
        y = node.get("y", 0)
        styles.append(f"position:absolute;left:{x}px;top:{y}px")
    if node.get("innerRadius"):
        styles.append("border-radius:999px")
    return ";".join(styles)


def render(node: dict[str, Any], var_map: dict[str, Any]) -> str:
    node_type = node.get("type", "frame")
    classes = f' class="n-{node_type}"'
    if node_type == "text":
        content = node.get("content", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<div{classes} style="{_css(node, var_map)}">{content}</div>'
    children = node.get("children") or []
    inner = "".join(render(child, var_map) for child in children)
    return f'<div{classes} style="{_css(node, var_map)};overflow:hidden;box-sizing:border-box">{inner}</div>'


def build_preview(pages: list[dict[str, Any]], var_map: dict[str, Any]) -> str:
    cards = []
    for index, page in enumerate(pages):
        cards.append(
            f'<section class="page-card"><h2>{index + 1}. {page["name"]}</h2>'
            f'<div class="artboard"><div class="scale-wrap">{render(page, var_map)}</div></div></section>'
        )
    swatches = "".join(
        f'<span class="swatch"><i style="background:{value}"></i>{name}<code>{value}</code></span>'
        for name, value in TOKENS["colors"].items()
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Procurement AI Workbench — design preview</title>
<style>
  :root {{ font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif; }}
  body {{ margin: 0; background: #f6f7f9; color: #0f172a; }}
  header {{ padding: 32px 40px 8px; }}
  h1 {{ margin: 0 0 6px; font-size: 24px; }}
  .sub {{ color: #475569; font-size: 13px; }}
  .swatches {{ display: flex; flex-wrap: wrap; gap: 10px; padding: 16px 40px 8px; }}
  .swatch {{ display: flex; align-items: center; gap: 6px; font-size: 11px; color: #475569; background: #fff;
             border: 1px solid #e3e7ed; border-radius: 8px; padding: 4px 8px; }}
  .swatch i {{ width: 14px; height: 14px; border-radius: 4px; display: inline-block; }}
  .swatch code {{ color: #94a3b8; }}
  main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(760px, 1fr)); gap: 24px; padding: 16px 40px 48px; }}
  .page-card {{ background: #fff; border: 1px solid #e3e7ed; border-radius: 16px; padding: 16px; }}
  .page-card h2 {{ font-size: 15px; margin: 0 0 12px; }}
  .artboard {{ width: 720px; height: 450px; overflow: hidden; border: 1px solid #e3e7ed; border-radius: 12px; background: #fff; }}
  .scale-wrap {{ width: 1440px; height: 900px; transform: scale(0.5); transform-origin: top left; position: relative; }}
</style>
</head>
<body>
<header>
  <h1>Procurement AI Workbench · 设计稿预览</h1>
  <div class="sub">由 <code>scripts/gen_design_pen.py</code> 从 <code>design/tokens.json</code> 生成；源稿为 <code>design/design.pen</code>（pen.dev schema 2.14，设计 only，D13）。</div>
</header>
<div class="swatches">{swatches}</div>
<main>
{''.join(cards)}
</main>
</body>
</html>
"""


def main() -> None:
    DESIGN_DIR.mkdir(parents=True, exist_ok=True)
    pages = build_pages()
    document = {"version": "2.14", "variables": variables(), "children": pages}
    var_map = {name: spec["value"] for name, spec in document["variables"].items()}

    (DESIGN_DIR / "tokens.json").write_text(json.dumps(TOKENS, ensure_ascii=False, indent=2), encoding="utf-8")
    (DESIGN_DIR / "design.pen").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    (DESIGN_DIR / "preview.html").write_text(build_preview(pages, var_map), encoding="utf-8")

    # 自检：设计稿必须能解析、页面数=6、变量引用全部可解析
    loaded = json.loads((DESIGN_DIR / "design.pen").read_text(encoding="utf-8"))
    assert loaded["version"] == "2.14"
    assert len(loaded["children"]) == 6
    unknown: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        for key in ("fill", "stroke"):
            value = node.get(key)
            if isinstance(value, str) and value.startswith("$") and value[1:] not in var_map:
                unknown.append(value)
        for child in node.get("children") or []:
            walk(child)

    for page_node in loaded["children"]:
        walk(page_node)
    assert not unknown, f"未知变量引用：{sorted(set(unknown))}"
    print(f"design.pen: {len(loaded['children'])} artboards, {(DESIGN_DIR / 'design.pen').stat().st_size} bytes")
    print(f"preview.html: {(DESIGN_DIR / 'preview.html').stat().st_size} bytes")
    print("DESIGN_OK")


if __name__ == "__main__":
    main()

