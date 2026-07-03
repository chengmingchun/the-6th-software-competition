@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"

echo ============================================================
echo  一骑红尘：荔枝争运战 - 本地模拟服务器
echo   IP: 127.0.0.1 : 30000
echo   Seed: 随机
echo   Ctrl+C 停止
echo ============================================================
echo.

python -m lizhi_server.run_server --port 30000

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] 服务器启动失败，请确认 Python 环境正确。
    pause
    exit /b %ERRORLEVEL%
)
