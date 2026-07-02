@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Launch two local clients against one local debug server.
rem MAIN uses the current working tree. BASELINE uses a git worktree checked out
rem from baseline-700-first-score, the first real-environment scoring version.
rem
rem Default local server: 127.0.0.1:30000
rem Default players are inferred from local-debug start message:
rem   MAIN     = 2765
rem   BASELINE = 2779
rem
rem Usage:
rem   start_local_dual.bat [MAIN_PLAYER_ID] [BASELINE_PLAYER_ID] [HOST] [PORT]

cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=30000"
set "MAIN_PLAYER_ID=2765"
set "BASE_PLAYER_ID=2779"
set "BASELINE_BRANCH=baseline-700-first-score"
set "BASELINE_DIR=%~dp0.worktrees\baseline-700-first-score"
set "LOG_DIR=%~dp0logs"

if not "%~1"=="" set "MAIN_PLAYER_ID=%~1"
if not "%~2"=="" set "BASE_PLAYER_ID=%~2"
if not "%~3"=="" set "HOST=%~3"
if not "%~4"=="" set "PORT=%~4"

set "MAIN_PLAYER_NAME=lz-main-%MAIN_PLAYER_ID%"
set "BASE_PLAYER_NAME=lz-baseline-%BASE_PLAYER_ID%"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul

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

where git >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
  echo [ERROR] Cannot find git in PATH. Baseline match needs git worktree.
  pause
  exit /b 1
)

git rev-parse --verify --quiet "%BASELINE_BRANCH%" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
  echo [ERROR] Cannot find local branch %BASELINE_BRANCH%.
  echo [ERROR] Create it first or pull it from remote.
  pause
  exit /b 1
)

if not exist "%~dp0.worktrees" mkdir "%~dp0.worktrees" >nul 2>nul
if not exist "%BASELINE_DIR%\.git" (
  echo [INFO] Creating baseline worktree at %BASELINE_DIR%
  git worktree add "%BASELINE_DIR%" "%BASELINE_BRANCH%"
  if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to create baseline worktree.
    pause
    exit /b 1
  )
)

for /f "tokens=*" %%i in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "MAIN_BRANCH=%%i"
for /f "tokens=*" %%i in ('git rev-parse --short HEAD 2^>nul') do set "MAIN_SHA=%%i"
for /f "tokens=*" %%i in ('git -C "%BASELINE_DIR%" rev-parse --short HEAD 2^>nul') do set "BASE_SHA=%%i"

echo ============================================================
echo Launching local MAIN vs BASELINE clients
echo Server:   %HOST%:%PORT%
echo MAIN:     id=%MAIN_PLAYER_ID%  name=%MAIN_PLAYER_NAME%  branch=%MAIN_BRANCH%  sha=%MAIN_SHA%
echo BASELINE: id=%BASE_PLAYER_ID%  name=%BASE_PLAYER_NAME%  branch=%BASELINE_BRANCH%  sha=%BASE_SHA%
echo Baseline worktree: %BASELINE_DIR%
echo Logs: %LOG_DIR%
echo ============================================================
echo.
echo Make sure the local debug server is already listening.
echo.

start "lizhi MAIN %MAIN_PLAYER_ID%" /D "%~dp0" cmd /k "set LIZHI_DEBUG=1&& set LIZHI_RAW_LOG=1&& set LIZHI_FILE_LOG=1&& set LIZHI_FIXTURE_LOG=1&& set LIZHI_LOG_DIR=%LOG_DIR%&& set LIZHI_VERSION=main-%MAIN_SHA%&& set LIZHI_PLAYER_NAME=%MAIN_PLAYER_NAME%&& echo [MAIN] playerId=%MAIN_PLAYER_ID% playerName=%MAIN_PLAYER_NAME% host=%HOST% port=%PORT%&& %PYTHON_CMD% main.py %MAIN_PLAYER_ID% %HOST% %PORT%"
timeout /t 1 /nobreak >nul
start "lizhi BASELINE %BASE_PLAYER_ID%" /D "%BASELINE_DIR%" cmd /k "set LIZHI_DEBUG=1&& set LIZHI_RAW_LOG=1&& set LIZHI_FILE_LOG=1&& set LIZHI_FIXTURE_LOG=1&& set LIZHI_LOG_DIR=%LOG_DIR%&& set LIZHI_VERSION=base-%BASE_SHA%&& set LIZHI_PLAYER_NAME=%BASE_PLAYER_NAME%&& echo [BASELINE] playerId=%BASE_PLAYER_ID% playerName=%BASE_PLAYER_NAME% host=%HOST% port=%PORT%&& %PYTHON_CMD% main.py %BASE_PLAYER_ID% %HOST% %PORT%"

echo [INFO] MAIN and BASELINE client windows launched.
echo [INFO] Expected server logs:
echo        Registered player ... playerId=%MAIN_PLAYER_ID% ... playerName=%MAIN_PLAYER_NAME%
echo        Registered player ... playerId=%BASE_PLAYER_ID% ... playerName=%BASE_PLAYER_NAME%
echo [INFO] If the server still stops at ready, check both client windows and both log files.
pause
