# ============================================
#  Co-Sight 后端一键启动/重启脚本 (PowerShell)
#  用法：powershell -ExecutionPolicy Bypass -File start_backend.ps1
#  说明：自动切换到 Co-Sight 目录，重启端口 7790
# ============================================
$ErrorActionPreference = "SilentlyContinue"
Set-Location -Path $PSScriptRoot

Write-Host "[1/3] 检查端口 7790..." -ForegroundColor Cyan
$conn = Get-NetTCPConnection -LocalPort 7790 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "端口7790已占用，请确认并手动停止旧服务，避免误停其他程序。" -ForegroundColor Yellow
    exit 1
}

Write-Host "[2/3] 启动后端（约 1-2 分钟就绪）..." -ForegroundColor Cyan
$pythonCommand = Get-Item -LiteralPath (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") -ErrorAction SilentlyContinue
if (-not $pythonCommand) { $pythonCommand = Get-Command python -ErrorAction SilentlyContinue }
if (-not $pythonCommand) { $pythonCommand = Get-Command py -ErrorAction SilentlyContinue }
if (-not $pythonCommand) {
    Write-Host "❌ 未找到 Python。请安装 Python 3.12 并确保 python 或 py 在 PATH 中。" -ForegroundColor Red
    exit 1
}
$pythonArgs = if ($pythonCommand.Name -eq "py.exe" -or $pythonCommand.Name -eq "py") {
    @("-3.12", "cloud_app.py", "-p", "7790")
} else {
    @("cloud_app.py", "-p", "7790")
}
if (-not $env:COSIGHT_COOKIE_SECURE) { $env:COSIGHT_COOKIE_SECURE = "false" } # This launcher binds loopback only.
if (-not $env:COSIGHT_HOST) { $env:COSIGHT_HOST = "127.0.0.1" }
if ($env:COSIGHT_HOST -ne "127.0.0.1") { throw "本地启动器仅用于127.0.0.1；公网部署请按docs/MULTI_USER_DEPLOYMENT.md配置HTTPS。" }
$pythonExecutable = if ($pythonCommand.FullName) { $pythonCommand.FullName } else { $pythonCommand.Source }
Start-Process $pythonExecutable -ArgumentList $pythonArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden

Write-Host "[3/3] 等待就绪..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 5
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:7790/cosight/" -TimeoutSec 2
        if ($resp.StatusCode) { $ready = $true; break }
    } catch { }
}
if ($ready) {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  ✅ 后端已就绪！" -ForegroundColor Green
    Write-Host "  页面：http://localhost:7790/cosight/" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
    Start-Process "http://localhost:7790/cosight/"
} else {
    Write-Host "⚠️ 等待超时（150秒），请手动打开 http://localhost:7790/cosight/ 检查。" -ForegroundColor Yellow
}
