# design/ — pen.dev 设计稿（设计 only，D13）

> 本目录是**设计资产**，不是前端代码事实源。代码 UI 仍按 D13 手写（`frontend/src`），
> 设计稿与 `frontend/src/styles/tokens.css`、`design/tokens.json` **三处同源**。

## 产物

| 文件 | 说明 |
|---|---|
| `tokens.json` | 设计令牌（颜色/圆角/间距/字号/字体），与 `tokens.css` 的 CSS 变量一一对应 |
| `design.pen` | pen.dev（Pencil）文档，schema **2.14**，6 个 1440×900 artboard，变量即设计令牌 |
| `preview.html` | 静态预览（无需安装 pen.dev 即可查看布局），由同一脚本生成 |
| `README.md` | 本文件 |

生成方式（可重复，不手改 .pen）：

```bash
python scripts/gen_design_pen.py
# design.pen: 6 artboards, ~199KB
# preview.html
# DESIGN_OK
```

脚本内置自检：文档可解析、artboard 数 = 6、所有 `$variable` 引用都存在。

## 页面与 artboard 对应

| # | artboard | 路由 | 关键信息块 |
|---|---|---|---|
| 1 | Run 中心 | `/` | 三模块启动卡、最近运行表、离线成本说明 |
| 2 | M1 采购申请预检 | `/precheck` | PR 输入、结论横幅、明细/检索候选、findings + 补丁 |
| 3 | M2 供应商评估 | `/supplier` | 结论 + 置信度、SVG 五维雷达、6 闸门条、LLM 复核卡（`.pen` 中用 ellipse 示意雷达） |
| 4 | M3 三单匹配 | `/matching` | PO/发票输入、三列对齐表、差异卡片、放行/挂起 |
| 5 | 规则库 | `/rules` | 品类筛选、规则表（JSONLogic 子集） |
| 6 | 可观测与评测 | `/observability` | 运行/成本指标卡、事件表、评测摘要 |

## 在 pen.dev 中打开

1. 安装 [pen.dev](https://www.pen.dev/)（Pencil）；
2. `File → Open` 选择 `design/design.pen`（schema 2.14；若版本更新，pen.dev 会自动转换）；
3. 每个 artboard 是一个页面 frame；变量面板中可看到与 `tokens.json` 同名的颜色/间距/字号变量；
4. 修改设计后**不要**直接改 `.pen` 里的数值——先改 `tokens.json` 或脚本，再重新生成，避免三处漂移。

> 说明：`design.pen` 由脚本生成（节点 id 本地有序生成），用于设计评审/交接，不包含可交互逻辑；
> 若 pen.dev 提示节点 id 归属问题，直接用 `scripts/gen_design_pen.py` 重新生成或在其画布内另存即可。

