@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Windows local launcher for development.
rem Official Linux runner still uses start.sh.
rem
rem Usage with args, same as start.sh:
rem   start.bat <playerId> <host> <port>
rem
rem Double click without args:
rem   interactive menu for remote connect / local fixture / unit tests.

cd /d "%~dp0"

if not exist logs mkdir logs >nul 2>nul

set "LIZHI_DEBUG=1"
set "LIZHI_RAW_LOG=1"
set "LIZHI_FILE_LOG=1"
if not defined LIZHI_PLAYER_NAME set "LIZHI_PLAYER_NAME=lizhi-python-baseline"
if not defined LIZHI_VERSION set "LIZHI_VERSION=1.0"

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
echo 一骑红尘：荔枝争运战 - Windows 本地调试启动器
echo ============================================================
echo 当前目录: %CD%
echo Python: %PYTHON_CMD%
echo 日志: stderr + logs\^<playerId^>.jsonl
echo.
echo 1. 连接远程比赛服务端 ^(手动输入 playerId/host/port^)
echo 2. 使用默认远程参数连接 ^(2779 / 7.225.86.238 / 6001^)
echo 3. 跑本地 fixture ^(不连服务端，只验证 start -^> ready -^> inquire -^> action^)
echo 4. 跑单元测试
echo 5. 退出
echo.
set /p "CHOICE=请选择 [1-5]: "
if "%CHOICE%"=="1" goto PROMPT_REMOTE
if "%CHOICE%"=="2" goto DEFAULT_REMOTE
if "%CHOICE%"=="3" goto LOCAL_FIXTURE
if "%CHOICE%"=="4" goto UNIT_TEST
if "%CHOICE%"=="5" exit /b 0
goto MENU

:PROMPT_REMOTE
echo.
set /p "PLAYER_ID=playerId [2779]: "
if "%PLAYER_ID%"=="" set "PLAYER_ID=2779"
set /p "HOST=host [7.225.86.238]: "
if "%HOST%"=="" set "HOST=7.225.86.238"
set /p "PORT=port [6001]: "
if "%PORT%"=="" set "PORT=6001"
goto RUN_REMOTE

:DEFAULT_REMOTE
set "PLAYER_ID=2779"
set "HOST=7.225.86.238"
set "PORT=6001"
goto RUN_REMOTE

:RUN_WITH_ARGS
set "PLAYER_ID=%~1"
set "HOST=%~2"
set "PORT=%~3"
goto RUN_REMOTE_NO_PAUSE

:RUN_REMOTE
echo.
echo [INFO] Connecting: playerId=%PLAYER_ID% host=%HOST% port=%PORT%
echo [INFO] LIZHI_PLAYER_NAME=%LIZHI_PLAYER_NAME% LIZHI_VERSION=%LIZHI_VERSION%
echo [INFO] Press Ctrl+C to stop.
echo ------------------------------------------------------------
%PYTHON_CMD% main.py "%PLAYER_ID%" "%HOST%" "%PORT%"
set "EXIT_CODE=%ERRORLEVEL%"
echo ------------------------------------------------------------
echo [INFO] Client exited with code %EXIT_CODE%.
echo [INFO] Log file: logs\%PLAYER_ID%.jsonl
pause
exit /b %EXIT_CODE%

:RUN_REMOTE_NO_PAUSE
%PYTHON_CMD% main.py "%PLAYER_ID%" "%HOST%" "%PORT%"
exit /b %ERRORLEVEL%

:LOCAL_FIXTURE
echo.
echo [INFO] Running local fixture: fixtures\minimal_start_inquire.jsonl
echo ------------------------------------------------------------
if not exist fixtures\minimal_start_inquire.jsonl (
  echo [ERROR] Missing fixtures\minimal_start_inquire.jsonl
  pause
  exit /b 1
)
%PYTHON_CMD% main.py 2779 < fixtures\minimal_start_inquire.jsonl
set "EXIT_CODE=%ERRORLEVEL%"
echo ------------------------------------------------------------
echo [INFO] Local fixture exited with code %EXIT_CODE%.
pause
goto MENU

:UNIT_TEST
echo.
echo [INFO] Running unit tests...
echo ------------------------------------------------------------
%PYTHON_CMD% -m unittest -v
set "EXIT_CODE=%ERRORLEVEL%"
echo ------------------------------------------------------------
echo [INFO] Unit tests exited with code %EXIT_CODE%.
pause
goto MENU
