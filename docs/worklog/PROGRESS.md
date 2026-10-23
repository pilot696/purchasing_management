# PROGRESS — 里程碑与任务留痕

> 协议来源：`CLAUDE.md` §2。每条留痕必须包含：`[日期时间] [里程碑] 已完成模块 + 关联文件 + 单测通过数 + 下一步待办`。
> 唯一业务事实来源：`docs/PROJECT_SPEC.md`（v1.1）。

## 当前状态

| 项 | 值 |
|---|---|
| 当前里程碑 | **W0（骨架）— 已完成（除依赖 Docker 的端到端冒烟）** |
| 当前分支/提交 | `main` 首个提交（P0 全量） |
| 最近一次全量测试 | 后端 **28 passed / 3 skipped**（0.43s）+ **integration 3 passed**（DB 就绪时）；前端 18 passed；`tsc --noEmit` 0；`vite build` 通过；`smoke_e2e.py` OK |
| 下一步 (Next) | 启动 Docker Desktop → `scripts/dev.ps1 up/migrate` → 跑 3 条 integration 测试 → 端到端 SSE 演示；然后进入 W1-M3（数据工厂 → 抽取 → 对齐 → 容差） |

---

## 留痕记录（倒序追加，最新在上）

### [2026-10-23 20:05] W4 演示闭环：运行历史/收件箱/规则库/可观测 + 真实网关 + 截图
- **变更文件**：
  - 后端：新增 `api/v1/{rules,observability}.py`、`schemas/catalog.py`、`tests/test_ops_api.py`；`schemas/runs.py` 增加列表/收件箱/Trace 模型；
    `services/runs.py` 增加 `list_runs / list_review_inbox / trace / observability_summary / rule_catalog`（含 llm_call 用量汇总回 agent_run）；
    `main.py` 增加 SPA 深链回退（`SPAStaticFiles`）；`llm/{provider,config,router,models.yaml}` 接入 OpenCode Go（session 头、extra_headers、dotenv 展开、FakeLLM 禁缓存）；
    `agents/supplier.py` 真实复核（schema 注入 prompt、JSON 容错、越界字段归一化、run_id 归属）
  - 前端：`pages/{RunCenter,Rules,Observability}.tsx` 重写；`lib/api.ts` 增加 6 组接口；M1/M2/M3 支持 `?run=` 深链；`styles/tokens.css` 补 cancelled 状态
  - 脚本/资产：`scripts/{demo_runs,screenshots}.py`、`docs/screens/*.png`（7 张）、`docs/RESUME_TEMPLATE.md`、`evals/latest.json`（real_llm_smoke）、README/Spec v1.14
- **核心实现**：
  - Run 中心：KPI（运行数/待复核/成功率/LLM 调用）+ 模块入口 + 待复核收件箱（M1/M2/M3 摘要/verdict/去处理）+ 近期运行表（点行回放）；
  - 可观测：`summary`（成功率只按终态计算、P50/P95、tokens/cost、LLM 延迟/缓存）+ Trace（节点时间线 + llm_call 记账）+ 评测基线卡（读 evals/latest.json）；
  - 规则库：品类分桶 + 17 条规则（expression 原文）+ 容差策略表；
  - 真实网关：OpenCode Go 端点 `https://opencode.ai/inference/go/openai/v1`；`x-opencode-session` + UA；`${ENV}` 从 `.env` 解析；FakeLLM 模式禁用 prompt 缓存防污染；M2 schema 全量注入 + JSON 围栏容忍 + claim 超长截断。
- **本轮踩坑（GOTCHAS G-039~G-044）**：FakeLLM 污染 prompt 缓存、dotenv 不参与 `${ENV}` 展开、网关缺 session 头 400、reasoning tokens 截断 JSON、run 用量未归因、SPA 深链 404。
- **验证结果**：
  - 后端单测 **88 passed / 22 skipped**；DB 集成 **19 passed / 3 skipped**（新增 4 条演示闭环 API 测试）；前端 **39 passed**、tsc 0 错、build **381.67KB / gzip 114.45KB**；
  - 真实 LLM：M2 `llm_risk_review` 完整跑通（deepseek-v4.1-flash，单次 1658→2662 tokens、¥0.0131；缓存命中 cost=0），run 用量与 `llm_call` 归属正确（16.6 万 tokens 汇总在 run 上）；
  - e2e 截图：`SMOKE_*` 契约未回归；Playwright 实拍 7 张（Run 中心/M1/M2/M3/规则/可观测/设计稿）到 `docs/screens/`；
  - 服务端深链：`/precheck?run=...` 返回 SPA（200），旧 `StaticFiles(html=True)` 的 404 已修。
- **下一步 (Next)**：无剩余里程碑；可选：真实模型全量回填（四套 eval）、视觉适配器（Qwen Anthropic 端点）、向量 leg（embedding 供应商）、M1 补丁逐条编辑、运行分页筛选增强。

### [2026-10-23 19:10] W4：评测报告 + pen.dev 设计资产 + 容器化部署 + 简历数字回填
- **变更文件**：新增 `docs/EVAL_REPORT.md`、`evals/latest.json`、`docs/DEPLOYMENT.md`、根 `Dockerfile`、`.dockerignore`、`design/{tokens.json,design.pen,preview.html,README.md}`、`scripts/gen_design_pen.py`；修改 `docker-compose.yml`（api 多阶段构建 + healthcheck）、`README.md`（状态/部署/链接）、`docs/PROJECT_SPEC.md`（§9.4/§12/版本 v1.13）、`Makefile`（eval/up-app/seed-docker/design）
- **核心实现**：
  - **评测**：四套基线汇总为 `EVAL_REPORT.md`（抽取/M1/M2/M3/工程验证/复现命令），并写清 7 条诚实边界（合成数据、金标同源、离线 FakeLLM、向量 leg 未接、M2 无专家 gold、MemorySaver 单进程、人工基线估算）；`evals/latest.json` 为可机读快照，未测项列入 `pending_measurements`；
  - **设计资产**：`scripts/gen_design_pen.py` 以 `tokens.json` 为源生成 pen.dev schema 2.14 文档（6 个 1440×900 artboard：Run 中心/M1/M2/M3/规则库/可观测）+ 静态 `preview.html`；生成即自检变量引用与页面数（`DESIGN_OK`），避免手改 `.pen`；
  - **部署**：根 `Dockerfile` 两阶段（node 构建前端 dist → python 运行时，保留 backend/scripts/data 相对布局，FastAPI 同端口托管 SPA）；compose api 改为根上下文构建、启动自动 `alembic upgrade head`、内置 healthcheck；`DEPLOYMENT.md` 记录 compose/本机两种形态、配置项、生产限制（MemorySaver 单副本、迁移、缓存、SSO/TLS）；
  - **简历数字**：Spec §12 的 `{{...}}` 全部替换为实测值（M1 verdict 100%/findings F1 1.0/检索 top-1 100%、M2 闸门 100%/误报 0%/冷启动 100%/flip 5.55%、M3 行对齐 F1 1.0/差异 recall 1.0、发票字段 100%/p95 46.14ms、测试与 e2e 数），未测项保留"未测/待接入"标注，不编数字。
- **验证结果**：
  - `python scripts/gen_design_pen.py` → `DESIGN_OK`（6 artboards，design.pen≈199KB，preview.html≈98KB，变量引用自检通过）；
  - `docker compose config --quiet` 通过；本机生产形态（uvicorn 托管 `frontend/dist`）：`/` 200 text/html、`/assets/index-*.js` 200（360,902B）、`/healthz` 200；
  - **Docker 镜像构建未完成**：本机 Docker 无法访问 Docker Hub（`auth.docker.io` 连接超时），属网络环境限制（G-038），已记录并在 DEPLOYMENT §1 给出代理前置条件；应用层托管路径已用非容器方式验证；
  - 本轮未改后端/前端业务代码：上一轮全量测试结果仍有效（后端 88 passed/18 skipped、集成 17 passed/1 skipped、前端 39 passed、四套 e2e OK）。
- **下一步 (Next)**：无剩余里程碑；可选：接入真实 LLM key 回填成本/质量指标、配置 Docker Desktop 代理后跑 `docker compose up -d --build` 复核镜像。

### [2026-10-23 18:55] W3-M2：供应商准入与履约评估（五维评分 + 6 闸门 + LLM 复核者 + 看板）
- **变更文件**：新增 `backend/app/scoring/supplier.py`、`backend/app/agents/supplier.py`、`backend/app/schemas/supplier.py`、`backend/tests/{test_scoring,test_supplier_graph}.py`、`scripts/{m2_eval,smoke_m2}.py`、`evals/m2_baseline.json`、`frontend/src/lib/m2.ts`(+`.test.ts`)；重写 `frontend/src/pages/Supplier.tsx`；修改 `services/runs.py`（M2 图分派）、`main.py`（装配 supplier 图）、`seed/dataset.py`（证照基准有效期）、`pages/RunCenter.tsx`（M2 跳转）、`scripts/smoke_e2e.py`（走真实 M2 契约）
- **核心实现**：
  - **确定性评分**（纯函数）：price=100−120·max(0,index−1)（≤0.9 封顶）、delivery=OTD×100（延迟>5 天 ×0.9）、quality=60·qc+40·(1−defect)、service 分段(4/8/24/48h)+升级率扣分、compliance 扣项（临期−20/范围−15/缺安全资质−30/异常−100）；品类权重从 `supplier_weight` 读；
  - **6 条硬闸门**：G1/G3/G4/G5 否决；G2（范围，需证照原文引用）与 G6（安全资质，30 天补交）为**条件项不否决**；闸门命中直接 `reject/frozen` 并**跳过评分与 LLM**；黑名单并入 G3；
  - **敏感性**：权重 U(0.8,1.2) 扰动 300 次（固定 seed=42），输出 flip_rate + most_sensitive；
  - **LLM 复核者**：schema 无分数字段，`evidence_refs` 逐条校验证据包 id，失败条目丢弃并计入 `llm_dropped_refs`；离线/网关失败显式 `llm_status=unavailable`，不伪造正文；
  - **finalize 单事务**：artifact(kind=supplier_eval)+supplier_eval+audit_log；approve→active、conditional→conditional、frozen→frozen，reject 不改状态；HITL 前零写入。
- **本轮踩坑（GOTCHAS G-034~G-037）**：证照基准日过期导致随机 G1 误杀、闸门短路路径 llm_status 缺省错误、冷启动置信度未强制 low、评测把 `risk_flags=False` 当预期闸门。
- **验证结果**：
  - **M2 基线（30 家供应商，离线）**：闸门检出率 **100%**（9/9 注入异常）、闸门误报 **0%**、冷启动一致率 **100%**（12 家：conditional+low）、结论分布 approve 2 / conditional 21 / reject 3 / frozen 4、敏感性均值 flip_rate **5.55%**、非闸门供应商五维齐全；
  - **M3/M1 回归**：overall/F1 仍 100%，M1 verdict/findings/检索 top-1 仍 100%；
  - 单测 **88 passed / 18 skipped**；集成 **17 passed / 1 skipped**；前端 **39 passed**、tsc 0 错、build 360.9KB（gzip 110.3KB）；
  - 真实 e2e：`SMOKE_M2=OK`（SUP-001 approve 94.43、5 维、requester 403→buyer 200）、`SMOKE_M1/M3/E2E=OK`。
- **下一步 (Next)**：W4（pen.dev 设计资产 + 部署 + 评测报告 + 简历数字更新）

### [2026-10-23 17:40] W2-M1 第 4 步：M1 前端工作台（`/precheck`）
- **变更文件**：新增 `frontend/src/lib/m1.ts`(+`m1.test.ts`)、重写 `frontend/src/pages/Precheck.tsx`、修改 `pages/RunCenter.tsx`（M1 卡片跳转）、`docs/PROJECT_SPEC.md`（页面 2 实现口径 + v1.11）
- **核心实现**：
  - 页面：PR 号启动 → SSE 节点时间线 → 结论横幅（三色 + 类别）+ **预算进度条**（可用 vs 预估，超预算红色）→ 自动放行提示（requires_review=false 时）；
  - 左栏：申请明细（品名/规格桶/数量/规格键值）+ 检索候选（名称、桶、RRF 分数、来源 badge 词法/精确/规格桶）；
  - 右栏：findings 卡片（类型 + 规则号 + severity + 字段 + policy 证据）、建议补丁（路径、∅→值、来源/ref）；
  - 动作：`采纳并继续`（accept）/`退回补充`（reject），buyer/admin 角色提示；RunCenter 的 M1 卡片改为跳转 `/precheck`。
- **验证结果**：`vitest` **32 passed**（新增 m1 6 条：报告解析、verdict/finding/patch 文案、预算条、权限、候选来源）；`tsc` 0 错；`vite build` 成功（99 modules，345KB / gzip 107KB）
- **下一步 (Next)**：W3-M2（五维确定性评分 + 6 条硬闸门 + LLM 复核者 + 供应商看板页）

### [2026-10-23 17:35] W2-M1 第 1~3 步：规则 DSL + 混合检索 + 预检图 + M1 基线
- **变更文件**：
  - 新增 `backend/app/rules/{engine,loader}.py`、`backend/app/retrieval/search.py`、`backend/app/agents/precheck.py`、`backend/tests/{test_rules,test_precheck_graph}.py`、`scripts/{m1_eval,smoke_m1}.py`、`evals/m1_baseline.json`
  - 修改 `backend/app/services/runs.py`（M1/M3 共用 `_execute_graph`，自动放行）、`main.py`（装配 M1 图）、`seed/dataset.py`（规则与 gold 对齐、SKU 规格跟随品名）、`scripts/smoke_e2e.py`（demo 冒烟改走 m2 占位图）
- **GitNexus 影响面**：`RunService` 的图分派重构（m1/m3 共用 HITL/finalize 流程）影响 `/runs` 全部模块；`seed/dataset.py` 的规则/规格调整改变数据工厂输出（两套基线已重跑）；`detect_changes` 见提交记录。
- **核心设计执行**：
  - **规则 DSL**（`app/rules/engine.py`）：JSONLogic **白名单**求值器，自定义 `required`/`fn` 算子；表达式为真=命中；未声明算子直接抛错；输出 finding 带 `rule_code/evidence`；
  - 数据/规则对齐：qty 规则 type 改 `stat_outlier`、预算规则 severity 与 gold 一致、owner 字段移入 spec、ADMIN 增加数据敏感 policy 规则、补齐 SAAS/MKT 的预算/数量通用规则（共 17 条）；
  - **混合检索**（`app/retrieval/search.py`）：词法(pg_trgm) + 精确名/别名 + 结构化(规格桶) 三路 + RRF；向量 leg 留可注入位（当前网关无 embed 角色，ADR-020）；
  - **预检图**：`load_subject → retrieve → rules_engine → semantic_resolve → compose_report →（interrupt）→ finalize`；
    无 findings/patches 时 **自动放行**（不创建复核任务），否则 HITL 编辑补丁；finalize 才写 `pr_item.spec` + `audit_log` + artifact + `pr.status=prechecked`；
  - `scripts/m1_eval.py` 产出 `evals/m1_baseline.json`；`scripts/smoke_m1.py` 覆盖真实 HTTP+SSE+RBAC+HITL 全链路。
- **本轮踩坑（GOTCHAS G-029~G-033）**：规则求值 dict 字面量提前比较、psycopg NULL 参数类型推断与 JSONB CAST、`pr.status` varchar(16) 溢出、目录同名不同配导致检索指标失真、demo 冒烟被真实图接管。
- **验证结果**：
  - **M1 基线（120 条 gold）**：verdict **100%**、findings 精确匹配 **100%**（micro P/R/F1=1.0）、检索 top-1 **100%（105 条无歧义样本）**、平均 60% 用例产生补丁（共 120 条）、耗时 1.3s；
  - **M3 基线回归**：overall/F1/行对齐仍 **100%**；
  - 单测 **75 passed / 15 skipped**；集成 **14 passed / 1 skipped**；
  - 真实 e2e：`SMOKE_M1=OK`（verdict=needs_info，findings=2/patches=1，requester 403→buyer 200，applied_patches=1）、`SMOKE_M3=OK`、`SMOKE_E2E=OK`（m2 占位图）。
- **下一步 (Next)**：M1 前端（字段树 + 结论横幅 + findings + 可勾选补丁 + 证据面板）→ 然后 W3-M2

### [2026-10-23 17:15] W1-M3 第 6 步 + 前端：M3 工作台（对齐表 + 差异清单 + PDF 抽屉 + HITL）
- **变更文件**：
  - 前端：新增 `frontend/src/components/PdfDrawer.tsx`、`frontend/src/lib/m3.ts`(+`m3.test.ts`)，重写 `pages/Matching.tsx`，修改 `lib/api.ts`（文档/快照接口）、`lib/events.ts`（ReviewTask 载荷）、`pages/RunCenter.tsx`（M3 卡片跳转）
  - 后端：`api/v1/documents.py`（`/file`、`/page.png`）、`agents/matching.py`（报告补 `documents/po_lines/invoice_lines/po_total/invoice_total`、state 增加 `documents`）、`services/runs.py`（review 载荷附完整 report）
  - 新增 `scripts/smoke_m3.py`
- **GitNexus 影响面**：`GET /documents/{id}/annotations` 上游新增两个只读端点；`matching_graph` 的 state/report 字段变更影响 RunService review 载荷与前端渲染；`detect_changes` 见提交记录。
- **核心设计执行**：
  - 页面：PO/发票号输入 → `POST /runs(m3)` → SSE 节点时间线 → 对齐表（PO 行/发票行/方法/拟核销/剩余/状态）→ 差异卡片（severity 色 + 规则 + 建议）→ `放行核销/挂起`（角色不足时提示切 finance）；
  - PDF 抽屉：服务端渲染单页 PNG（`page.png`）+ 归一化 bbox 百分比覆盖层；打开时先 `POST /documents/{id}/extract` 再取 `annotations`，扫描件无注解时给出提示；
  - 报告补齐渲染所需原始行与文档 id（此前 review 载荷只有 diff/数量，前端无法画对齐表）。
- **本轮踩坑（GOTCHAS G-027/G-028）**：LangGraph `TypedDict` 未声明的 state key 被静默丢弃（`documents` 变 `{}`）；bbox 百分比浮点误差（`0.1*100=10.000000000000002%`）导致覆盖层偏移。
- **验证结果**：
  - 前端：`vitest` **26 passed**（新增 m3 8 条）；`tsc` 0 错；`vite build` 成功（98 modules，332.9KB / gzip 104.8KB）
  - 后端：unit **66 passed / 12 skipped**；integration **11 passed / 1 skipped**
  - **`scripts/smoke_m3.py` 真实 HTTP 全链路 OK**：m3 run → 节点事件（load/normalize/align/reconcile/compose）→ interrupt（overall=hold，3 项差异）→ `page.png` 111KB → 抽取后 annotations=3 → buyer 403 / finance 200 → finalize → run.end=succeeded → artifact.decision=accept
- **下一步 (Next)**：W1 完成，进入 W2-M1（规则 DSL + 混合检索 + 预检图 + HITL 补字段）

### [2026-10-23 16:00] W1 回归：e2e 冒烟发现并修复 SSE 终端竞态（GOTCHAS G-026）
- **变更文件**：`backend/app/api/v1/runs.py`（SSE 终端宽限 2×50ms，避免状态落库与尾部事件写入之间的窗口丢 `run.end`）
- **触发原因**：GitNexus `detect-changes` 对第 5 步给出 **risk=high / 6 条执行流**（`lifespan`、`RunService._execute/_set_status` 等共享生命周期符号），
  按纪律必须做真实回归，于是启动 API（8012 端口）跑 `scripts/smoke_e2e.py`：首跑复现"未收到 run.end"，修复后通过。
- **验证结果**：`SMOKE_E2E=OK`（8 events → interrupt → 403 越权 → resume → finalize/run.end → snapshot succeeded/artifact）；
  unit **66 passed / 12 skipped**；integration **11 passed / 1 skipped**
- **已知限制（Spec v1.8）**：M3 checkpointer 为进程内 `MemorySaver`，重启后不能恢复中断运行；M3 单张发票/次运行
- **下一步 (Next)**：M3 前端（对齐表 + PDF 抽屉 + bbox 高亮）→ W2 M1

### [2026-10-23 15:50] W1-M3 第 5 步：matching_graph（LangGraph）+ HITL + finalize 事务
- **变更文件**：新增 `backend/app/agents/{__init__,matching}.py`、`backend/alembic/versions/0002_invoice_remark.py`、`backend/tests/test_matching_graph.py`；修改 `backend/app/services/runs.py`（M3 按 module 分派图）、`backend/app/main.py`（装配图）、`backend/app/models/tables.py`（`invoice.remark`）、`backend/app/seed/persist.py`（写入 remark）
- **GitNexus 影响面**：`RunService._execute` 增加 M3 分支、`_Handle` 增加 `review_id`；`main.py` lifespan 新增图装配；`Invoice` ORM 加列需迁移（0002），影响 persist/graph/文档接口读取。`detect_changes` 见提交记录。
- **核心设计执行**：
  - 图节点：`load_subject → normalize → align → reconcile → compose_report →（interrupt_before）→ finalize`，每节点发 `node.start/end`（llm_calls=0, tool_calls=0）；
  - **决策前零会计写入**：`match_run/match_line/match_diff` 也统一放到 `finalize` 事务，避免挂起后残留半成品；
  - **finalize 幂等**：以 `audit_log(target_table='match_run', run_id)` 是否存在判断，重入不重复累加 `qty_invoiced`；
  - 放行写：`qty_invoiced`、尾差池、`invoice.status=matched`、`audit_log(release/update)`、`artifact(match_report)`、`agent_run.succeeded`；挂起写：`invoice.status=hold`、`audit_log(hold)`、`match_run.status=hold`；
  - `RunService` 对 `module=m3` 用图驱动（M1/M2 仍走演示节点，W2/W3 替换）；checkpointer 用进程内 `MemorySaver`（限制已记入 Spec v1.8）。
- **本轮修复的 schema 缺口**：`invoice` 表没有 `remark`，导致 DB 路径下 `terms_conflict` 静默漏判（GOTCHAS G-024）；新增列 + Alembic 0002 + 种子/图同步。
- **验证结果**：
  - 新增图级集成测试 **3 passed**：①放行→`qty_invoiced`/审计/`match_diff` 集合与 gold 一致/artifact；②挂起→会计零写入 + `invoice.status=hold`；③`RunService` 全流程（start→awaiting_review→resume→succeeded）
  - 全量：unit **66 passed / 12 skipped**；integration **11 passed / 1 skipped**（重复运行防脏数据 skip）
  - 迁移与重灌：`alembic upgrade head` 成功（0002），`seed.py --seed 42` 成功
- **下一步 (Next)**：M3 前端（对齐表 + PDF 抽屉 + bbox 高亮 + 差异徽标 + HITL 放行弹层）→ W2 M1（规则 DSL/检索/预检图）

### [2026-10-23 15:30] W1-M3 第 4 步：normalizer + align（匈牙利）+ tolerance/reconcile + M3 基线
- **变更文件**：新增 `backend/app/matching/{normalize,align,tolerance}.py`、`data/aliases.yaml`、`backend/tests/test_matching.py`、`scripts/m3_eval.py`、`evals/m3_baseline.json`；修改 `backend/app/seed/dataset.py`（扫描件精确配额、到货不足仅限 qty_over 组、terms_conflict 强制有合同、date_overdue 日期倒推）
- **GitNexus 影响面**：`matching/*` 为新增纯函数层；`normalize`/`align` 将被 matching_graph 与 W2 检索复用；`dataset.py` 改动仅影响数据工厂输出与 gold（已重跑两套基线）
- **核心设计执行**：
  - `normalize`：**规则优先**（品类别名 → family；内存/存储成对正则 → 单值兜底；SAAS/外包量纲），产出 M1/M2/M3 共用 `spec_bucket`；
  - `align`：L1 sku 精确 → L2 规格桶 → L3 difflib 语义；`scipy.optimize.linear_sum_assignment` 全局指派（0.75 接受 / 0.90 非歧义），`align_greedy` 作为消融基线；
  - `tolerance`：可开票上限 `min(ordered, received) - qty_invoiced`（服务类回退 ordered）；`price/tax/qty_over` 等行级差异**自动抑制重复 amount**；`tail` 用发票行合计作基准；`overall` 只在全部 diff 产生后判定；
  - 仍坚持"分析先行"：`reconcile` 只产出 `consumed_qty/remaining_qty/proposed_tail`，**不写** `qty_invoiced` 与尾差池（留给 `finalize`）
- **验证结果**：
  - `pytest tests/test_matching.py` → **13 passed**；全量后端 **66 passed / 9 skipped** + integration **9 passed**
  - **M3 基线（80 组）**：`overall_accuracy=100%`、6 类差异 `micro P/R/F1=1.0`（tp=60, fp=0, fn=0）、行对齐 `F1=1.0`（80/80）
  - **抽取基线（重跑，237 份文档）**：native 213 / scanned 24（15% 组配额）；invoice 字段准确率 **100%（n=68）**；0 needs_review；p50 **39ms** / p95 46ms
- **数据/代码修复（详见 GOTCHAS G-020~G-023）**：尾差浮点误报、`overall` 用过期 `hard_types` 快照导致 terms_conflict 漏 hold、随机到货不足产生 gold 未建模差异、扫描件比例漂移
- **下一步 (Next)**：`matching_graph`（LangGraph 节点编排 + `interrupt_before` HITL）+ `finalize` 事务（`qty_invoiced`/尾差池/审计）+ `/runs` M3 接入 + 前端对齐表/PDF 抽屉

### [2026-10-23 15:05] W1-M3 第 3 步：扫描件 VLM 路径 + 抽取落库 + 文档接口 + 基线评测
- **变更文件**：
  - 新增 `backend/app/extraction/{vlm,render_pages,persist}.py`、`backend/app/llm/multimodal.py`、`backend/app/api/v1/documents.py`、`backend/tests/{test_vlm_extraction,test_documents_api}.py`、`scripts/extract_eval.py`
  - 修改 `backend/app/extraction/service.py`（async + vision 接缝 + hybrid 回退）、`backend/app/schemas/extraction.py`（`extractor/repair_attempts/structured_errors`）、`backend/app/llm/{types,provider}.py`（多模态 content）、`backend/app/api/deps.py`（SessionDep）、`backend/app/api/v1/__init__.py`
- **GitNexus 影响面**：`app/llm/types.py::Message` 是**公共底座**（所有 LLM 调用共用），改动仅把 content 扩为 `str | list[dict]`，
  `impact "Message"` 上下游为 gateway 与 extraction（W1）；`detect_changes` 结果见提交前记录。
- **核心设计执行**：
  - **VLM 接缝**：`VisionExtractor` Protocol；`FakeVisionExtractor`（零网络单测）+ `LLMVisionExtractor`（走网关 `vision` 角色，
    qwen3.8-max → glm-5.3-flash 跨厂商降级）；`build_vision_messages` 生成 OpenAI 兼容 image data URL。
  - **repair 语义分层**：Schema/Pydantic 失败 → 回填错误重试（`max_attempts=3`，即 2 次 repair）；
    **业务校验失败（无行项目/缺销售方等）不重试**，直接进 `needs_review_fields` 交人工——避免把模型当规则引擎用。
  - **落库**：`persist_extraction()` 幂等替换 `doc_annotation`，追加 `extraction_record`（extractor/status/repair_attempts/error），回写 `doc` 元数据。
  - **接口**：`GET /documents/{id}`、`GET /documents/{id}/annotations`、`POST /documents/{id}/extract`（含路径越界防护）。
  - **基线评测**：`scripts/extract_eval.py` 遍历全量文档，产出 `evals/extraction_baseline.json`。
- **验证结果**：
  - 单测：**53 passed / 9 skipped**（新增 VLM 5 条：扫描件抽取、repair 恢复、repair 耗尽、业务缺失→needs_review、PNG 渲染）
  - 集成：**9 passed**（新增 documents API 3 条：原生发票抽取+注解、扫描件 needs_review、重复抽取注解幂等）
  - **抽取基线（234 份文档）**：`pdf_native 204 / scanned 30`；contract ok 60、invoice ok 63 + needs_review 1、po ok 80；
    **发票字段准确率 invoice_no / amount_tax_incl / seller_name = 100%（n=64）**；延迟 **p50 37ms / p95 43ms**
- **下一步 (Next)**：normalizer（别名+规格抽取 → spec_bucket）→ align（L1/L2/L3 + 匈牙利）→ tolerance（含 `min(ordered, received)` 上限）→ matching_graph + HITL

### [2026-10-23 14:50] W1-M3 第 2 步：抽取管线（DocumentRouter + pdf_native + validators + bbox）
- **变更文件**：新增 `backend/app/schemas/extraction.py`、`backend/app/extraction/{router,pdf_native,validators,service}.py`、`backend/tests/test_extraction.py`
- **GitNexus 影响面**：纯新增模块；`app/schemas/extraction.py` 将成为 M3 图与 API 的公共类型（后续改字段需 impact）
- **核心设计执行**：
  - `DocumentRouter`：`text_ratio` 与 Spec 同量纲，阈值 0.15/0.05；模板锚点命中 ≥2 判定（发票/PO/合同）
  - `pdf_native`：**以 `extract_tables` 为主、正则兜底**（表头 `标签 值` 对映射 + 行项目表列名映射），
    发票/PO/合同三类解析器统一产出 `DocLine`
  - bbox 注解：`page.search` → `extract_words` 两级回退，归一化坐标**以左上为原点**（前端 CSS overlay 直接用）
  - 校验器不抛异常：返回 `ValidationIssue`（算术/税率白名单/日期/数量/账期），`high` 进入 `needs_review_fields`
  - 扫描件不硬解析：返回 `VLM_REQUIRED` + `needs_review`，为下一步 VLM 留出明确接缝
- **验证结果**：`pytest tests/test_extraction.py` → **8 passed**（路由 3 类模板、扫描件、发票/PO/合同字段与源数据一致、bbox 归一化、尾差无 high、篡改合计可检出、扫描件待 VLM）；
  全量后端 **48 passed / 6 skipped** + integration **6 passed**
- **下一步 (Next)**：扫描件 VLM 抽取（FakeVLM 可测）+ `extraction_record`/`doc_annotation` 落库 + `POST /documents/{id}/extract`

### [2026-10-23 14:35] W1-M3 自查：修复 5 个数据/配置缺陷（其中 3 个会影响评测可信度）
- **变更文件**：`backend/app/seed/{dataset,persist}.py`、`backend/tests/{test_seed,test_seed_integrity}.py`、`docker-compose.yml`、`docs/PROJECT_SPEC.md`（§3.5/§7.2 阈值标定 + v1.4）
- **缺陷与修复**：
  1. **`po_line.qty_received` 未落库**（收货行只写 `receipt_line`）→ M3 的 `min(ordered, received)` 会把全部发票判超发。修复：落库前按收货行汇总回填 `qty_received`（`qty_invoiced` 保持 0，待匹配后提交）；
  2. **`text_ratio` 口径与 Spec 路由阈值不符**（种子用 `len/900`，Spec 用"每 1e3 pt² 字符数"且阈值 0.6）→ 实测原生 PO 仅 0.49，会被误判 scanned。修复：种子改用面积口径；并**把 Spec 阈值从 0.6 标定为 0.15**（原生 PO 0.435~0.509 / 发票 0.698~0.788 / 合同 0.76~0.81 / 扫描件 0）；
  3. **PR gold 与真实预算不一致**：`over_budget` 原先只在 anomaly 分支强制为真，clean 样本可能实际已超预算但 gold 写 pass → M1 评测失真。修复：`over_budget` 一律由 `estimated > available` 推导；新增 6 条 `policy_block` 样本（verdict=block），三类 verdict 均有样本；
  4. **PR owner/cost_center 丢失**：`pr_item` 无该列且种子写在 item 顶层 → 持久化时被丢弃。修复：统一写入 `spec["owner"]` / `spec["cost_center"]`，gold 路径同步为 `items[0].spec.owner`；
  5. **`docker compose` 强依赖 `backend/.env`**：新克隆仓库缺该文件时 `env_file` 直接报错。修复：`env_file` 改为 `{path, required: false}`，默认值走 `environment`。
- **验证结果**：`pytest tests/test_seed.py` → **12 passed**；`-m integration` → **6 passed**（新增 seed 完整性 4 条：收货一致性、doc 文本层口径、规模、种子存在性）；重新 seed：234 份 PDF / 11.12MB；DB ratio 分布已对齐（native ≥0.435）；`po_line` 示例 `ordered=15, received=8, invoiced=0`
- **下一步 (Next)**：M3 抽取管线（DocumentRouter → pdf_native → validators → extraction_record）

### [2026-10-23 14:15] W1-M3 第 1 步：数据工厂完成（master + 230 份 PDF + 自动 gold）
- **变更文件**：
  - 新增 `backend/app/seed/{__init__,config,dataset,render,degrade,persist,runner}.py`、`scripts/seed.py`
  - 新增 `backend/tests/test_seed.py`（10 条纯函数测试）
  - 更新 `backend/pyproject.toml`、`backend/requirements.txt`（reportlab / pillow / pypdfium2 / pdfplumber）、`Makefile`（`make seed`）、`scripts/dev.ps1`（`seed` 命令）
- **GitNexus 影响面**：纯新增模块 + 配置文件；未修改任何既有符号（`detect_changes` 结果见提交前记录）
- **核心设计执行**：
  - `build_dataset(cfg)` 与 `persist_dataset(cfg)` **纯函数与副作用分离**，保证单测可离线验证确定性；
  - `rl_config.invariant = 1` 让 ReportLab 输出字节稳定；中文用内置 CID 字体 `STSong-Light`（零外部字体）；
  - 扫描件退化：pypdfium2 渲染 → Pillow 旋转/模糊/JPEG/阴影 → ReportLab 重打包（**无文本层**）；
  - 异常覆盖按 `DIFF_PLAN` 精确分配槽位（每个槽位落到"尚未出现该类型"的组），保证 §10.3 每类 ≥10 正样本；
  - `requires_gr` 驱动：SaaS/市场外包不生成收货单；实物类收货不足会压低"可开票上限"。
- **验证结果**：
  - `pytest tests/test_seed.py` → **10 passed**（确定性哈希、规模、异常覆盖、整体结论一致性、发票算术、PDF 文本层、扫描件无文本层、合同付款条款）；
  - `scripts/seed.py --seed 42` → **SEED_OK**：230 份 PDF / 8.52MB；`gold_m1=120`、`gold_m3=80`；
  - DB 实证：supplier 30、sku 100、po_history 300、pr 120、contract 70（=80−10 无合同）、po 80、receipt 72、invoice 80、doc 230；
    `doc` 文本层分布 native contract 59 / invoice 68 / po 80，scanned contract 11 / invoice 12（text_ratio=0）；
    contract↔doc / invoice↔doc 外键全部对齐（70 / 80）
  - 全量：后端 **38 passed / 3 skipped** + integration **3 passed**
- **下一步 (Next)**：M3 抽取管线——`DocumentRouter` 分流 + `pdf_native`（pdfplumber 文本/表格/字符坐标 bbox）+ 校验器 + `extraction_record` 落库

### [2026-10-23 14:00] W0 收尾：PostgreSQL 启动 + 迁移 + 集成测试 + 端到端 SSE 冒烟全部通过
- **变更文件**：`backend/run.py`（新增，Windows 事件循环入口）、`backend/app/core/loop.py`（新增）、`backend/app/main.py`（导入时兜底切策略）、`backend/tests/conftest.py`（pytest 前切策略）、`scripts/smoke_e2e.py`（新增，端到端冒烟）、`scripts/dev.ps1`（api / test-int 命令修正）、`scripts/_debug_db.py`（临时调试脚本，已删除）
- **GitNexus 影响面**：`impact "create_app"` → **LOW**（depth 1，仅 main.py）；`impact "db_ping"` → **LOW**（上游 healthz/readyz）；`detect-changes` 结果见提交前记录
- **环境打通**：
  - Docker daemon 就绪（Server 29.1.3），`pgvector/pgvector:pg16` 拉取成功（首次 pull 遇 EOF，重试 1 次成功，共 631MB）
  - `alembic upgrade head`：`0001_initial` 应用成功；**43 张 public 表**（42 业务表 + `alembic_version`）、扩展 `vector` + `pg_trgm` 就位；三张语料表 `embedding` 为 `vector` 类型；v1.1 的 `requires_gr` / `last_seq` 列存在
- **关键排障（GOTCHAS G-009）**：端到端一开始失败，根因不是密码/端口，而是 **psycopg 异步驱动不支持 Windows 默认 ProactorEventLoop**；
  uvicorn 0.54 又在单进程模式把 loop factory 硬编码为 `ProactorEventLoop`。修复：`run.py` 覆盖 `config.get_loop_factory` 为 `SelectorEventLoop`，
  pytest conftest 与脚本在创建循环前切策略。修复后 `/healthz` 从 `db:false` 变为 `db:true`
- **验证结果**：
  - 集成测试 `pytest -m integration` → **3 passed**（seq 并发单调 10 并发不重号 / after_seq 回放 / 未知事件类型拒绝）
  - 端到端 `scripts/smoke_e2e.py` → **SMOKE_E2E=OK**：
    run `7a237e94…`，interrupt 前 8 个事件（run.start + 3 节点 ×2 + interrupt），`requester` 复核被 **403** 拦截，
    buyer resume 后 3 个事件（finalize ×2 + run.end），快照 `status=succeeded`、`artifact.decision=accept`
  - DB 实证：`review_task = apply_patch/accepted`，`artifact = precheck/accept`，`run_event` 11 条（seq 1..11 连续）
  - 静态页 `GET /` 200（FastAPI 托管 dist）、`/api/v1/meta/enums` 200、`/api/v1/reviews` 空数组
- **说明**：集成测试当前复用 dev 库（`procurement`），会在 `agent_run` 留 3 条 `queued` 测试数据；W1 拆 `procurement_test` 库
- **下一步 (Next)**：W1-M3 第 1 步——数据工厂 master data + ReportLab 发票/PO/合同模板 + 扫描件退化 + gold

### [2026-10-23 11:57] W0: GitNexus 索引 + 提交前风控（P0 收尾）
- **变更文件**：`AGENTS.md`、`.claude/skills/gitnexus-*`、`claude.md`（GitNexus 自动写入的规范块）、`README.md`（测试数字修正）、`backend/app/db/session.py`（删除占位函数）、`docs/worklog/*`
- **GitNexus 影响面**：
  - `analyze`（v1.6.12）成功：**1205 symbols / 1870 relationships / 22 execution flows**；`.gitnexus/`（17MB）已在 `.git/info/exclude` 中忽略，不入库
  - `impact "get_engine" --direction upstream`：上游链 `deps.ensure_app_state → RunService/EventBus`（depth 3），**risk LOW**
  - `detect-changes --scope all`：`3 files, 5 symbols, affected processes 0, risk low`
  - FTS/BM25 检索不可用（Windows 缺 VC++ 运行时，见 GOTCHAS G-008），不影响 impact/detect-changes
- **验证结果**：后端 **26 passed / 3 skipped**（0.43s）；前端 **18 passed**；`tsc --noEmit` 0；`vite build` 通过；提交 `587de1f` 后工作树干净（本条目对应的二次提交随后产生）
- **下一步 (Next)**：W1-M3 第 1 步——数据工厂 master data + ReportLab 发票/PO/合同模板 + 扫描件退化 + gold（对应 Spec §10.1~§10.4）

### [2026-10-23 11:54] W0: 运行时冒烟与环境结论
- **变更文件**：无（验证动作）；`README.md`、`scripts/dev.ps1`、`scripts/doctor.ps1`、`docker-compose.yml`、`backend/Dockerfile`、`Makefile`、`.gitignore`
- **执行**：`.venv\Scripts\python -m uvicorn app.main:app --port 8011` → 用 `Invoke-WebRequest` 验证 5 个端点
- **验证结果**：
  - `GET /healthz` → 200 `{"status":"degraded","db":false,"env":"dev","version":"0.1.0-p0"}`（无 DB 时如实降级）
  - `GET /api/v1/meta/enums` → 200（958 bytes）
  - `GET /openapi.json` → 200（10 条路径）
  - `GET /` → 200（FastAPI 已托管 `frontend/dist`，418 bytes index）
  - `POST /api/v1/runs` → 503 `SERVICE_UNAVAILABLE`（统一错误体 + `request_id`，见 §4.8）
- **结论**：应用层全链路可用；**端到端 SSE 演示待 Docker daemon 启动**（本机 Docker Desktop 进程在但引擎未起、无本地 PG、5432 未监听）
- **单测**：`pytest` 26 passed / 3 skipped（0.42s）；前端 `vitest run` 18 passed（2 files）
- **下一步 (Next)**：W1 数据工厂（ReportLab 发票/PO/合同模板 + 扫描件退化 + gold），先让 `make seed` 可复现

### [2026-10-23 11:53] W0: 前端壳 + SSE 状态归约器
- **变更文件**：`frontend/src/{main.tsx,App.tsx,styles/tokens.css}`、`frontend/src/lib/{events.ts,sse.ts,api.ts,useRunStream.ts}`、`frontend/src/lib/{events.test.ts,sse.test.ts}`、`frontend/src/pages/*.tsx`、`frontend/{package.json,vite.config.ts,vitest.config.ts,tsconfig.json,index.html,pnpm-workspace.yaml}`
- **GitNexus 影响面**：纯新增；`events.ts`/`sse.ts` 是后续所有页面共用的纯函数层，改动需回归两套测试
- **核心设计执行**：
  - `reduceEvent`：按 `seq` 幂等去重、节点状态机、findings 增量、delta 按 channel 拼接、`interrupt` → `awaiting_review`、`run.end` 终态（Spec §9.6）
  - `parseSseChunk`：手写 SAX 风格解析（不引依赖），兼容 CRLF / 半包 / retry 行 / 非法 JSON 丢弃
  - `useRunStream`：`fetch` + ReadableStream + `AbortController`（原生 EventSource 不能带 `X-Demo-Role`，Spec §9.6）
- **验证结果**：`vitest run` 18 passed（events 12 + sse 6）；`tsc --noEmit` exit 0；`vite build` 96 modules / dist 318KB
- **下一步 (Next)**：UI 只在 W1~W3 按 Spec §9.4 逐页填真；P0 只保留壳与状态机

### [2026-10-23 11:52] W0: 后端骨架 + 事件总线 + SSE + LLM 网关
- **变更文件**：
  - 基础：`backend/pyproject.toml`、`backend/.env.example`、`backend/requirements.txt`、`backend/Dockerfile`
  - 数据层：`backend/app/models/tables.py`（42 张表，Spec §3 + v1.1 的 `requires_gr`/`audit_log`/`supplier_eval`/`agent_run.last_seq`）、`backend/app/db/{base,session}.py`、`backend/alembic/*`（基线 revision `0001_initial`）
  - 可观测：`backend/app/observability/runevents.py`（`UPDATE agent_run.last_seq RETURNING` 原子分发 seq + payload 内嵌 seq + replay）
  - 网关：`backend/app/llm/{types,cost,cache,config,provider,router,ledger}.py` + `models.yaml`（角色路由、跨厂商降级、指数退避、prompt 缓存、`llm_call` 记账）
  - 服务与接口：`backend/app/services/runs.py`（演示 run 生命周期 + `interrupt`/`resume` + `finalize` 事务）、`backend/app/api/{deps.py,v1/runs.py,v1/meta.py}`、`backend/app/main.py`（lifespan/懒初始化/CORS/请求 ID/`SQLAlchemyError→503`/静态托管）
  - 测试：`backend/tests/{conftest,test_cost,test_cache,test_llm_gateway,test_llm_config,test_api_smoke,test_runevents,test_integration_db}.py`
- **GitNexus 影响面（`impact`）**：不可用（仓库首次提交前无索引，见 IMPACT ADR-001）；以"新增文件清单"替代，未修改任何既有源文件
- **核心设计执行**：
  - `run_event.seq` 用 `UPDATE ... RETURNING` 原子分配，解决并发节点 `MAX(seq)` 撞唯一键（ADR-003）
  - 中断前只持久化"分析结果"（`review_task`），`finalize` 才写 `artifact` 与终态（Spec §4.6 写入边界）
  - 网关缓存键 = `sha256(provider|model|prompt_sha|schema_sha|temperature)`，命中记 `cache_hit/cost=0`
  - `models.yaml` 的 `reason → reason_small(智谱)` 为跨厂商降级，并有契约回归单测 `test_reason_falls_back_to_a_different_provider`
- **验证结果**：
  - `pytest` → **26 passed, 3 skipped**（0.42s）；3 条 integration 因无 PG 按 ADR-002 自动 skip
  - `from app.main import app` 成功；OpenAPI 10 条路径；`/healthz` 无 DB 时返回 degraded 而非 500
- **下一步 (Next)**：启动 Docker 后 `alembic upgrade head` 验证 42 张表 + pgvector 扩展；补一条 SSE 端到端脚本（`scripts/smoke_sse.ps1`）


### [2026-10-23 11:40] W0: 环境探测与基线确认（开工前 Read State）
- **已完成**：
  - 读取 `CLAUDE.md`（GitNexus 纪律 + 三份留痕 + 运行期四层留痕）与 `docs/PROJECT_SPEC.md` v1.1（§2.3 目录、§3.7 可观测表、§4.2 网关、§8 契约）。
  - 环境探测：Python 3.12.14（bundled）/ Node 24.21 / npm 11.16 / git 2.55 / Docker CLI 29.1.3（**daemon 未运行**）/ PyPI 可达 / 仓库未 git init。
  - 修复工作区 Windows 文件权限阻塞（详见 `GOTCHAS.md` G-001）。
- **结论/决策**：
  - `.venv` 由 3.11 重建为 3.12（对齐 Spec §2.1）。
  - 无 PG 可用 → 集成测试标记 `@pytest.mark.integration`，默认跳过；P0 验收以**单元测试 + ASGI 冒烟**为准。
- **验证结果**：环境探测命令输出已记录；无代码变更，无单测。
- **下一步 (Next)**：创建 `backend/` 骨架与依赖安装，落地 `agent_run / run_event / llm_call` 三张可观测表。

<!-- 追加位置：新记录写在本行上方 -->

