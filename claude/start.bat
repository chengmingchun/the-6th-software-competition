@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Claude bot Windows launcher.
rem Usage with args:
rem   start.bat <playerId> <host> <port>
rem Double click without args:
rem   interactive menu for local connect / dual connect / tests.

cd /d "%~dp0"

if not exist main.py (
  echo [ERROR] main.py not found. Please run this script from the claude directory.
  pause
  exit /b 1
)
if not exist lizhi_agent (
  echo [ERROR] lizhi_agent directory not found. Claude bot package is incomplete.
  pause
  exit /b 1
)
if not exist logs mkdir logs >nul 2>nul

set "DEFAULT_PLAYER_ID=2779"
set "DEFAULT_RED_PLAYER_ID=2765"
set "DEFAULT_BLUE_PLAYER_ID=2779"
set "DEFAULT_HOST=127.0.0.1"
set "DEFAULT_PORT=30000"
set "DEFAULT_PLAYER_NAME=你荔枝一点"

rem Compact logs by default. Set these before running if you need full protocol dumps.
set "LIZHI_DEBUG=1"
set "LIZHI_LOG_MODE=brief"
set "LIZHI_LOG_STYLE=pretty"
set "LIZHI_RAW_LOG=0"
set "LIZHI_FIXTURE_LOG=0"
set "LIZHI_FILE_LOG=1"
set "LIZHI_LOG_DIR=%~dp0logs"
if not defined LIZHI_PLAYER_NAME set "LIZHI_PLAYER_NAME=%DEFAULT_PLAYER_NAME%"
if not defined LIZHI_VERSION set "LIZHI_VERSION=claude-local"

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  set "PYTHON_CMD=python"
) else (
  where py >nul 2>nul
  if %ERRORLEVEL% EQU 0 (
    set "PYTHON_CMD=py -3"
  ) else (
    echo [ERROR] Cannot find python or py in PATH.
    pause
    exit /b 1
  )
)

if not "%~3"=="" goto RUN_WITH_ARGS

:MENU
cls
echo ============================================================
echo 一骑红尘：荔枝争运战 - Claude Windows 启动器
echo ============================================================
echo 当前目录: %CD%
echo Python: %PYTHON_CMD%
echo 默认队名: %LIZHI_PLAYER_NAME%
echo 默认本地服务端: %DEFAULT_HOST%:%DEFAULT_PORT%
echo 日志: 精简事件流 + logs\^<playerId^>.log
echo.
echo 1. 启动单个本地客户端 ^(%DEFAULT_PLAYER_ID% / %DEFAULT_HOST% / %DEFAULT_PORT%^)
echo 2. 同时启动两个本地客户端 ^(RED=%DEFAULT_RED_PLAYER_ID%, BLUE=%DEFAULT_BLUE_PLAYER_ID%^)
echo 3. 手动输入 playerId/host/port 连接
echo 4. 跑 Claude 单元测试
echo 5. 快速导入检查 ^(import + Strategy init^)
echo 6. 打开日志目录
echo 7. 退出
echo.
set /p "CHOICE=请选择 [1-7]: "
if "%CHOICE%"=="1" goto DEFAULT_LOCAL
if "%CHOICE%"=="2" goto DUAL_LOCAL
if "%CHOICE%"=="3" goto PROMPT_REMOTE
if "%CHOICE%"=="4" goto UNIT_TEST
if "%CHOICE%"=="5" goto SMOKE_TEST
if "%CHOICE%"=="6" goto OPEN_LOGS
if "%CHOICE%"=="7" exit /b 0
goto MENU

:PROMPT_REMOTE
echo.
set /p "PLAYER_ID=playerId [%DEFAULT_PLAYER_ID%]: "
if "%PLAYER_ID%"=="" set "PLAYER_ID=%DEFAULT_PLAYER_ID%"
set /p "HOST=host [%DEFAULT_HOST%]: "
if "%HOST%"=="" set "HOST=%DEFAULT_HOST%"
set /p "PORT=port [%DEFAULT_PORT%]: "
if "%PORT%"=="" set "PORT=%DEFAULT_PORT%"
goto RUN_REMOTE

:DEFAULT_LOCAL
set "PLAYER_ID=%DEFAULT_PLAYER_ID%"
set "HOST=%DEFAULT_HOST%"
set "PORT=%DEFAULT_PORT%"
goto RUN_REMOTE

:DUAL_LOCAL
if exist start_local_dual.bat (
  call start_local_dual.bat %DEFAULT_RED_PLAYER_ID% %DEFAULT_BLUE_PLAYER_ID% %DEFAULT_HOST% %DEFAULT_PORT%
) else (
  echo [ERROR] Missing start_local_dual.bat
  pause
)
goto MENU

:RUN_WITH_ARGS
set "PLAYER_ID=%~1"
set "HOST=%~2"
set "PORT=%~3"
goto RUN_REMOTE_NO_PAUSE

:RUN_REMOTE
echo.
echo [INFO] Connecting: playerId=%PLAYER_ID% host=%HOST% port=%PORT%
echo [INFO] Team=%LIZHI_PLAYER_NAME% Version=%LIZHI_VERSION%
echo [INFO] Log mode=%LIZHI_LOG_MODE% raw=%LIZHI_RAW_LOG% fixture=%LIZHI_FIXTURE_LOG%
echo [INFO] Make sure the local server is already listening.
echo ------------------------------------------------------------
%PYTHON_CMD% main.py "%PLAYER_ID%" "%HOST%" "%PORT%"
set "EXIT_CODE=%ERRORLEVEL%"
echo ------------------------------------------------------------
echo [INFO] Client exited with code %EXIT_CODE%.
echo [INFO] Log file: logs\%PLAYER_ID%.log
pause
exit /b %EXIT_CODE%

:RUN_REMOTE_NO_PAUSE
%PYTHON_CMD% main.py "%PLAYER_ID%" "%HOST%" "%PORT%"
exit /b %ERRORLEVEL%

:UNIT_TEST
echo.
echo [INFO] Running Claude tests...
echo ------------------------------------------------------------
%PYTHON_CMD% -m unittest discover -s tests -v
set "EXIT_CODE=%ERRORLEVEL%"
echo ------------------------------------------------------------
echo [INFO] Tests exited with code %EXIT_CODE%.
pause
goto MENU

:SMOKE_TEST
echo.
echo [INFO] Checking imports and strategy construction...
echo ------------------------------------------------------------
%PYTHON_CMD% -c "from lizhi_agent.config import StrategyConfig; from lizhi_agent.logger import DecisionLogger; from lizhi_agent.strategy import BaselineStrategy; s=BaselineStrategy('2779', StrategyConfig.default(), DecisionLogger('smoke')); print('OK', type(s).__name__)"
set "EXIT_CODE=%ERRORLEVEL%"
echo ------------------------------------------------------------
echo [INFO] Smoke test exited with code %EXIT_CODE%.
pause
goto MENU

:OPEN_LOGS
start "" "%~dp0logs"
goto MENU
