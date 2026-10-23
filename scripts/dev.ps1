# 开发入口（Windows / PowerShell 7）
# 用法：
#   pwsh -File scripts/dev.ps1 setup         # 建 .venv 并安装后端依赖
#   pwsh -File scripts/dev.ps1 up            # 起 PostgreSQL(pgvector)
#   pwsh -File scripts/dev.ps1 migrate       # 建表（Alembic 基线 + pgvector 扩展）
#   pwsh -File scripts/dev.ps1 test          # 后端单测（FakeLLM，零网络）
#   pwsh -File scripts/dev.ps1 test-int      # 集成测试（需 DB）
#   pwsh -File scripts/dev.ps1 api           # 起 FastAPI :8000
#   pwsh -File scripts/dev.ps1 web-install   # 装前端依赖
#   pwsh -File scripts/dev.ps1 web           # 起 Vite :5173
#   pwsh -File scripts/dev.ps1 doctor        # 环境自检
param([Parameter(Mandatory = $true)][string]$Command)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
$runtimePython = 'C:\Users\91975\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$nodeExe = 'C:\Users\91975\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
$pnpmCli = 'C:\Users\91975\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs'

function Get-Python {
    if (Test-Path $python) { return $python }
    if (Test-Path $runtimePython) { return $runtimePython }
    throw '找不到 Python：请安装 Python 3.12 或在 PATH 提供 python'
}

switch ($Command.ToLower()) {
    'setup' {
        if (-not (Test-Path $python)) {
            & (Get-Python) -m venv .venv
        }
        & $python -m pip install --upgrade pip
        & $python -m pip install -e 'backend[dev]'
        Write-Host '后端依赖安装完成' -ForegroundColor Green
    }
    'up' { docker compose up -d db; docker compose ps }
    'down' { docker compose down }
    'migrate' {
        Set-Location (Join-Path $root 'backend')
        & $python -m alembic upgrade head
        Write-Host 'schema 迁移完成（含 pgvector / pg_trgm 扩展）' -ForegroundColor Green
    }
    'test' {
        Set-Location (Join-Path $root 'backend')
        & $python -m pytest
    }
    'test-int' {
        Set-Location (Join-Path $root 'backend')
        & $python -m pytest -m integration
    }
    'api' {
        Set-Location (Join-Path $root 'backend')
        & $python -m uvicorn app.main:app --reload --port 8000
    }
    'web-install' {
        Set-Location (Join-Path $root 'frontend')
        & $nodeExe $pnpmCli install
    }
    'web' {
        Set-Location (Join-Path $root 'frontend')
        & $nodeExe $pnpmCli dev
    }
    'doctor' { & (Join-Path $PSScriptRoot 'doctor.ps1') }
    default { throw "未知命令: $Command（可用：setup/up/down/migrate/test/test-int/api/web-install/web/doctor）" }
}
