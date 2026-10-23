# Procurement AI Agent Workbench

企业通用**间接采购**（IT 软硬件 / SaaS / 行政办公 / 市场活动与外包服务）的 AI 工作台：
在「申请 → 询价 → 准入 → 下单 → 收货 → 结算」链路上，用三个 Agent 卡住**信息不完整**、
**供应商风险**、**三单不一致**三个漏损点，并保留人工复核（HITL）兜底。

- 业务与架构唯一事实来源：**[docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md)**（v1.1）
- 开发留痕：[PROGRESS](docs/worklog/PROGRESS.md) · [IMPACT_AND_DECISIONS](docs/worklog/IMPACT_AND_DECISIONS.md) · [GOTCHAS](docs/worklog/GOTCHAS.md)

## 当前状态：W0 骨架（P0）

已落地：

- 后端：FastAPI 骨架、全量表结构 ORM（§3）、Alembic 基线、`run_event` 事件总线（并发安全 seq）、
  SSE 事件流（回放 `?after_seq=` + 心跳 + 终态收尾）、LLM 网关（角色路由 / 跨厂商降级 / 重试 / prompt 缓存 /
  `llm_call` 记账）、演示运行与 HITL 中断恢复。
- 前端：React 19 + Vite + Tailwind + TanStack Query 壳，6 页路由，SSE `fetch` 流 + 纯函数 reducer（18 条单测）。
- 测试：后端单测 **38 passed / 3 skipped**（无 DB 时集成测试自动 skip；有 DB 时 `-m integration` 全绿）；前端 reducer + SSE 解析器 **18 条单测**；
  `tsc --noEmit` 与 `vite build` 均通过。
- **端到端已验证**（2026-10-23）：`alembic upgrade head` → 43 张 public 表 + `vector`/`pg_trgm`；集成测试 3 passed；
  `scripts/smoke_e2e.py` 跑通「建 run → SSE → interrupt → requester 403 → buyer resume → run.end → 快照校验」。
- **数据工厂已验证**（W1-M3 第 1 步）：`scripts/seed.py --seed 42` 生成 **230 份 PDF（含 23 份扫描件）/ 8.5MB**、
  120 份 M1 gold、80 组 M3 gold；DB 落 30 供应商 / 100 SKU / 300 历史成交行 / 120 PR / 80 组三单。

尚未落地（按 Spec §11 里程碑）：M1 前端（字段树 + 结论横幅 + 可勾选补丁 + 证据面板，W2 收尾）、M2 评分（W3）、
pen.dev 画布与部署（W4）。M3 已端到端跑通（抽取 → 对齐 → 容差 → HITL → finalize → 前端工作台）；
M1 后端已跑通（规则 DSL + 混合检索 + 预检图 + HITL 补字段 + 基线评测），W2 补前端后进入 M2。

M3 抽取管线（W1 第 2~3 步）已完成：`DocumentRouter`（文本层占比 + 模板锚点分流）、`pdf_native`
（表格结构优先、正则兜底，产出 Pydantic 模型 + 归一化 bbox）、确定性校验器（算术/税率/日期/数量）、
扫描件 `VLM_REQUIRED` 接缝 + `VisionExtractor`（Fake 可离线测 / LLM 走网关 vision 角色）、
抽取结果落 `extraction_record` + `doc_annotation`、`POST /documents/{id}/extract`，以及
`scripts/extract_eval.py` 基线评测（当前：237 份文档、native 213 / scanned 24，发票字段准确率 100%（n=68），p50 39ms）。

M3 匹配层（W1 第 4 步）已完成：`normalizer`（别名 + 规格抽取 → `spec_bucket`）、`align`（L1/L2/L3 +
匈牙利指派，贪心作消融基线）、`tolerances`（`min(ordered, received)` 上限、五类容差、尾差基准、金额去重），
`scripts/m3_eval.py` 基线：80 组 gold 上 overall **100%**、6 类差异 **F1 100%**、行对齐 **F1 100%**
（同源数据一致性基线；真实精度需真实票据验证）。

M3 编排（W1 第 5 步）已完成：`matching_graph`（LangGraph：`load → normalize → align → reconcile → compose →
interrupt_before → finalize`）+ HITL + finalize 单事务（`qty_invoiced`、尾差池、`invoice.status`、`audit_log`、
`artifact`），`audit_log` 幂等防重复核销；`RunService` 对 `module=m3` 按图分派，重跑 `scripts/seed.py --seed 42`
后可直接 `pytest -m integration` 验证放行/挂起/RunService 全流程。

M3 工作台前端（W1 第 6 步）已完成：`/matching` 页面支持输入 PO/发票号启动真实图运行，SSE 显示节点时间线，
对齐表（PO 行 / 发票行 / 方法 / 拟核销 / 剩余 / 状态）+ 差异卡片（severity 色/规则/建议）+ 财务放行/挂起；
点「查看发票」打开 PDF 抽屉（服务端渲染单页 PNG + 归一化 bbox 高亮，ADR-019）。
`scripts/smoke_m3.py` 一条命令验证全链路（run → interrupt → PDF/注解 → financing 放行 → artifact）。

M1 预检后端（W2 第 1~3 步）已完成：规则 DSL（`app/rules/engine.py`，JSONLogic 白名单 + `required`/`fn` 算子）、
混合检索（`app/retrieval/search.py`：pg_trgm 词法 + 精确名/别名 + 规格桶结构化，RRF 融合，向量 leg 待接入）、
`precheck_graph`（`load → retrieve → rules_engine → semantic_resolve → compose → interrupt → finalize`，无问题自动放行）、
补丁在 finalize 写回 `pr_item.spec` 并留 `audit_log`。
基线：120 条 gold 上 verdict/findings **100%**、检索 top-1 **100%**（`scripts/m1_eval.py`）；
`scripts/smoke_m1.py` 验证真实 HTTP+SSE+HITL 全链路。

M1 前端（W2 第 4 步）已完成：`/precheck` 页面输入 PR 号启动真实预检图，SSE 节点时间线 + 结论横幅（含预算进度条）
+ 字段/规格与检索候选（来源 badge：词法/精确/规格桶）+ findings 卡片 + 建议补丁（∅→值、来源、policy 证据）；
无问题时显示"自动放行"；`采纳并继续` 会走 HITL → finalize 写回 `pr_item.spec` 并落审计。前端 32 单测、构建通过。

## 快速开始（Windows）

```powershell
# 1) 后端依赖（首次）
pwsh -File scripts/dev.ps1 setup

# 2) 起数据库（需要 Docker Desktop 已启动）
pwsh -File scripts/dev.ps1 up
pwsh -File scripts/dev.ps1 migrate
pwsh -File scripts/dev.ps1 seed        # 生成合成数据：237 份 PDF + gold（固定 seed=42）

# 3) 单测 / 集成测试
pwsh -File scripts/dev.ps1 test
pwsh -File scripts/dev.ps1 test-int

# 4) 起服务
pwsh -File scripts/dev.ps1 api         # http://127.0.0.1:8000/docs
pwsh -File scripts/dev.ps1 web-install # 首次
pwsh -File scripts/dev.ps1 web         # http://127.0.0.1:5173
```

无 Docker 时仍可跑：`scripts/dev.ps1 test`（FakeLLM + 无 DB 单测）与前端壳；
`/healthz` 会如实返回 `db:false`，前端顶部显示降级横幅。

## 目录

```text
backend/            FastAPI + SQLAlchemy + Alembic + LLM 网关 + 事件总线
frontend/           React 19 + Vite + Tailwind（6 页壳 + SSE reducer）
docs/PROJECT_SPEC.md      PRD & Agent Spec（唯一的业务/架构事实来源）
docs/worklog/       三份开发留痕（PROGRESS / IMPACT_AND_DECISIONS / GOTCHAS）
scripts/            dev.ps1（Windows 入口）、doctor.ps1（环境自检）
docker-compose.yml  PostgreSQL 16 + pgvector（可选 api 容器）
```

## 关键设计约束（详见 Spec）

1. **确定性优先**：数值与放行判定由代码/规则引擎产出，LLM 的 schema 里没有决定字段。
2. **证据可追溯**：每条 finding/risk 必须带 `evidence`，引用必须能在库中命中。
3. **HITL 边界**：中断前只写"分析结果"，会计/业务事实只在 `finalize` 且用户决策后提交。
4. **可观测**：每次 LLM 调用写 `llm_call`，每个节点写 `run_event`，UI 可回放。
