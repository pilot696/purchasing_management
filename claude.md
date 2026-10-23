# CLAUDE.md — Procurement AI Agent Workbench 开发留痕与 GitNexus 协作规范

> 本文件放置在项目根目录（`CLAUDE.md` / `AGENTS.md`），由 Claude Code / Cursor / Codex 在每次会话启动时自动加载。
> 唯一业务与架构事实来源：`docs/PROJECT_SPEC.md`。

## 1. GitNexus 代码知识图谱与 Skill 强制使用规范

本项目已接入 [GitNexus](https://github.com/abhigyanpatwari/GitNexus)（基于 Tree-sitter AST 的零服务器代码知识图谱引擎与 MCP 服务）。严禁在不了解调用链影响面的情况下盲目修改跨模块文件。

### 1.1 环境初始化与自动索引命令
在首次拉取仓库或新增大型模块后，确保执行以下命令以激活 GitNexus 的 MCP 工具、PreToolUse/PostToolUse Hooks 以及内置的 4 大 Agent Skills（`Exploring`、`Debugging`、`Impact Analysis`、`Refactoring`）：

```bash
# 1. 全局安装 GitNexus CLI
npm install -g gitnexus

# 2. 构建当前代码仓库的 AST 知识图谱索引（开发时可加 --watch 自动增量索引）
npx gitnexus analyze

# 3. 自动配置 Claude Code / Cursor 的 MCP、Hooks 与 .claude/skills/gitnexus-* 技能
npx gitnexus setup

# 4. （可选）安装代码审查 Skill
npx skills add abhigyanpatwari/gitnexus/gitnexus-review --agent claude-code
```

### 1.2 开发过程中的 GitNexus 触发纪律（必须遵守）
1. **探索与定位（Exploring Skill）**：
   - 当需要理解跨模块调用关系（例如 `spec_bucket` 如何在 M1 预检、M2 评分、M3 行对齐之间流转）时，优先调用 GitNexus MCP 的 `query` 和 `context` 工具，禁止全仓库盲目 `grep`。
2. **改前爆炸半径分析（Impact Analysis Skill）**：
   - 在修改任何公共底座代码（如 `backend/app/llm/provider.py`、`backend/app/matching/normalize.py`、`backend/app/schemas/`、或 SQLAlchemy ORM 模型）**之前**，必须先调用 GitNexus 的 `impact` 工具获取受影响的上游调用方与下游测试文件清单。
3. **跨文件重构（Refactoring Skill）**：
   - 需要重命名符号、调整 Pydantic Schema 字段或拆分模块时，必须使用 GitNexus 的 `rename` 工具进行多文件协同重命名，确保前后端类型与单测同步更新。
4. **提交前变更风控（Pre-commit Risk Check）**：
   - 每次完成一个子任务准备提交前，必须调用 GitNexus 的 `detect_changes` 工具检查未预期的调用链断裂；当架构发生变动时，调用 `generate_map` 自动更新 `docs/PROJECT_SPEC.md` 中的 Mermaid 架构图。

---

## 2. Coding Agent 开发期工作留痕协议（Engineering Worklog）

为了防止跨会话（Session）失忆、方便复盘开发过程并沉淀简历面试素材，Agent 在编码过程中必须维护以下 **3 份留痕文件**（位于 `docs/worklog/` 目录）：

| 留痕文件 | 作用与更新时机 | 必填字段格式 |
| :--- | :--- | :--- |
| `docs/worklog/PROGRESS.md` | **里程碑与任务留痕**：每完成一个节点/接口/页面后立即更新 | `[日期时间] [里程碑 W0~W4] 已完成模块 + 关联文件 + 单测通过数 (pytest/vitest) + 下一步待办` |
| `docs/worklog/IMPACT_AND_DECISIONS.md` | **GitNexus 影响面与架构变更留痕**：每次修改公共模块或偏离 `PROJECT_SPEC.md` 时记录 | `[ADR/变更编号] 触发原因 + GitNexus impact 爆炸半径摘要 + 最终方案 + 同步更新的 Spec 章节` |
| `docs/worklog/GOTCHAS.md` | **踩坑与排障留痕（面试高频素材）**：每次遇到 Bug、Schema Repair 失败或性能瓶颈时记录 | `[问题现象] 根因定位（附 GitNexus debugging 调用链） + 修复手段 + 防回归单测用例名` |

### 每次任务执行的标准闭环（SOP）
1. **开工前（Read State）**：先读取 `docs/worklog/PROGRESS.md` 末尾的最新状态，明确当前所处的周计划（`W0` → `W1: M3` → `W2: M1` → `W3: M2` → `W4`）。
2. **动手前（Check Blast Radius）**：对拟修改的函数/类运行 GitNexus `impact` 工具，在回复中简要列出受影响文件。
3. **验证后留痕（Write Trace）**：运行 `pytest`（使用 `FakeLLM` 零网络单测）+ GitNexus `detect_changes`，验证通过后**必须在同一轮把执行摘要追加写入 `docs/worklog/PROGRESS.md`**（若有踩坑则同步写入 `GOTCHAS.md`），绝不允许只改代码不留痕。

### `docs/worklog/PROGRESS.md` 单条留痕模板
```markdown
### [2026-10-23 14:30] W1-M3: 完成三单行项目对齐与分批核销状态机
- **变更文件**: `backend/app/matching/align.py`, `backend/app/matching/reconcile.py`, `backend/tests/test_matching.py`
- **GitNexus 影响面 (`impact` / `detect_changes`)**: 影响 `matching_graph` 的 `align` 与 `reconcile` 节点；确认未破坏 `M1` 与 `M2` 对 `normalize.spec_bucket` 的调用。
- **核心设计执行**:
  - 修正核销上限为 `min(qty_ordered, qty_received) - qty_invoiced`（SaaS 品类 `requires_gr=False` 时回退 `qty_ordered`）；
  - 将 `po_line.qty_invoiced` 写库推迟至 HITL 通过后的 `finalize` 节点。
- **验证结果**: `pytest backend/tests/test_matching.py` 14 passed (耗时 0.42s)
- **下一步 (Next)**: 实现 `tolerance.py` 五类容差查表与尾差池自动平账。
```

---

## 3. 业务 Agent 运行期留痕规范（Runtime Observability & Audit）

除了开发过程留痕，本项目自身作为 **AI Agent 工作台**，在运行期必须严格落实以下四层业务留痕（详见 `docs/PROJECT_SPEC.md` §3.7 & §4.7）：

1. **节点级事件流留痕（`run_event` 表）**：
   - 所有 LangGraph 节点必须包裹 `@instrumented(node_name=...)` 装饰器，自动写入 `node.start` / `node.end`（含 `duration_ms`、`llm_calls`、`tool_calls`）及单调递增的 `seq`，支持前端 SSE 断线按 `?after_seq=` 精确回放。
2. **模型调用与成本记账留痕（`llm_call` 表）**：
   - 严禁绕过 `LLMProvider` 直接调用 SDK。每次调用必须记录 `prompt_sha`、`tokens_in/out`、`cost_cny`、`latency_ms`、`retry_no`、`cache_hit` 以及 `schema_repair`（Pydantic 校验重试次数）。
3. **证据链防幻觉留痕（`Evidence` 后置校验）**：
   - M1/M2/M3 的所有 Finding 和 RiskItem 必须附带 `evidence=[{kind, ref, snippet}]`，由后置校验器核验 `ref` 在数据库中真实存在；校验失败的条目丢弃并记入 `llm_hallucination_refs` 统计。
4. **人工复核与数据变更审计留痕（`review_task` + `audit_log` 表）**：
   - 任何修改业务单据的动作（如 M1 采纳 AI 规格修正 `FieldPatch`、M2 确认准入条件、M3 财务放行核销）必须经过 `interrupt_before` 挂起，并在 `finalize` 事务中同步写入 `audit_log`（记录 `run_id, actor_role, target_table, target_id, before_json, after_json, source, created_at`）。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **purchasing_management** (1205 symbols, 1870 relationships, 22 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "main"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "main" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/purchasing_management/context` | Codebase overview, check index freshness |
| `gitnexus://repo/purchasing_management/clusters` | All functional areas |
| `gitnexus://repo/purchasing_management/processes` | All execution flows |
| `gitnexus://repo/purchasing_management/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
