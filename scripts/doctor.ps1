# 环境自检：python / node / pnpm / docker daemon / PostgreSQL
# 用法： pwsh -File scripts/doctor.ps1
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Check($name, $ok, $hint) {
    if ($ok) { Write-Host ("[OK]   {0}" -f $name) -ForegroundColor Green }
    else { Write-Host ("[FAIL] {0}  -> {1}" -f $name, $hint) -ForegroundColor Yellow }
}

Write-Host "== Procurement AI Agent Workbench :: doctor ==" -ForegroundColor Cyan

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
Write-Check '.venv (Python 3.12)' (Test-Path $venvPython) '运行 scripts/dev.ps1 setup'

if (Test-Path $venvPython) {
    $pyVersion = & $venvPython -c "import sys;print(sys.version.split()[0])" 2>$null
    $depsOk = & $venvPython -c "import fastapi, sqlalchemy, pydantic, psycopg, pgvector; print('ok')" 2>$null
    Write-Check ("python deps ({0})" -f $pyVersion) ($depsOk -eq 'ok') 'backend/pyproject.toml 依赖未安装'
}

$nodeExe = 'C:\Users\91975\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
$nodeOnPath = Get-Command node -ErrorAction SilentlyContinue
$nodeFound = (Test-Path $nodeExe) -or ($null -ne $nodeOnPath)
Write-Check 'node' $nodeFound '安装 Node 24+ 并加入 PATH'

$pnpm = 'C:\Users\91975\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs'
Write-Check 'pnpm' (Test-Path $pnpm) '运行 scripts/dev.ps1 web-install'

$dockerCli = Get-Command docker -ErrorAction SilentlyContinue
Write-Check 'docker cli' ($null -ne $dockerCli) '安装 Docker Desktop'

$daemonOk = $false
if ($dockerCli) {
    & docker ps --format '{{.Names}}' *> $null
    $daemonOk = ($LASTEXITCODE -eq 0)
}
Write-Check 'docker daemon' $daemonOk '启动 Docker Desktop 后再重试'

$dbOk = $false
if (Test-Path $venvPython) {
    $dbOk = (& $venvPython -c "import socket;s=socket.socket();s.settimeout(1);print('ok' if s.connect_ex(('127.0.0.1',5432))==0 else 'down')" 2>$null) -eq 'ok'
}
Write-Check 'PostgreSQL 5432' $dbOk '运行 scripts/dev.ps1 up && scripts/dev.ps1 migrate'

Write-Host ""
if ($dbOk) { Write-Host '结论：可以跑集成测试与端到端演示。' -ForegroundColor Green }
else { Write-Host '结论：只能跑单元测试与前端壳（集成测试会自动 skip）。' -ForegroundColor Yellow }
