@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"

echo ============================================================
echo 一骑红尘：荔枝争运战 - 本地模拟服务器
echo ============================================================

python -m lizhi_server.run_server --port 30000 --seed 42

pause
