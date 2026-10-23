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
