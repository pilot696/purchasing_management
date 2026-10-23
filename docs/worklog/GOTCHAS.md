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

<!-- 追加位置：新踩坑写在本行上方 -->
