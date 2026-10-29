@echo off
chcp 65001 >nul
REM ============================================
REM  Co-Sight 后端一键启动/重启脚本
REM  用法：双击运行，或 cmd 里执行 start_backend.bat
REM  说明：必须在 Co-Sight 目录下运行（脚本已自动 cd 到这里）
REM ============================================
cd /d "%~dp0"

echo [1/3] 检查端口 7790 是否被占用...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7790" ^| findstr "LISTENING"') do (
    echo   端口7790已被占用，请手动确认旧服务，不自动结束进程。
    goto end
)

echo [2/3] 启动后端（约 1-2 分钟就绪）...
REM Local launcher only; production must use HTTPS and Secure cookies.
set COSIGHT_HOST=127.0.0.1
set COSIGHT_COOKIE_SECURE=false
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_backend.ps1"
goto end

echo [3/3] 等待就绪...
set /a tries=0
:waitloop
set /a tries+=1
powershell -Command "try{(Invoke-WebRequest -UseBasicParsing -Uri http://localhost:7790/cosight/ -TimeoutSec 2 | Out-Null); exit 0}catch{exit 1}" >nul 2>&1
if %errorlevel%==0 goto ready
if %tries% geq 30 goto timeout
timeout /t 5 /nobreak >nul
goto waitloop

:ready
echo.
echo ============================================
echo   ✅ 后端已就绪！
echo   页面：http://localhost:7790/cosight/
echo   API ：http://localhost:7790/api/nae-deep-research/v1/deep-research/search
echo ============================================
start http://localhost:7790/cosight/
goto end

:timeout
echo.
echo ⚠️ 等待超时（90秒）。请手动打开 http://localhost:7790/cosight/ 检查。
:end
pause
