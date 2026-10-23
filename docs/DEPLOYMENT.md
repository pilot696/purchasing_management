# 部署说明（Docker Compose 一键起）

> 目标：新机器 `git clone → docker compose up -d --build → seed → 打开 http://localhost:8000`。
> 本地非 Docker 的生产形态（uvicorn 直接托管前端 dist）见 §4。

## 1. 前置条件

- Docker Desktop（含 compose v2）；
- 首次构建需要能访问 Docker Hub / PyPI / npm（拉取 `pgvector/pgvector:pg16`、`python:3.12-slim`、`node:22-slim` 基础镜像；
  构建阶段会 `pip install -r backend/requirements.txt` 与 `pnpm install --frozen-lockfile`）。
  若公司网络或代理限制访问，请先给 Docker Desktop 配置代理（Settings → Resources → Proxies），
  否则会报 `failed to authorize ... auth.docker.io` / 拉取超时。

## 2. 启动

```bash
cp backend/.env.example backend/.env   # 可选：不建也能起，默认 FORCE_FAKE_LLM=1
docker compose up -d --build           # db + api（api 启动时自动 alembic upgrade head）
docker compose exec api python scripts/seed.py --seed 42   # 首次初始化演示数据
```

打开 <http://localhost:8000>（api 同时托管前端构建产物，SPA 路由回退到 `index.html`）。

演示角色切换：前端右上角下拉（或请求头 `X-Demo-Role: requester|buyer|finance|admin`）。

## 3. 验证

```bash
curl -s localhost:8000/healthz
# {"status":"ok","db":true,...}

# 真实 e2e（需要先 seed；容器内或宿主机均可，--base 指向 8000）
python scripts/smoke_m1.py --base http://127.0.0.1:8000
python scripts/smoke_m2.py --base http://127.0.0.1:8000
python scripts/smoke_m3.py --base http://127.0.0.1:8000

docker compose ps        # db healthy / api healthy
docker compose logs -f api
```

## 4. 不用 Docker 的生产形态（本机）

```bash
cd frontend && pnpm run build          # 产物 frontend/dist
cd ../backend
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/procurement \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
# FastAPI 检测到 ../frontend/dist 后同端口托管 SPA + API
```

## 5. 配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://app:app@db:5432/procurement`（compose 内） | PostgreSQL 16 + pgvector |
| `FORCE_FAKE_LLM` | `1` | 离线演示用 FakeLLM；接真实模型时置 `0` 并配置 provider key |
| `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` 等 | 空 | 见 `backend/.env.example` 与 `config/models.yaml` 的角色路由 |
| `DEMO_STEP_DELAY_MS` | 350 | 演示节点间延迟（便于观察 SSE 时间线） |
| `LOG_LEVEL` | `INFO` | 结构化日志 |

> 真实 LLM 接入后需重跑 `scripts/{extract,m1,m2,m3}_eval.py` 回填成本/延迟/质量指标（`docs/EVAL_REPORT.md` §7）。

## 6. 生产注意事项（诚实边界）

1. **单进程约束**：当前 LangGraph 使用 `MemorySaver`（进程内 checkpointer），HITL 中断状态存在内存里；
   多副本部署必须换成 Postgres checkpointer 或粘性会话，否则 `resume` 可能找不到中断点。compose 默认单副本。
2. **迁移**：容器启动命令已含 `alembic upgrade head`（幂等）；生产建议拆成独立 migration job。
3. **前端缓存**：静态资源文件名带内容哈希，可放心长缓存；`index.html` 建议 no-cache（反向代理配置）。
4. **数据卷**：`pgdata` volume 持久化；`data/files` 为 seed 生成的 PDF 文件，容器内每次重建需重新 seed（幂等，会重建数据）。
5. **HTTPS/鉴权**：demo 用 `X-Demo-Role` 模拟 RBAC，生产需接企业 SSO/OIDC 与 TLS 终止。
6. **观测**：`run_event` / `llm_call` / `audit_log` 已落库；生产可加 Prometheus 指标与结构化日志采集。

