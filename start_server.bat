@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"

echo ============================================================
echo  Yi Qi Hong Chen - Local Competition Server
echo  IP: 127.0.0.1 : 30000
echo  Seed: random
echo  Press Ctrl+C to stop
echo ============================================================
echo.

python -m lizhi_server.run_server --port 30000

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Server failed to start. Check Python environment.
    pause
    exit /b %ERRORLEVEL%
)
