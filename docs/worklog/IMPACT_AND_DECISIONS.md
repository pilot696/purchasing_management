# IMPACT & DECISIONS — 影响面分析与架构决策留痕

> 协议来源：`CLAUDE.md` §2 + §1.2。格式：`[ADR/变更编号] 触发原因 + GitNexus impact 爆炸半径摘要 + 最终方案 + 同步更新的 Spec 章节`。
> 说明：P0 阶段为**纯新增文件**，尚无跨模块调用链；GitNexus 索引与 `impact` 分析待首次提交后补跑，偏差在 ADR-001 中登记。

## 当前影响面基线

- 已索引代码图谱：**无**（仓库未 `git init`、`gitnexus` 未安装 → 见 ADR-001）
- 公共底座模块（需 impact 分析后才能改）：`backend/app/llm/*`、`backend/app/matching/normalize.py`、`backend/app/schemas/*`、`backend/app/models/*`

---

## 决策记录（倒序追加，最新在上）

### ADR-022｜M2 图：闸门短路 + 纯函数评分 + LLM 只写叙述 + 离线降级
- **触发原因**：Spec §6 要求"分数由公式算、闸门由规则判、叙述由模型写"，且闸门命中不得让 LLM 有机会美化否决项。
- **GitNexus impact**：`RunService` 上游 4 个文件（main/api deps/runs/api init），risk **LOW**；`supplier_graph` 新增不影响既有 M1/M3 图；
  `seed/dataset.py` 证照与闸门标记变更影响 M2 基线与 e2e。
- **最终方案**：
  1. `app/scoring/supplier.py` 全部纯函数（piecewise/price/delivery/quality/service/compliance/weights/verdict/confidence/sensitivity/gates），可单测；
  2. `gate_check` 命中否决即条件边直达 `compose_result`（跳过 aggregate/score/llm）；G2/G6 仅作为条件项，不自动否决；
  3. LLM 复核者 schema 无分数字段，`evidence_refs` 必须命中证据包 id（失败丢弃 + 计数）；离线/网关失败显式 unavailable，不伪造；
  4. 规则与数据契约：闸门标记必须与数据一致（scope_mismatch 表示本次标的物、证照有效期不随机过期），seed 基线负责守住；
  5. finalize 才写 supplier_eval/artifact/audit，HITL 前零写入（与 M1/M3 一致）。
- **同步更新 Spec**：§6 实现口径、§13.3 v1.12。

### ADR-020｜M1 检索先用「词法 + 精确 + 结构化」三路 RRF，向量 leg 留可注入位
- **触发原因**：Spec §5.3 要求 pgvector + 全文 + RRF；但当前 LLM 网关只有 `complete`，没有 `embed` 角色，
  seed 也未落 embedding；直接造"伪向量"检索会得出不可解释的指标。
- **GitNexus impact**：`app/retrieval/search.py` 为新增层，被 `agents/precheck.py` 与 `scripts/m1_eval.py` 调用；
  后续接 embedding 只需在 `retrieve_sku_candidates` 加第三/第四路 ranking，不改调用方。
- **最终方案**：
  1. 三路确定性检索：pg_trgm 词法（中文短名可用） + 精确名/别名 + 规格桶结构化；
  2. RRF（k=60，纯函数 `rrf_fuse` 可单测）融合；返回候选带 `sources`（证据面板可展示"命中来源"）；
  3. 评测按"sku_code 或 (name,spec_bucket) 等价类"判定 top-1；同名不同配的数据问题在 G-032 中修正。
- **同步更新 Spec**：§5.3 实现口径、§13.3 v1.10；向量 leg 接入后补评测对比。

### ADR-021｜M1 预检图 + 规则 DSL 白名单 + 自动放行
- **触发原因**：M1 必须"规则确定性优先、LLM 只做语义层"，且无问题 PR 不应强迫人工点一次。
- **GitNexus impact**：`RunService` 重构为 M1/M3 共用 `_execute_graph`（HITL/自动放行/finalize 统一），
  影响 `/runs` 所有模块；`rule` 表与 seed 规则变更（17 条）影响数据工厂与 baseline。
- **最终方案**：
  1. 规则存 `rule.expression`（JSONB），引擎只实现白名单算子（required/fn/var/比较/in/not/if/cat/len/exists），
     未声明算子显式抛 `RuleEvalError`；finding 带 `rule_code + policy evidence`；
  2. 预检图五节点：load → retrieve → rules_engine → semantic_resolve → compose；**决策前不写 spec**；
  3. `report.requires_review=False`（无 findings/patches）时自动 `decision=accept` 并 finalize，不创建复核任务；
  4. finalize 单事务：apply patches 到 `pr_item.spec`、写 `audit_log(apply_patch/hold)`、artifact、`pr.status=prechecked/returned`；
  5. 数据工厂规则与 gold 对齐（qty→stat_outlier、预算 severity、owner 入 spec、补 ADMIN 敏感数据规则等）。
- **同步更新 Spec**：§5.2/§5.4 实现口径、§13.3 v1.10。

### ADR-019｜PDF 抽屉用「服务端渲染 PNG + bbox 覆盖层」，暂不引入 PDF.js
- **触发原因**：Spec §9.4 原计划 PDF.js 渲染 + overlay；W1 要在不引入前端 PDF 渲染依赖的前提下把"抽取字段高亮"跑通，
  并保证 bbox 与底图像素严格对齐。
- **GitNexus impact**：新增 `GET /documents/{id}/file`、`/page.png` 两个只读端点（不改原契约）；
  `matching_graph` 报告新增 `documents/po_lines/invoice_lines`，影响 review 载荷与前端 `lib/m3.ts`。
- **最终方案**：
  1. 后端 `pypdfium2` 渲染单页 PNG（`scale=2.0`，Cache-Control 1h），前端 `<img>` 铺满容器；
  2. bbox 用归一化坐标（左上原点）直接换算百分比覆盖层，**天然与图片对齐**，无需处理 PDF.js 的 viewport/scale；
  3. 打开抽屉时按需触发 `POST /documents/{id}/extract`（幂等）再取 `annotations`，扫描件明确提示"需视觉抽取"；
  4. 限制：单页、无文本选择/缩放/搜索；W4 视觉阶段若确需多页/选择再评估 PDF.js，后端 PNG 接口保留。
- **同步更新 Spec**：§8.3 新增两个端点；§9.4 页面 4 增加 W1 实现口径；§13.3 v1.9。

### ADR-018｜M3 用 LangGraph 图驱动 + finalize 单事务 + audit_log 幂等
- **触发原因**：W1 要求把 P0 的假节点序列换成真实图；M3 必须严格满足"HITL 前不写会计事实"与"同一 run 重入不重复核销"。
- **GitNexus impact**：`RunService._execute`（新增 M3 分支）上游是 `/runs` API 与 `main.py` lifespan；
  `Invoice` ORM 加列影响 seed persist、documents 抽取落库与图加载；`detect_changes` 见提交记录。
- **最终方案**：
  1. `StateGraph + interrupt_before=["finalize"]`，checkpointer 用 `MemorySaver`（同进程 resume；生产换 `PostgresSaver`，限制写入 Spec v1.8）；
  2. `load_subject` 一次读齐 PO/发票/合同/收货/容差/品类 `requires_gr`，后续节点纯计算；
  3. **所有落库集中在 finalize 单事务**（match_run/match_line/match_diff、qty_invoiced、尾差池、invoice.status、audit_log、artifact、run 状态）；
  4. 幂等以 `audit_log(target_table='match_run')` 存在性判断，天然与"审计即事实"一致；
  5. 图级测试断言改为读 gold 文件动态比较，避免硬编码错位（G-025）。
- **同步更新 Spec**：§3.4 `invoice.remark`、§4.6 M3 实现口径 + 已知限制、§13.3 v1.8。

### ADR-017｜匹配层保持"纯函数 + 分析先行"，匈牙利用于全局指派
- **触发原因**：M3 是三单状态机，必须让每行判定可溯源、可单测、可回放；且 HITL 前不得写会计事实。
- **GitNexus impact**：`matching/*` 为新增层（无既有上游），将成为 `matching_graph` 节点与 W2 检索的公共依赖；
  `normalize.py`/`align.py` 被 `tests/test_matching.py` 与 `scripts/m3_eval.py` 引用，后续改签名需先 `impact`。
- **最终方案**：
  1. `normalize` 规则优先（别名 → family → 规格槽位），无 LLM；`spec_bucket` 作为跨模块公共语义坐标；
  2. `align` 三级评分 + `scipy.optimize.linear_sum_assignment`（新增依赖 scipy），保留贪心基线用于消融；
  3. `tolerance` 纯函数输出 `proposed_consumed_qty/proposed_tail/diffs/overall`，**不落库**；
  4. `min(ordered, received)` 上限（服务类回退 `ordered`）、尾差基准改"发票行含税合计"、行级差异抑制重复 `amount`；
  5. 扫描件配额精确化，保证评测集比例稳定。
- **同步更新 Spec**：§7.4/§7.5 实现口径（尾差基准、金额去重）、§13.3 v1.7。

### ADR-016｜VLM 以 Protocol 接缝接入；repair 只修 Schema，不修业务规则
- **触发原因**：M3 必须处理 15% 扫描件，但项目当前没有可用的视觉 API key，且不能把"未来接真实模型"变成阻塞；
  同时需要区分"模型输出格式错"与"模型漏了业务字段"。
- **GitNexus impact**：`app/llm/types.py::Message` 是公共底座（全部 LLM 调用共用），content 扩为 `str | list[dict]`
  后影响 gateway 与 extraction；`impact "Message"` 上下游已核对，`detect_changes` 见提交记录。其余为新增模块。
- **最终方案**：
  1. `VisionExtractor` 协议 + `FakeVisionExtractor`/`LLMVisionExtractor` 两实现：单测零网络，真实调用走网关 `vision` 角色；
  2. 多模态消息按 OpenAI 兼容 content parts 构造（data URL），provider 透传，FakeLLM 只统计文本 token；
  3. repair 仅覆盖 Schema/Pydantic 失败（≤2 次）；业务校验失败不重试，进 `needs_review_fields`（G-018）；
  4. 抽取结果落 `extraction_record` + `doc_annotation`，`doc` 回写 route 元数据；文档接口只读 + 触发抽取，不做上传（W2 补）。
- **同步更新 Spec**：§7.2 repair 语义、§8.3 documents 接口落地（子集）、§13.3 v1.6。

### ADR-015｜抽取管线「结构优先、正则兜底」，bbox 采用左上原点归一化
- **触发原因**：Spec §7.2 要求"确定性优先、VLM 只兜底"。首版实现按全文正则抽取，三类文档全部解析失败/
  字段不全（GOTCHAS G-017），说明 ReportLab 表格的文本布局不适合纯正则。
- **GitNexus impact**：纯新增（`app/extraction/*`、`app/schemas/extraction.py`）；`schemas/extraction.py` 将是
  M3 图与 API 的公共类型，后续修改需先 `impact`。
- **最终方案**：
  1. 抽取以 `page.extract_tables()` 的结构为准：`_header_map()` 汇总"标签/值"对，`_lines_from_table()` 按列名映射；
     正则只用于"表格外"的单值字段兜底；
  2. bbox 统一为**左上原点归一化坐标**（x0,y0,x1,y1 ∈ [0,1]，y 向下），`doc_annotation` 与前端 overlay 同一套语义；
  3. 校验器返回 `ValidationIssue` 而非抛异常，`high` 级问题进入 `needs_review_fields`，不阻塞后续匹配；
  4. 扫描件显式返回 `VLM_REQUIRED`，与数字原生路径解耦，VLM 后续接入时只替换这一分支。
- **同步更新 Spec**：§3.5 `doc_annotation` 增加"坐标为左上原点归一化"约定；§13.3 v1.5。

### ADR-014｜用数据工厂标定路由阈值，并修复种子数据 4 处一致性缺陷
- **触发原因**：W1-M3 开工前自查发现：`po_line.qty_received` 未落库、`text_ratio` 口径与 Spec 阈值不符、PR gold 的
  `over_budget` 与真实预算不一致、`pr_item` 的 owner/cost_center 在持久化时丢失（GOTCHAS G-014/G-015）。
- **GitNexus impact**：`impact "build_dataset" / "persist_dataset"` 无上游既有调用（新增模块）；改动集中在 `app/seed/*`，
  影响面为数据工厂自身与测试；`detect_changes` 结果见提交前记录。
- **最终方案**：
  1. `persist` 落库前汇总 `receipt_line.qty` 回填 `po_line.qty_received`（`qty_invoiced=0`），保证 §7.5 上限口径可用；
  2. `text_ratio` 改为与 Spec 同量纲（每 1e3 pt² 字符数），**阈值由 0.6 标定为 0.15**（原生 PO 0.435~0.509，扫描件 0）；
  3. PR 的 `over_budget` 由 `estimated > available` 推导，verdict 由 findings 推导，新增 `policy_block` 样本；
  4. owner/cost_center 移入 `spec` 槽位（与 §3.4 表结构兼容），gold 路径同步；
  5. compose `env_file` 改为 optional（G-016）。
- **同步更新 Spec**：§3.5 `text_ratio numeric(6,4)`、§7.2 路由阈值 0.15（v1.4 变更记录）。

### ADR-013｜数据工厂拆分为「纯构建」与「副作用落库」，异常槽位精确分配
- **触发原因**：Spec §10 要求"生成即金标、固定 seed 可复现"。若构建函数直接写 DB/文件，单测必须连库，且难以验证确定性；
  §10.3 又要求每个差异类型 ≥10 正样本，随机分配会缺样本（首版 `price` 只有 11 条）。
- **GitNexus impact**：纯新增（`app/seed/*`、`scripts/seed.py`、`tests/test_seed.py`），无既有符号被修改；新模块后续被 W1 抽取/评测脚本引用。
- **最终方案**：
  1. `build_dataset(cfg) -> dict` 纯函数（无 DB/IO），`persist_dataset()` 负责落库/写盘/gold；
  2. `rl_config.invariant = 1` + sha256 派生 RNG，保证 PDF 与数据都字节/数值级可复现；
  3. 异常槽位按"类型不重复落到同组"的游标分配，精确满足 `DIFF_PLAN` 的每类数量；
  4. `requires_gr` 驱动收货单生成（SaaS/市场外包无 GR），为 W1 的 `min(ordered, received)` 核销规则提供真实反例。
- **同步更新 Spec**：§10.2/§10.3/§10.4 的实现对齐（无设计变更）；§13.3 记录 v1.3。

### ADR-012｜新增 `scripts/smoke_e2e.py` 作为端到端验收脚本
- **触发原因**：单测与集成测试覆盖不到"HTTP + SSE + HITL 恢复 + 快照"的完整链路；需要一条可在 CI/演示前重复执行的验收命令。
- **GitNexus impact**：新增文件，无既有符号被修改。
- **最终方案**：脚本按 Spec §8.2 顺序断言——建 run → 收满 `run.start/node.*/interrupt` → `requester` resume 必须 403
  → buyer resume 200 → `finalize/run.end` → 快照 `succeeded` + `artifact.decision=accept`；`trust_env=False` 绕过系统代理。
- **同步更新 Spec**：附录 A.3（验收命令）。

### ADR-011｜Windows 事件循环入口层（`run.py` + `core/loop.py` + conftest）
- **触发原因**：psycopg 异步驱动不支持 Windows 默认 `ProactorEventLoop`，且 uvicorn 0.54 单进程模式硬编码 Proactor
  （`loops/asyncio.py`），单纯设置全局 policy 会被 uvicorn 的 `loop_factory` 覆盖 → 应用所有 DB 操作失败（GOTCHAS G-009）。
- **GitNexus impact**：`impact "create_app"` → **LOW**（depth 1，仅 main.py）；`impact "db_ping"` → **LOW**（上游 healthz/readyz）；
  改动面为 1 个已有文件（main.py 增加兜底调用）+ 3 个新增/测试文件。
- **最终方案**：
  - 新增 `app/core/loop.py::ensure_windows_selector_loop()`；
  - 新增 `run.py`：单进程时覆盖 `config.get_loop_factory` 为 `asyncio.SelectorEventLoop`，`--reload` 走 uvicorn 的 `use_subprocess=True` 分支；
  - `app/main.py` 导入时兜底、`tests/conftest.py` 在 pytest-asyncio 建循环前切换；
  - `scripts/dev.ps1 api` 固定走 `run.py`；README 明令禁止 `python -m uvicorn app.main:app`。
- **detect_changes（提交前）**：`Risk level: medium`，受影响执行流 `Lifespan → get_settings / get_session_factory / build_providers`
  （因 main.py 顶层新增 import 与策略兜底调用被标记为 lifespan 变更）。缓解：`pytest` 28 passed + `-m integration` 3 passed +
  `smoke_e2e.py` 全链路 OK，确认启动链路未回归；medium 的成因是"文件内符号变化"，非调用链断裂。
- **同步更新 Spec**：§2.3 目录（`backend/run.py`）、§13.3 变更记录 v1.2。

### ADR-009｜应用状态懒初始化（兼容 lifespan 与 ASGITransport）
- **触发原因**：单测用 `httpx.ASGITransport` 默认不执行 FastAPI lifespan，导致 `/api/v1/runs` 依赖读 `app.state.runs` 抛 `AttributeError`（P0 测试暴露）。
- **GitNexus impact**：`app/api/deps.py` 是所有 API 依赖的入口，影响全部 v1 路由；`detect_changes` 待索引可用后补跑。
- **最终方案**：`ensure_app_state(app)` 幂等装配 `bus/runs`，lifespan 与依赖注入都走它；生产环境行为不变（lifespan 先装配，依赖调用是 no-op）。
- **同步更新 Spec**：§4.1/§8 无契约变化，属实现细节；测试用例 `test_api_smoke.py::test_start_run_returns_503_when_db_down` 防回归。

### ADR-008｜前端单测与构建配置拆分（vitest.config.ts 独立）
- **触发原因**：`vitest 2.x` 自带 vite 5，而项目用 vite 6；在同一个 `vite.config.ts` 里 `defineConfig` from `vitest/config` 会让 `@vitejs/plugin-react`/`@tailwindcss/vite` 的 vite6 插件类型与 vite5 类型冲突，`tsc --noEmit` 直接失败。
- **GitNexus impact**：仅前端构建配置，无运行时代码引用。
- **最终方案**：`vite.config.ts` 只放插件与 dev proxy（`defineConfig` from `vite`）；`vitest.config.ts` 只放 `test` 配置（from `vitest/config`，不引插件）——单测只覆盖纯函数，无需 DOM/插件链。
- **同步更新 Spec**：§2.3 前端目录说明（vitest 配置独立）。

### ADR-007｜pnpm 10 忽略 esbuild 构建脚本：接受为环境噪声
- **触发原因**：`pnpm install` 报 `ERR_PNPM_IGNORED_BUILDS: esbuild@0.21.5, esbuild@0.25.12`，退出码 1；但 `pnpm-workspace.yaml` 中 `onlyBuiltDependencies: [esbuild]` 未生效（pnpm 10 的审批机制）。
- **GitNexus impact**：无（依赖安装层）。
- **最终方案**：验证实际能力未受影响——`vitest` 18 passed、`tsc` 0、`vite build` 96 modules 通过（esbuild 的平台二进制走 optionalDependencies 提供）。在 README/doctor 中说明该告警可忽略；若需消除，交互执行 `pnpm approve-builds`。
- **同步更新 Spec**：附录 A.3（开发环境说明）。

### ADR-006｜依赖安装路径修正：DSH 运行时不含 npm
- **触发原因**：首次尝试用 `node\bin\npm.cmd` 安装前端依赖失败（`Cannot find module ...\node\node_modules\npm\bin\npm-cli.js`）；`load_workspace_dependencies` 仅返回 node 与 pnpm 路径。
- **GitNexus impact**：无。
- **最终方案**：前端统一用 **bundled pnpm**（`dependencies/pnpm/bin/pnpm.mjs` + `node.exe`）安装；`scripts/dev.ps1` 固定该路径，不依赖 PATH 上的 npm。`scripts/doctor.ps1` 同步检查。
- **同步更新 Spec**：附录 A.3。

### ADR-005｜`.venv` 重建为 Python 3.12
- **触发原因**：项目自带 `.venv` 为 Python 3.11.14，与 Spec §2.1「Python 3.12」不一致；该 venv 为空壳（无已安装依赖）。
- **GitNexus impact**：不适用（环境层）。
- **最终方案**：`python -m venv .venv --clear`（3.12.14）后安装后端依赖；后续所有命令固定 `\.venv\Scripts\python.exe`。
- **同步更新 Spec**：无（§2.1 已写 3.12）。

### ADR-004｜P0 基线迁移用 `metadata.create_all`，W1 起切细粒度 Alembic
- **触发原因**：Docker daemon 未运行，无法就地 autogenerate；手写 30+ 张表的 `op.create_table` 成本高且易与 ORM 漂移。
- **GitNexus impact**：无历史调用方，纯新增。
- **最终方案**：Alembic 环境照常接入（`alembic.ini` + `env.py`），首个 revision `0001_initial` 在 `upgrade()` 内执行 `Base.metadata.create_all(bind=op.get_bind())`；W1 第一个 schema 增量前补一次真实 autogenerate 基线，此后全部细粒度迁移。
- **同步更新 Spec**：§2.3、附录 A.3（Make 目标 `make migrate` 语义不变）。

### ADR-003｜新增 `agent_run.last_seq` 列用于并发安全的事件序号分配
- **触发原因**：Spec §3.7 的 `run_event(run_id, seq)` 要求 seq 单调，但未定义分配机制。用 `SELECT MAX(seq)` 在并发节点下会撞唯一键。
- **GitNexus impact**：无（新增表结构）。
- **最终方案**：`agent_run.last_seq int NOT NULL DEFAULT 0`；写入事件时 `UPDATE agent_run SET last_seq = last_seq + 1 WHERE id = :run_id RETURNING last_seq`，行锁天然串行化，再插入 `run_event`。
- **同步更新 Spec**：§3.7 `agent_run` 列清单（W0 结束时回写）。

### ADR-002｜Docker daemon 未运行 → 集成测试默认跳过（已解除）
- **触发原因**：开工时 `docker ps` 报 `npipe:////./pipe/dockerDesktopLinuxEngine` 不存在；PostgreSQL/pgvector 无法启动。
- **当前状态（2026-10-23 14:00 更新）**：Docker Desktop 已启动，`pgvector/pgvector:pg16` 就绪，`alembic upgrade head` 成功
  （43 张 public 表 + `vector`/`pg_trgm` 扩展），`pytest -m integration` **3 passed**，`scripts/smoke_e2e.py` **OK**。
- **保留机制**：测试仍分 `unit` / `integration` 两层，无 `DATABASE_URL` 时集成测试自动 skip（保证 CI 与无 Docker 环境不红）。
- **遗留**：集成测试复用 dev 库 `procurement`，W1 起拆分 `procurement_test`（用 `EVAL_DB_URL`/独立 compose service）。
- **同步更新 Spec**：附录 A.3 测试分层说明。

### ADR-010｜GitNexus 索引与提交前风控已接通（P0 收尾）
- **触发原因**：`CLAUDE.md`/`AGENTS.md` 要求提交前 `detect_changes`、改动前 `impact`。
- **GitNexus impact**：`gitnexus@1.6.12 analyze` 成功：**1205 symbols / 1870 relationships / 22 execution flows**；
  生成 `.gitnexus/`（已被 `.git/info/exclude` 忽略）、`AGENTS.md`、`.claude/skills/gitnexus-*`。
  - `impact "get_engine" --direction upstream` → 上游链 `deps.ensure_app_state → RunService/EventBus`（depth 3），风险 LOW；
  - `detect-changes --scope all` → `3 files, 5 symbols, affected processes 0, risk low`（README / session.py / claude.md）。
- **最终方案**：`AGENTS.md` 与 `.claude/` 作为工具链配置入库；`.gitnexus/` 不入库；W1 起每次提交前执行
  `node .gitnexus/run.cjs detect-changes --scope all --repo .`，改动公共底座前执行 `impact`。
- **同步更新 Spec**：无（工具链）。

### ADR-001｜GitNexus 在 P0 前半段不可用（已解除）
- **触发原因**：开工时仓库未 `git init`、无 AST 索引，无法执行 `impact`。
- **处理过程**：P0 前半段为纯新增文件（未修改任何既有源文件），以「新增文件清单」替代 impact 分析；
  首次提交 `587de1f` 后执行 `npx --yes gitnexus@latest analyze`（v1.6.12）建立索引，随后补跑
  `impact` + `detect-changes`，状态由"未执行"变为"已接通"（详见 ADR-010）。
- **遗留**：GitNexus 的 **FTS/BM25 检索不可用**（Windows 缺 VC++ 运行时导致 `libfts.lbug_extension` 加载失败），
  不影响 `impact` / `detect-changes` / 图查询，登记在 GOTCHAS G-008。
- **同步更新 Spec**：无。

<!-- 追加位置：新 ADR 写在本行上方 -->
