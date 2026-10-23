# 本项目主要面向 Windows 开发，PowerShell 入口见 scripts/dev.ps1。
# Makefile 供 CI / Linux 使用；用 .RECIPEPREFIX 避免 tab 丢失问题。
.RECIPEPREFIX = >

PYTHON ?= ../.venv/Scripts/python.exe
FRONTEND_NODE ?= node
PNPM ?= pnpm

.PHONY: help setup up down migrate seed test test-int eval lint api web up-app seed-docker design

help:
> @echo "setup     安装后端依赖（需要先有 .venv，见 scripts/dev.ps1 setup）"
> @echo "up        启动 PostgreSQL 16 + pgvector"
> @echo "down      停止容器"
> @echo "migrate   Alembic 基线迁移（含 pgvector 扩展）"
> @echo "seed      生成合成数据（master + 230 份 PDF + gold，固定 seed=42）"
> @echo "test      后端单测（FakeLLM，零网络）"
> @echo "test-int  集成测试（需 DB）"
> @echo "eval      四套离线基线（extract/m1/m2/m3）"
> @echo "up-app    构建并启动 db+api（api 同端口托管前端 dist，:8000）"
> @echo "seed-docker 容器内初始化演示数据"
> @echo "design    重新生成 pen.dev 设计稿与预览"
> @echo "api       本地起 FastAPI（5173 代理到 8000）"
> @echo "web       本地起 Vite"

setup:
> cd backend && $(PYTHON) -m pip install -e ".[dev]"

up:
> docker compose up -d db

down:
> docker compose down

migrate:
> cd backend && DATABASE_URL=$${DATABASE_URL:-postgresql+psycopg://app:app@localhost:5432/procurement} $(PYTHON) -m alembic upgrade head

seed:
> $(PYTHON) scripts/seed.py --seed 42

test:
> cd backend && $(PYTHON) -m pytest

test-int:
> cd backend && $(PYTHON) -m pytest -m integration

api:
> cd backend && $(PYTHON) run.py --port 8000 --reload

web:
> cd frontend && $(PNPM) dev

eval:
> $(PYTHON) scripts/extract_eval.py
> $(PYTHON) scripts/m1_eval.py
> $(PYTHON) scripts/m2_eval.py
> $(PYTHON) scripts/m3_eval.py

up-app:
> docker compose up -d --build

seed-docker:
> docker compose exec api python scripts/seed.py --seed 42

design:
> $(PYTHON) scripts/gen_design_pen.py
