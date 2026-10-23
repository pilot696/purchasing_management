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
COPY evals/ ./evals/

# 防御：Windows 工作区拷贝可能带 CRLF，容器内 shell 脚本必须 LF。
RUN sed -i 's/\r$//' scripts/docker_entrypoint.sh
COPY --from=web /web/dist ./frontend/dist

# 容器入口：自动迁移 + 首次自动 seed（SEED_ON_START=0 可关闭）+ 启动 API
# 详见 scripts/docker_entrypoint.sh
EXPOSE 8000
CMD ["sh", "/workspace/scripts/docker_entrypoint.sh"]


