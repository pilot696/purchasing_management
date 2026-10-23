# syntax=docker/dockerfile:1

# --------------------------------------------------------------------------- 前端构建
FROM node:22-slim AS web
WORKDIR /web
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./ 
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

# --------------------------------------------------------------------------- 运行时（API + 静态前端）
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /workspace

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY data/aliases.yaml ./data/aliases.yaml
COPY --from=web /web/dist ./frontend/dist

# 启动前先跑迁移（幂等）；seed 请在首次启动后执行：
#   docker compose exec api python scripts/seed.py --seed 42
EXPOSE 8000
CMD ["sh", "-c", "cd backend && alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
