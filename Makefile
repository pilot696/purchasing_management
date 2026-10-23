# 本项目主要面向 Windows 开发，PowerShell 入口见 scripts/dev.ps1。
# Makefile 供 CI / Linux 使用；用 .RECIPEPREFIX 避免 tab 丢失问题。
.RECIPEPREFIX = >

PYTHON ?= ../.venv/Scripts/python.exe
FRONTEND_NODE ?= node
PNPM ?= pnpm

.PHONY: help setup up down migrate test test-int eval seed lint api web

help:
> @echo "setup     安装后端依赖（需要先有 .venv，见 scripts/dev.ps1 setup）"
> @echo "up        启动 PostgreSQL 16 + pgvector"
> @echo "down      停止容器"
> @echo "migrate   Alembic 基线迁移（含 pgvector 扩展）"
> @echo "test      后端单测（FakeLLM，零网络）"
> @echo "test-int  集成测试（需 DB）"
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

test:
> cd backend && $(PYTHON) -m pytest

test-int:
> cd backend && $(PYTHON) -m pytest -m integration

api:
> cd backend && $(PYTHON) -m uvicorn app.main:app --reload --port 8000

web:
> cd frontend && $(PNPM) dev
