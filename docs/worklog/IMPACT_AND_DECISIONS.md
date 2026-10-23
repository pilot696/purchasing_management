# IMPACT & DECISIONS — 影响面分析与架构决策留痕

> 协议来源：`CLAUDE.md` §2 + §1.2。格式：`[ADR/变更编号] 触发原因 + GitNexus impact 爆炸半径摘要 + 最终方案 + 同步更新的 Spec 章节`。
> 说明：P0 阶段为**纯新增文件**，尚无跨模块调用链；GitNexus 索引与 `impact` 分析待首次提交后补跑，偏差在 ADR-001 中登记。

## 当前影响面基线

- 已索引代码图谱：**无**（仓库未 `git init`、`gitnexus` 未安装 → 见 ADR-001）
- 公共底座模块（需 impact 分析后才能改）：`backend/app/llm/*`、`backend/app/matching/normalize.py`、`backend/app/schemas/*`、`backend/app/models/*`

---

## 决策记录（倒序追加，最新在上）

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

### ADR-002｜Docker daemon 未运行 → 集成测试默认跳过
- **触发原因**：`docker ps` 报 `npipe:////./pipe/dockerDesktopLinuxEngine` 不存在；PostgreSQL/pgvector 无法启动。
- **GitNexus impact**：不适用。
- **最终方案**：测试分两层——`unit`（纯函数/FakeLLM/ASGI 冒烟，默认跑）与 `integration`（需 `DATABASE_URL`，默认 `-m 'not integration'` 跳过）。W1 开工前需启动 Docker Desktop 并跑 `make up migrate`。
- **同步更新 Spec**：附录 A.3 增加测试分层说明。

### ADR-001｜GitNexus 在 P0 尚未可用（偏差登记）
- **触发原因**：`CLAUDE.md` §1 要求改动前用 GitNexus `impact` 做爆炸半径分析；但仓库当前未 `git init`、`gitnexus` 未安装、无 AST 索引，MCP 工具不可用。
- **GitNexus impact**：**未执行**（无索引）。以「新增文件清单 + 预留调用点」替代：P0 不修改任何已有源文件（仓库仅有 PyCharm 模板 `main.py`/`pyproject.toml`，未触碰）。
- **最终方案**：P0 结束提交后执行 `npx gitnexus analyze` 并补 `setup`；W1 起凡触及公共底座（`app/llm/*`、`app/schemas/*`、`app/models/*`）必须先 `impact`。若 `npm install -g gitnexus` 不可用，则退化为「改动前先跑全量 `pytest` + 列出引用点」并在本文件登记。
- **同步更新 Spec**：无（属工具链偏差）。

<!-- 追加位置：新 ADR 写在本行上方 -->
