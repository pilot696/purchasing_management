# GOTCHAS — 踩坑与排障留痕（面试高频素材）

> 协议来源：`CLAUDE.md` §2。格式：`[问题现象] 根因定位（附调用链） + 修复手段 + 防回归单测用例名`。
> 面试用法：每条都能讲成"我发现→我定位→我修复→我防回归"的四段式。

---

## G-001｜Windows：所有 shell 命令报 `SetNamedSecurityInfoW failed (Win32 5): grantWrite(<workspace>)`

- **问题现象**：`pwsh -Command` 任意命令（哪怕只读 `Get-Content`）在执行前即失败：
  `Error: SetNamedSecurityInfoW failed (Win32 5): grantWrite(D:\sgg_code\purchasing_management)`。
  `write`/`edit` 工具正常，说明写入本身没问题，是**进程沙箱的 workspace 授权（provisioning）失败**。
- **根因定位**：
  1. 沙箱在启动命令前，要对 workspace 根目录补一条"当前用户可写"的授权；该操作需要目录的 `WRITE_OWNER` 权限。
  2. 用 DSH 自带的 `diagnose-windows-sandbox-acl` 脚本检查 `D:\sgg_code\purchasing_management` 及其祖先，观测到：
     - `D:\sgg_code\purchasing_management`：`WRITE_DAC=True`、**`WRITE_OWNER=False`** → 判定 `PRECONDITION`（无法同时以 `WRITE_DAC|WRITE_OWNER` 打开对象，授权必然失败）。
     - `D:\sgg_code`：同样 `WRITE_OWNER=False`；`D:\`：owner 为 `BUILTIN\Administrators`，当前用户无 `WRITE_DAC`。
  3. 结论：工作区根目录缺少 `WRITE_OWNER`（深层原因指向目录由管理员/其他账户创建后继承链不完整），不是代码问题、也不是杀软拦截。
- **修复手段**：运行该脚本做**一次**最小修复（仅 `D:\sgg_code\purchasing_management` 本身）：
  1. 先备份原 DACL 与独立回滚脚本 → `D:\sgg_code\.dsh-acl-recovery\acl-backup-*.json(.ps1)`；
  2. 仅补一条「当前用户 FullControl allow ACE」，保留 deny ACE / owner / SACL；
  3. 重新读取校验：`before {writeDac:true, writeOwner:false}` → `after {writeDac:true, writeOwner:true}` **verified**；
  4. 复跑原命令，恢复正常。
- **回滚命令**（如需）：
  `pwsh -NoProfile -File 'D:\sgg_code\.dsh-acl-recovery\acl-backup-<id>.json.ps1' -Path 'D:\sgg_code\purchasing_management' -AllowRoot 'D:\sgg_code\purchasing_management' -Restore 'D:\sgg_code\.dsh-acl-recovery\acl-backup-<id>.json'`
  报告：`D:\sgg_code\.dsh-acl-recovery\acl-report-61b3b0ce5e7045fd9805659aace08387.jsonl`
- **防回归**：新增机器/换工作区时先跑 `scripts/doctor.ps1`（P0 提供）检查 Python/Node/Docker/DB/权限五项，任一失败即给出修复提示。
- **教训（可讲）**：报错信息里的 `Win32 5` 是 access denied，但**出现在沙箱装配阶段而非命令内部**——看到"命令没跑就失败"要先怀疑"运行环境装配"，而不是命令语法。

---

## G-002｜ bundled Python 缺全部后端依赖，且 pip 默认指向系统环境
- **问题现象**：`import fastapi/sqlalchemy/pydantic` 全部 MISSING；直接 `pip install` 会装到 DSH 运行时目录，污染宿主环境且换机器不可复现。
- **根因定位**：DSH 运行时自带的 Python 只预装数据科学依赖（numpy/pandas/openpyxl 等），Web 框架需自行安装。
- **修复手段**：项目内 `.venv`（3.12.14）作为唯一运行环境；所有命令走 `\.venv\Scripts\python.exe -m ...`；`backend/pyproject.toml` 锁定依赖，`make setup` 一键安装。
- **防回归**：`scripts/doctor.ps1` 检查 `.venv` 存在且 `import fastapi` 成功；CI 用同一 `pyproject.toml`。

---

## G-003｜Docker Desktop 未启动导致 PG 集成测试无法执行
- **问题现象**：`docker ps` 报 `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`；`docker-compose up` 会直接失败。
- **根因定位**：Docker 引擎（Linux 容器后端）未运行，只有 CLI 可用；与项目代码无关。
- **修复手段**：启动 Docker Desktop（等待 whale 图标变绿）→ `docker ps` 能列出容器后再 `make up`。测试侧用 `integration` 标记隔离，无 DB 时默认跳过而不是"红一片"。
- **防回归**：`tests/conftest.py::db_available` fixture 探测连接（1s 超时），不可用则 `pytest.skip`；`scripts/doctor.ps1` 输出 `docker daemon: DOWN (启动 Docker Desktop 后可跑集成测试)`。

## G-004｜`tsc --noEmit` 报大量 vite Plugin 类型不兼容（vite5 vs vite6 双实例）
- **问题现象**：`src/../vite.config.ts` 类型检查失败，错误链指向
  `vite@5.4.21`（vitest 依赖）与 `vite@6.4.5`（项目依赖）的 `UserConfig/PluginOption` 互不兼容。
- **根因定位**：`vite.config.ts` 从 `vitest/config` 引入 `defineConfig`（vitest 2.x 绑定 vite 5 类型），
  而 `plugins` 来自 vite 6 生态；pnpm 的隔离 node_modules 让两个 vite 版本同时存在，类型层面无法统一。
- **修复手段**：拆成两份配置——`vite.config.ts`（from `vite`，只放插件/proxy）与
  `vitest.config.ts`（from `vitest/config`，只放 test 配置，不引插件）。单测只测纯函数，不需要插件链。
- **防回归**：`pnpm run typecheck` 进 CI；`test_api_smoke` 之外新增前端纯函数测试 18 条。

## G-005｜`pnpm install` 退出码 1：ERR_PNPM_IGNORED_BUILDS (esbuild)
- **问题现象**：安装"成功"但 exit 1，`Run "pnpm approve-builds"`；`pnpm-workspace.yaml` 的 `onlyBuiltDependencies` 未被采纳。
- **根因定位**：pnpm 10 默认禁止依赖执行 postinstall；esbuild 的平台二进制实际由 optionalDependencies
  （`@esbuild/win32-x64`）提供，postinstall 只是校验/链接，因此缺失脚本不致命。
- **修复手段**：实测 `vitest`/`tsc`/`vite build` 全部通过后，把它登记为"环境噪声"；需要消除时交互执行
  `pnpm approve-builds` 或升级到支持非交互审批的 pnpm 版本。
- **防回归**：`scripts/doctor.ps1` 以"能否跑通 vite build"作为真实判据，而不是 install 退出码。

## G-006｜DSH 运行时没有 npm，误用 `node\bin\npm.cmd` 导致"node 找不到模块"
- **问题现象**：`& "$nodeDir\npm.cmd" install` 报 `Cannot find module ...\node\node_modules\npm\bin\npm-cli.js`（且被 PowerShell 包装成 Node 的 MODULE_NOT_FOUND）。
- **根因定位**：DSH 打包的 Node 目录只含 `node.exe`，不含 npm；系统 PATH 上另有 `D:\nodejs\npm.ps1`，
  但我的脚本硬编码了错误路径。
- **修复手段**：前端统一走 bundled `pnpm.mjs` + bundled `node.exe`；`scripts/dev.ps1` 固化了这组路径。
- **防回归**：`scripts/doctor.ps1` 检查 pnpm/node 是否存在，并打印实际使用的路径。

## G-007｜httpx `ASGITransport` 不执行 lifespan → `AttributeError: 'State' object has no attribute 'runs'`
- **问题现象**：`test_start_run_returns_503_when_db_down` 失败，异常栈落在 `app/api/deps.py::get_run_service`。
- **根因定位**：`ASGITransport(app=app)` 不会触发 startup/lifespan，`app.state.bus/runs` 从未装配；
  而依赖注入默认它们存在。
- **修复手段**：`deps.ensure_app_state()` 做懒初始化（lifespan 与依赖两条路径都安全）；生产行为不变。
- **防回归**：`backend/tests/test_api_smoke.py`（5 条无 DB 冒烟 + 1 条角色校验）。

## G-008｜GitNexus 的 FTS/BM25 扩展在 Windows 加载失败（不影响 impact/detect-changes）
- **问题现象**：`npx gitnexus analyze` 报
  `LOAD fts failed ... libfts.lbug_extension 找不到指定的模块 (Windows error 126)`，提示安装
  Microsoft Visual C++ 2015-2022 Redistributable (x64) 或把 Git 的 mingw64/bin 加入 PATH。
- **根因定位**：LadybugDB 的 FTS 扩展依赖 MSVC 运行时 / OpenSSL 3 DLL；本机未装 VC++ 运行时，
  `LoadLibrary` 返回 126（模块找不到）。仓库索引本身的 AST 解析已用 Tree-sitter（纯 JS/WASM）完成，
  因此**不影响符号图谱与调用链分析**，只影响全文/BM25 检索加速。
- **修复手段**：安装 VC++ 2015-2022 x64 运行时（或从 Git Bash 里运行使 mingw64/bin 进入 PATH）后
  `gitnexus analyze --repair-fts` 重建搜索索引。当前不影响 SOP 要求的 `impact` / `detect_changes`。
- **防回归**：`doctor.ps1` 不把 FTS 作为判据（判据是 `impact`/`detect-changes` 能否跑通）。

## G-009｜Windows 下 psycopg 异步驱动直接不可用（ProactorEventLoop）
- **问题现象**：Docker 里 PG 正常、`psql` 能连，但应用 `/healthz` 一直 `db:false`；
  `pytest -m integration` 3 条全部 skip；直接连库抛
  `InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode. Please use a compatible event loop ...`。
- **根因定位（两层）**：
  1. Windows 默认事件循环是 `ProactorEventLoop`，**psycopg 3 的异步连接不支持它**（SQLAlchemy asyncpg/psycopg 同理由此不可用）；
  2. 即使设置了全局策略也没用：`uvicorn 0.54` 在**单进程**模式下把 loop factory 硬编码成 Proactor——
     `uvicorn/loops/asyncio.py::asyncio_loop_factory(use_subprocess=False)` 里 `if sys.platform == "win32" and not use_subprocess: return asyncio.ProactorEventLoop`，
     而 `Server.run()` 通过 `asyncio_run(..., loop_factory=self.config.get_loop_factory())` 使用它，绕过 `set_event_loop_policy`。
- **修复手段**（三处入口，缺一不可）：
  1. `backend/run.py`：单进程启动时把 `config.get_loop_factory` 覆盖为 `lambda: asyncio.SelectorEventLoop`；
     `--reload` 走 `use_subprocess=True` 分支（uvicorn 自身会选 Selector）；
  2. `backend/app/core/loop.py::ensure_windows_selector_loop()`：`app/main.py` 导入时兜底、`tests/conftest.py` 在 pytest-asyncio 建循环前调用；
  3. 脚本（`scripts/smoke_e2e.py`）在 `asyncio.run` 前同样切策略。
- **防回归**：`backend/tests/test_loop.py`（策略切换 + 幂等）；`scripts/dev.ps1 api` 固定走 `run.py`；README 禁止用 `python -m uvicorn app.main:app`。

## G-010｜httpx 请求本机 API 返回 502，而 PowerShell 请求返回 200
- **问题现象**：`smoke_e2e.py` 第一步 `/healthz` 拿到 `status=502 body=''`，同端口 `Invoke-WebRequest` 却是 200。
- **根因定位**：httpx 0.28 在 `trust_env=True`（默认）时会读取**Windows 系统代理设置**（注册表 WinINET），
  而环境变量里 `HTTP_PROXY` 为空——所以排查时容易误判"没有代理"。公司代理对本机地址返回 502。
- **修复手段**：本机冒烟/探活统一 `httpx.AsyncClient(trust_env=False)`（`scripts/smoke_e2e.py` 已改）。
- **防回归**：脚本首步显式校验 `/healthz` 的 `db` 字段；失败时打印 `status + body` 便于区分"代理 502"与"应用 503"。
- **教训（可讲）**：`Invoke-WebRequest 200 / httpx 502` 这种"客户端不一致"优先怀疑代理与连接复用，而不是先怀疑服务端。

## G-011｜`docker pull` 拉取 pgvector 镜像中途 EOF
- **问题现象**：`docker compose up -d db` 报
  `failed to copy: httpReadSeeker: failed open: ... blobs/sha256:... EOF`，compose exit 1。
- **根因定位**：Docker Hub 拉取大层（631MB 镜像）时网络中断；Docker 会保留已完成层，重试可续传。
- **修复手段**：`docker pull pgvector/pgvector:pg16` 重试 1 次即成功（同层缓存生效），再 `docker compose up -d db`；
  DB 健康检查 `pg_isready` 通过后再跑迁移。
- **防回归**：`scripts/dev.ps1 up` 之后 `docker compose ps` 看到 `healthy` 才继续；`doctor.ps1` 把 5432 端口探测作为判据。

## G-012｜Python `hash()` 随机化让"同一 seed 数据不一致"
- **问题现象**：数据工厂里用 `random.Random(seed ^ hash(case_id))` 给每个单据组派生 RNG，单进程内可复现，
  但**跨进程**（每次 `python scripts/seed.py`）哈希不同 → 同一 seed 生成的金额/日期发生漂移，gold 与 DB 对不上。
- **根因定位**：CPython 对 `str` 的 `hash()` 默认开启随机化（`PYTHONHASHSEED`），这不是随机数种子的问题，是**派生种子不可复现**。
- **修复手段**：改用 `hashlib.sha256(f"{seed}:{case_id}").digest()[:8]` 派生 RNG（`persist.py`）；
  单测 `test_dataset_is_deterministic` 直接比对同一 seed 的完整 JSON 哈希。
- **防回归**：`test_seed.py::test_dataset_is_deterministic`（10 条 seed 测试之一）。

## G-013｜ReportLab 中文与 pypdfium2 版本探测
- **问题现象**：① ReportLab 默认字体不支持中文（显示黑块/报错）；② `import pypdfium2 as pdfium; pdfium.__version__` 抛 `AttributeError`。
- **根因定位**：① ReportLab 内置拉丁字体无 CJK 字形，需注册 CID 字体；② pypdfium2 不暴露 `__version__`（用 `V_PYPDFIUM2`）。
- **修复手段**：① `pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))` 并统一 `ParagraphStyle(fontName="STSong-Light")`；
  ② 版本检查只 import 不取 `__version__`。
- **防回归**：`test_seed.py::test_pdf_has_text_layer` 断言抽取文本包含"增值税"与发票号；`test_contract_contains_payment_terms` 断言"付款条款"。

## G-014｜Spec 里的路由阈值（text_ratio > 0.6）与真实文档不符
- **问题现象**：按 Spec §7.2 的判据，种子生成的原生 PO 的 `text_ratio` 只有 0.49，会被 `DocumentRouter` 判成 scanned/hybrid，
  把 80 份数字 PO 全部推去走 VLM（成本与错误率都会爆炸）。
- **根因定位**：阈值 0.6 是拍脑袋写的，未用真实文档标定；`text_ratio = 字符数 / 页面积(pt²) × 1e3` 的量纲下，
  A4 一页正文约 0.4~1.5，0.6 处于"表格类文档"的灰区。
- **修复手段**：用数据工厂的 234 份文档实测标定——原生 PO 0.435~0.509、发票 0.698~0.788、合同 0.760~0.812、扫描件 0；
  阈值改为 **≥0.15 判 native**、`<0.05` 判 scanned、其间为 hybrid；Spec §7.2 同步更新（v1.4）。
- **防回归**：`test_seed.py::test_scan_degrade_removes_text_layer`（原生 ratio ≥0.15、扫描件 ratio=0）；
  `test_seed_integrity.py::test_doc_text_ratio_consistent_with_text_layer`（DB 层同口径）。

## G-015｜gold 与真实业务计算不一致（PR 超预算标记）
- **问题现象**：M1 gold 里部分"clean"样本实际 `estimated > budget.available`，但 gold 记为 `verdict=pass`。
  一旦 W2 的规则引擎接上真实预算，eval 会稳定出现假阴性，且无法判断是模型错还是数据错。
- **根因定位**：生成时只在 `anomaly == "over_budget"` 分支强制该标记，未把"自然超预算"统一纳入判定；
  同时紧预算部门（可用额约为总额 1.9%）让绝大多数采购天然超预算。
- **修复手段**：`over_budget` 一律由 `estimated > available` 推导（anomaly 只是放大金额）；verdict 由 findings 统一推导；
  新增 6 条 `policy_block` 样本覆盖 block 类。并加不变量测试：gold 标记 == 实际可用额推导结果。
- **防回归**：`test_seed.py::test_pr_gold_matches_budget_and_verdict`、`test_m1_verdict_distribution_has_all_classes`。

## G-016｜`docker compose` 在缺少 `backend/.env` 时直接失败（新克隆不可用）
- **问题现象**：`docker compose config` 报 `env file ... not found`，导致即使只想 `up db` 也起不来；
  而 `.env` 被 gitignore，新克隆仓库必然缺失。
- **根因定位**：compose 文件用短语法 `env_file: [./backend/.env]`，默认 required。
- **修复手段**：改为长语法 `{path: ./backend/.env, required: false}`，缺省值由 `environment` 提供（DB URL / FakeLLM）。
- **防回归**：`scripts/dev.ps1 up` 前已有 `.env` 检查；README 快速开始第 1.5 步给复制命令。

## G-017｜pdfplumber 抽取的三个陷阱：捕获组、`page.search` 失效、表头分散
- **问题现象**：首版解析器全部 `PARSE_EXCEPTION: IndexError: no such group`；修掉后又发现
  ① `page.search("INV-2026-3001")` 返回空（发票号/金额搜不到），② 表头字段散落在多张表的行里，单行匹配拿不全。
- **根因定位**：
  1. `_find()` 统一 `match.group(1)`，但 `PO-\d{4}-\d{4}` 等正则**没有捕获组** → `IndexError`；
  2. pdfplumber 的 `search()` 依赖字符相邻性，表格单元格里的连字符/数字组合可能不连续，导致文本层可读但 `search` 为空；
  3. ReportLab 表格把 `订单号/下单日期`、`供应商/币种` 等放在**不同行**，只按"包含 订单号 的那一行"取键会漏字段。
- **修复手段**：
  1. `_search()` 按 `match.groups()` 决定取组 1 还是整体；
  2. bbox 采用两级回退：`page.search` → `page.extract_words()` 精确词匹配；金额候选去掉 `¥` 再搜；
  3. 表头统一走 `_header_map()`：扫描所有表的所有"标签/值"对，跳过行项目表头，`setdefault` 合并。
- **防回归**：`test_extraction.py` 8 条（字段一致性、bbox 归一化、三类模板路由、扫描件待 VLM、篡改合计可检出）。
- **教训（可讲）**：`extract_text()` 能读 ≠ `search()` 能定位；**以表格结构为主、正则只做兜底**比"全文正则一把梭"稳一个量级。

## G-018｜repair loop 不该"什么错都重试"：Schema 失败重试、业务校验失败进人工
- **问题现象**：首版 VLM 单测里 `{"invoice_no": "BROKEN"}`（无行项目）居然**通过 Pydantic 校验**（其余字段都有默认值），
  结果 repair 循环以为成功，随后被业务校验器判 high —— 如果继续把业务错误回填模型重试，等于让模型"猜"业务规则。
- **根因定位**：`invoice_no` 是唯一必填字段，`lines`/`seller_name` 都是可空默认；Schema 合法 ≠ 业务可用。
- **修复手段**：明确两层——Schema/JSON 解析失败才 repair（`max_attempts=3`）；业务校验（LINES_EMPTY / SELLER_MISSING 等）
  只产 `ValidationIssue` 并进 `needs_review_fields`，不再消耗模型调用。
- **防回归**：`test_vlm_extraction.py` 四条（repair 恢复 / repair 耗尽 / 业务缺失→needs_review / 无 vision→VLM_REQUIRED）。

## G-019｜合成数据里的"未来发票"污染抽取基线（DATE_FUTURE）
- **问题现象**：抽取基线显示 64 份原生发票里 36 份 `needs_review`，全部命中 `DATE_FUTURE`（开票日期晚于今天）。
- **根因定位**：数据工厂用 `BASE_DATE + 随机天数` 生成承诺交期/收货/开票日期，末端单据落到 2026-11 之后；
  发票日期由"收货 + 1~10 天"推导，于是出现 2026-11/12 的"未来发票"。校验器是对的，错在数据。
- **修复手段**：生成时把收货日期钳制到 `BASE_DATE-2`、开票日期钳制到 `BASE_DATE-1`（保持"收货 < 开票"）；
  重跑基线后 `needs_review` 从 36 降到 **1**（仅 1 份边缘样本），字段准确率 100%。
- **防回归**：`scripts/extract_eval.py` 的 `per_doc_type.needs_review` 作为回归观察项；`test_seed.py` 校验单据算术与规模。

## G-020｜尾差误报：浮点噪声 + 行级差异重复计入
- **问题现象**：M3 基线里 64 组 clean 样本有 2 组被判 `tail`（出现 ¥0.001~0.02 的"假尾差"）；另有 price/tail 同时注入的样本漏报 tail。
- **根因定位**：首版 `tail = 发票合计 − Σ(consumed × 单价 × (1+税率))`，浮点乘加后再与已按分取整的发票合计比较，
  误差可达 1~2 分；同时 `amount` 与 `tail` 二选一的写法，在已存在 `price` 差异时把 tail 一并吞掉。
- **修复手段**：尾差基准改为 **`发票合计 − Σ(已匹配发票行的含税金额)`**（行内已按分取整，无浮点噪声），
  尾差判定独立于"金额抑制"逻辑：行级差异已解释的金额不重复报 `amount`，但尾差仍单独识别。
- **防回归**：`test_reconcile_tail_releases_with_note`、`test_reconcile_does_not_double_report_amount`、`test_matching.py::test_reconcile_clean_group_passes`。

## G-021｜overall 判定用了"过期快照"：terms_conflict 漏判 hold
- **问题现象**：`terms_conflict` 样本的 `overall` 输出 `release_with_note`，而 gold 是 `hold`——差异清单里明明有 `terms_conflict`。
- **根因定位**：`hard_types` 在"金额检查"之前就算好了，而 `terms_conflict` 的 diff 在之后才 `append`；
  `overall` 用的是旧集合，于是硬错误被降级成提示。
- **修复手段**：`overall` 在**所有 diff 生成后**重新计算 `hard_types`；金额抑制处也改为实时读取 diff 类型集合。
- **防回归**：`test_matching.py::test_reconcile_terms_conflict_detected`（断言 hold + terms_conflict 存在）。

## G-022｜数据工厂的"随机到货不足"产生了 gold 未建模的差异
- **问题现象**：基线出现 4 例"gold 应为 pass、引擎报 qty_over"的假阳性。
- **根因定位**：`if rng.random() < 0.10: received_qty -= 1..2` 对所有组生效，但没有对应 gold 差异记录；
  引擎按 `min(ordered, received)` 正确判超发，反而被算作误报。
- **修复手段**：到货不足只对 `qty_over` 槽位组注入；gold 与注入逻辑一一对应（数据工厂原则：**每条异常必须有 gold**）。
- **防回归**：`test_seed.py::test_pr_gold_matches_budget_and_verdict` 同类思路 + `m3_eval.py` 的 `fp=0` 观察项。

## G-023｜扫描件比例漂移（15% → 30%）与 normalizer 连写陷阱
- **问题现象**：fixed seed 下扫描件组数从 15/80 漂到 24/80；同时 `"16G 512G"` 被单独正则识别成 `storage=16G`。
- **根因定位**：扫描件概率用逐组 `rng.random() < 0.15`，在 RNG 流被其他分支改变后同 seed 也会大幅波动；
  `(\d+)G` 单值正则会先命中内存数字，内存/存储**成对写法**未被优先解析。
- **修复手段**：扫描件改为**精确配额 + `rng.sample` 打散**（`round(80×15%)=12` 组）；
  normalizer 先匹配 `16G 512G` / `16G/512G` 成对模式，再对孤立 `NNNG` 做 ≥128→存储、<128→内存的兜底分类。
- **防回归**：`extraction_baseline.json` 的 `scanned/docs` 比例；`test_matching.py::test_normalize_english_and_chinese_names_share_bucket`。

## G-024｜DB 路径静默漏判 terms_conflict：`invoice.remark` 列缺失
- **问题现象**：纯函数基线里 `terms_conflict` 100% 命中，但 LangGraph 从 DB 加载数据后，同一组三单的 `overall` 从 `hold` 变成 `pass`。
- **根因定位**：付款条款冲突判定依赖发票备注（"预付 50%"），数据工厂/gold 里存在该字段，但 `invoice` 表**没有 remark 列**，
  DB 路径读不到 → 差异静默消失（不是抛错，是"看起来正常"的错误，最危险）。
- **修复手段**：`Invoice` 增加 `remark` 列 + Alembic 0002 迁移；种子 persist 写入 remark；图加载时透传。
  另将图级测试的期望改为**读取同 case 的 gold 文件**，而不是硬编码（发现测试用例编号也错位过，见 G-025）。
- **防回归**：`test_matching_graph.py::test_graph_interrupts_before_finalize_then_release_writes_accounting`
  断言 `match_diff` 类型集合 == gold `expected_diffs` 类型集合。

## G-025｜集成测试"硬编码期望"导致跨组配对：PO `i+1` vs 发票 `3000+i`
- **问题现象**：图级测试用 `PO-2026-0001` + `INV-2026-3001` 组合，跑出来 `overall=pass`，与 gold 的 `hold` 不符。
- **根因定位**：种子里 PO 编号是 `i+1`、发票编号是 `3000+i`，两个序列**整体错位一位**；测试把不同组的 PO 和发票配对，
  行对齐自然对不上，判定结果与 gold 无关。纯函数基线用的是同一组内数据，所以没暴露。
- **修复手段**：测试成对取 `(PO-2026-{i+1:04d}, INV-2026-{3000+i:04d}, m3-{i+1:04d})`；断言从 gold 文件动态读取；
  对"已核销"的 PO 加 skip 守卫（重复跑集成测试不会因脏数据误判）。
- **防回归**：`_gold(case_id)` 动态期望 + `m3-0001/0002/0003` 三组覆盖放行/挂起/RunService 全流程。

## G-026｜SSE 终端竞态：run 状态先落库、`run.end` 事件后写入 → 客户端等不到完成信号
- **问题现象**：e2e 冒烟 `[4/5] resume` 之后只收到 `finalize node.start`，随后 60s 超时"未收到 run.end"；
  但查 DB：`agent_run.status=succeeded`，事件 seq 9/10/11（node.start/node.end/run.end）齐全——事件没丢，是客户端提前被断开。
- **根因定位**：SSE 生成器用 `status in TERMINAL and not events: return` 判断收尾；而 `finalize` 是**先 commit `run.status=succeeded`，
  再 emit `node.end`/`run.end`**。客户端恰好在两次写入之间发起第二次 poll：事件为空 + 状态已终端 → 直接关闭流。
  属于"状态与事件不在同一事务"的经典竞态，压测/慢库时必现。
- **修复手段**：终端且无新事件时给 **2×50ms 宽限重查**（期间有新事件则复位计数），仍为空才关闭；
  比"把 run.end 提前写"更稳，因为不改事件/事务契约。
- **防回归**：`scripts/smoke_e2e.py`（断言 finalize + run.end + snapshot artifact）；修复后
  `events_after_resume=3`、`SMOKE_E2E=OK`。

## G-027｜LangGraph `TypedDict` 未声明的 state key 会被静默丢弃
- **问题现象**：M3 报告里 `documents` 在前端一直是空对象 `{}`，PDF 抽屉拿不到发票 doc_id；但后端 `load_subject` 明明查到了 doc 并 return 了 `"documents"`。
- **根因定位**：`StateGraph(MatchingState)` 只保留 TypedDict 声明的通道；`load_subject` 返回值里多了一个未声明的 `documents` 字段，
  节点更新被**静默忽略**——不报错、不告警，最难查的一类（还是靠端到端冒烟发现）。
- **修复手段**：在 `MatchingState` 显式声明 `documents: dict[str, str | None]`；并把"报告必须含 documents/po_lines/invoice_lines"
  写成端到端冒烟断言，任何字段再次被吞都会在提交前暴露。
- **防回归**：`scripts/smoke_m3.py` 的 `[3/6]`（report.documents.invoice）与 `[4/6]`（page.png + annotations）断言。

## G-028｜bbox 百分比出现浮点尾巴：`0.1 * 100 = 10.000000000000002%`
- **问题现象**：前端 PDF 抽屉的 bbox 覆盖层单测失败，style 左侧变成 `10.000000000000002%`；真实浏览器里表现为轻微错位/难以断言。
- **根因定位**：归一化坐标转百分比直接 `value * 100`，JS 二进制浮点误差进入 CSS 字符串；这类误差在断言与视觉对齐里都不可接受。
- **修复手段**：`annotationBoxStyle` 统一 `parseFloat((v*100).toFixed(4))`，既消掉尾巴又保留 0.01% 精度；单测改为断言精确字符串。
- **防回归**：`frontend/src/lib/m3.test.ts::annotationBoxStyle`（含负值/越界钳制用例）。

## G-029｜规则求值器里 dict 字面量的"提前求值"陷阱
- **问题现象**：MKT 类 PR 一跑规则就抛 `TypeError: '>' not supported between NoneType and bool`，但规则表达的是 `==`。
- **根因定位**：比较算子写成 `{">": left > right, "==": left == right, ...}[operator]`，**dict 字面量会先求值所有分支**，
  即使 operator 是 `==`，`None > True` 也已经先炸了。
- **修复手段**：改为 if/elif 逐分支求值；同时把比较类型错误包成 `RuleEvalError`（规则问题显式报错，不静默放过）。
- **防回归**：`test_rules.py::test_comparisons_do_not_crash_on_type_mix`、`test_operator_whitelist_rejects_unknown`。

## G-030｜psycopg 参数类型推断失败：`$2 IS NULL` 与 `jsonb_build_array($1)`
- **问题现象**：M1 检索 SQL 报 `AmbiguousParameter: could not determine data type of parameter`。
- **根因定位**：PG 服务端无法从 `$2 IS NULL OR col = $2` 推断参数类型；`jsonb_build_array($1)` 同样无法推断 text 入参。
- **修复手段**：SQL 动态拼接可选条件（`AND category_code=:category` 只在有值时拼接），
  JSONB 入参显式 `CAST(:item_name AS text)`；所有可选参数不再依赖 PG 推断。
- **防回归**：`scripts/m1_eval.py` 全量跑通（120 条 × 3 路检索）。

## G-031｜`pr.status` 是 varchar(16)：写 `prechecked_needs_info` 直接 DataError
- **问题现象**：M1 预检 finalize 在 commit 时抛 `StringDataRightTruncation`，事务回滚、artifact 没写。
- **根因定位**：`pr.status` 定义 varchar(16)，我写入了 21 字符的 `prechecked_needs_info`；"看起来只是一次赋值"的长度问题要到 flush 才暴露。
- **修复手段**：状态统一写 `prechecked`；"是否需要补信息"由 report.findings / artifact 表达，不塞进状态枚举。
- **防回归**：`test_precheck_graph.py::test_precheck_graph_accept_applies_patch`（断言 `pr.status.startswith("prechecked")`）。

## G-032｜目录"同名不同配"污染检索指标：同名 SKU 规格不一致
- **问题现象**：M1 检索 top-1 只有 49.5%，但抽查发现同名 SKU 的 **spec_bucket 不一样**。
- **根因定位**：数据工厂里品名按 `i % len(name_pool)`、规格按 `i % len(spec_pool)` 独立取模（10 与 4/3 互质周期不一致），
  生成出"同一品名、不同内存/周期"的重复 SKU；这既不符合真实目录，也让"同名等价类"判定永远失败。
- **修复手段**：规格改为跟随品名索引（`spec_pool[name_index % len(spec_pool)]`），同名 SKU 规格一致；
  检索评测按"sku_code 相等 或 (name, spec_bucket) 相同"的等价类判定。
- **防回归**：`scripts/m1_eval.py` 的 `retrieval_top1_accuracy`（修复后 100%，样本 105）。

## G-033｜demo 冒烟被真实图"接管"：`smoke_e2e.py` 用 m1 占位 subject 直接崩
- **问题现象**：M1 图上线后，原 demo 冒烟（`POST /runs {"module":"m1","subject":{"pr_id":...}}`）报
  `M1 运行需要 subject.pr_no`，而这条冒烟本意是覆盖 SSE/HITL/恢复契约，不关心业务模块。
- **根因定位**：架构演进（m1 从占位节点换成真实图）后，回归脚本仍把 m1 当"任意 subject"的演示模块。
- **修复手段**：demo 冒烟改走仍是占位路径的 `module=m2` + supplier subject；真实 M1 链路由 `smoke_m1.py` 覆盖。
- **防回归**：`scripts/smoke_e2e.py`（SSE/中断/恢复）、`scripts/smoke_m1.py`（真实 M1）、`scripts/smoke_m3.py`（真实 M3）三件套。

## G-034｜M2 证照"随机过期"造成 G1 误杀：种子基准日选在 2025-12-31
- **问题现象**：M2 首次评测 30 家里 8 家被 G1 否决，明细里却没有 `license_invalid` 标记，结论分布直接塌成 0 家 approve。
- **根因定位**：证照生成 `valid_to = 2025-12-31 + rand(30..900)`，在"当前日期 2026-10"下约 30% 的营业执照已过期——
  数据工厂的随机性越过了业务契约（本应只有 expiring 集合刻意临期）。
- **修复手段**：基准日改为 `2027-12-31 + rand(30..900)`，仅 expiring 集合写 2026-11-01；重跑 seed 与 M2 基线。
- **防回归**：`scripts/m2_eval.py` 的闸门误报率（0%）+ `test_scoring.py::test_expired_license_gate`（显式过期才命中）。

## G-035｜M2 闸门短路路径的 `llm_status` 默认值不对
- **问题现象**：G1 命中直接 reject 的 run，report 里 `llm_status="unavailable"`，前端会误显示"LLM 未配置"，而不是"闸门命中跳过"。
- **根因定位**：gate_check → compose_result 的条件边绕过了 llm 节点，`state.get("llm_status", "unavailable")` 取默认值；
  `"skipped_gate_hit"` 只写在 llm 节点内部，没被短路路径覆盖。
- **修复手段**：compose 时按 `gate_veto` 推导：`state.get(...) or ("skipped_gate_hit" if veto else "unavailable")`。
- **防回归**：`test_supplier_graph.py::test_gate_hit_short_circuits_and_finalize_writes_eval`（断言 skipped_gate_hit + dimensions=[]）。

## G-036｜冷启动置信度没强制 low：pending 供应商算出 medium/high
- **问题现象**：冷启动供应商（无历史）report 里 confidence=medium/high，违反 Spec §6.5"没有历史数据的 85 分是假的"。
- **根因定位**：`confidence()` 只按 completeness/sample/freshness 公式算；pending 供应商在外卖历史表里仍有行（数据工厂循环分配），
  公式结果客观"高"，但业务上尚未准入，必须强制降级。
- **修复手段**：`aggregate_metrics` 在 `cold_start=True` 时覆盖 `level="low"` 且 `value≤0.49`；结论由 `verdict_for(cold_start=True)` 强制 conditional；条件附 3 个月试用期 + 首单 100% 抽检。
- **防回归**：`test_supplier_graph.py::test_cold_start_forced_conditional_low_confidence`、`scripts/m2_eval.py` 的 cold_start 一致率（100%）。

## G-037｜评测脚本把 `risk_flags` 里值为 False 的键当"预期闸门"
- **问题现象**：M2 评测闸门召回率只有 58.9%，missing 列表里每个闸门供应商都缺 5 个"别的闸门"；误报率也被算成 16.7%。
- **根因定位**：seed 的 gate 供应商写的是完整字典 `{license_invalid: idx==3, dishonest_judgment: idx==7, ...}`（其余为 False）；
  评测用 `for flag in risk_flags if flag in FLAG_TO_GATE` 把 False 也计入 expected——"标记存在 ≠ 标记为真"。
- **修复手段**：expected 只取 `value` 为真的键；误报改为"无 veto 预期却出现 veto 闸门"（并与冷启动/条件项区分）。
- **防回归**：`scripts/m2_eval.py` 输出 gate_detection.recall=100%、false_positive_rate=0、extra_hits=0。

## G-038｜本机 Docker 无法访问 Docker Hub：镜像构建卡在拉基础镜像
- **问题现象**：`docker compose build api` 报 `failed to authorize ... Post "https://auth.docker.io/token": dial tcp 162.125.83.1:443: connectex: A connection attempt failed`；
  `docker pull node:22-slim` 20s 超时。本机只缓存了 `pgvector/pgvector:pg16`，python/node 基础镜像都没有。
- **根因定位**：Docker daemon 直连 Docker Hub 被网络拦截（无 HTTP_PROXY 环境、Docker Desktop 未配代理），与 Dockerfile 无关；
  shell 里也没有代理变量，改 `pip/npm` 镜像源只影响构建阶段命令、不影响 daemon 拉镜像。
- **修复/规避**：交付层保留根 `Dockerfile` + compose `api` 服务；用 `docker compose config` 验证编排合法性，
  用**非容器生产形态**（uvicorn 托管 `frontend/dist`）验证 SPA/API 同端口路径；文档明确"构建需 Docker Desktop 配置代理"。
- **防回归**：`docs/DEPLOYMENT.md` §1 写清前置条件与典型报错；把 `docker compose config` 纳入提交前检查（本轮已过）。

## G-039｜FakeLLM 响应污染 prompt 缓存：切真实模型后拿到 `{"ok": true}`
- **问题现象**：真实网关配好后，M2 复核仍报 `ValidationError ... input_value={'ok': True}`；llm_call 显示 provider=zhipu、latency=0、tokens_out=3、cost=0。
- **根因定位**：离线模式（FORCE_FAKE_LLM=1）时 `build_providers` 用**真实 provider 名**构造 FakeLLM，prompt 缓存 key 只含 provider/model/prompt/schema；
  fake 的 `{"ok": true}` 被写进磁盘缓存。切回真实模型后同一 prompt 命中缓存，直接返回假响应。
- **修复手段**：`_cached_router(force_fake=True)` 不注入 PromptCache（fake 只读不写，避免污染）；清理 `.cache/prompts`；并让 llm_call 记录 cache_hit 以便排查。
- **防回归**：真实 M2 首跑不再出现 `{'ok': True}`；可观测页 llm_call 能区分 cache_hit。

## G-040｜`${DEEPSEEK_API_KEY}` 在 models.yaml 里展开成空串：Settings 读 .env 但不注入 os.environ
- **问题现象**：配置真实网关后所有请求 `LLM_UPSTREAM`（base_url/api_key 为空），而 `curl` 手工调用正常。
- **根因定位**：`app/llm/config.py` 的 `_expand_env` 只查 `os.getenv`；pydantic-settings 读取 `.env` 后**不会**写回 os.environ，
  于是 `${DEEPSEEK_BASE_URL}` 等全部变空串。
- **修复手段**：加载 yaml 时先解析 `backend/.env` 合并进变量表（os.environ 优先），再展开；不把密钥写进任何 tracked 文件。
- **防回归**：`python -c`/网关探针能跑通 `router.complete`；`test_llm_config.py` 校验 roles/pricing。

## G-041｜OpenCode Go 网关：端点不是 `/zen/go/v1/chat/completions`，且必须有 `x-opencode-session`
- **问题现象**：`GET /zen/go/v1/models` 200，但 `POST /zen/go/v1/chat/completions` 返回 400 空 body；即使换 header 仍 400。
- **根因定位**：官方文档（docs.pencil.dev 同款 Go 文档）给出推理端点 `https://opencode.ai/inference/go/openai/v1/chat/completions`；
  且网关要求 `x-opencode-session`（稳定会话 id）+ 非通用 User-Agent。
- **修复手段**：provider 配置新增 `extra_headers` 与 `session_header`，provider 进程内生成稳定 session；models.yaml 配好 session 头与 UA；`/zen/go/v1` 只用于 models 探测。
- **防回归**：真实 M2 复核可成功；文档/README 写清端点和环境变量。

## G-042｜reasoning tokens 吃掉 max_tokens：JSON 在 2048 处被截断
- **问题现象**：M2 复核日志 `JSONDecodeError: Unterminated string starting at ... char 1707`；tokens_out 恰好 2048。
- **根因定位**：DeepSeek/GLM 先输出 reasoning tokens，再输出 content；`max_tokens` 把两者一起限制。
- **修复手段**：`reason`/`reason_small` 提到 4096 并放宽超时；JSON 提取容忍 ```json 围栏与前后缀；对"claim 超 80 字/条件超 3 条/memo 超 120 字"
  做归一化截断后再做 Pydantic 校验（只截表达越界，不伪造事实）；失败才降级 unavailable。
- **防回归**：M2 真实运行 `llm_status=ok`、top_risks=5；失败路径仍有 `llm_status=unavailable` 单测。

## G-043｜llm_call 不归因到 run：可观测页 tokens/cost 全是 0
- **问题现象**：M2 真实调用成功，`llm_call` 有 rows，但 `agent_run.tokens_in/out/cost` 全 0，列表与 summary 显示 0；llm_call.run_id 为 NULL。
- **根因定位**：supplier 复核调用 `router.complete(...)` 未传 `run_id`；RunService 也从未把 llm_call 汇总回 agent_run。
- **修复手段**：`call_llm_review` 接收 run_id 并传给 router；RunService 新增 `_sync_run_usage`（sum llm_call → agent_run），在 awaiting_review 与 finally 各同步一次。
- **防回归**：`test_ops_api.py::test_trace_contains_events_and_llm_ledger`；真实运行 DB 可看到 run.tokens 与 llm_call 一致。

## G-044｜SPA 深链 404：`StaticFiles(html=True)` 不会回退 index.html
- **问题现象**：`/` 正常，但 `/precheck?run=<id>` 返回 `{"detail":"Not Found"}`，截图/刷新都失败。
- **根因定位**：FastAPI `StaticFiles(directory, html=True)` 只把目录请求映射到 index.html，未知路径直接 404，React Router 的 history 路由没有入口。
- **修复手段**：新增 `SPAStaticFiles`，404 且路径没有扩展名时回退 `index.html`；静态资源缺失仍返回 404（避免 MIME 错乱）。
- **防回归**：验证 `/precheck?run=test` 返回 200 且含 `<div id="root">`；`scripts/screenshots.py` 深链全部成功。

<!-- 追加位置：新踩坑写在本行上方 -->
