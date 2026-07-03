@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Launch two Claude clients against one local debug server.
rem Usage:
rem   start_local_dual.bat [RED_PLAYER_ID] [BLUE_PLAYER_ID] [HOST] [PORT]

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

set "HOST=127.0.0.1"
set "PORT=30000"
set "RED_PLAYER_ID=2765"
set "BLUE_PLAYER_ID=2779"
set "TEAM_NAME=你荔枝一点"
set "LOG_DIR=%~dp0logs"

if not "%~1"=="" set "RED_PLAYER_ID=%~1"
if not "%~2"=="" set "BLUE_PLAYER_ID=%~2"
if not "%~3"=="" set "HOST=%~3"
if not "%~4"=="" set "PORT=%~4"

set "RED_PLAYER_NAME=%TEAM_NAME%"
set "BLUE_PLAYER_NAME=%TEAM_NAME%"

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

for /f "tokens=*" %%i in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "CURRENT_BRANCH=%%i"
for /f "tokens=*" %%i in ('git rev-parse --short HEAD 2^>nul') do set "CURRENT_SHA=%%i"
if "%CURRENT_BRANCH%"=="" set "CURRENT_BRANCH=unknown"
if "%CURRENT_SHA%"=="" set "CURRENT_SHA=unknown"

echo ============================================================
echo Launching Claude local dual clients
echo Server: %HOST%:%PORT%
echo Version: branch=%CURRENT_BRANCH% sha=%CURRENT_SHA%
echo RED:    id=%RED_PLAYER_ID%  name=%RED_PLAYER_NAME%
echo BLUE:   id=%BLUE_PLAYER_ID% name=%BLUE_PLAYER_NAME%
echo Logs:   %LOG_DIR%
echo ============================================================
echo.
echo Make sure the local debug server is already listening.
echo.

start "Claude RED %RED_PLAYER_ID%" /D "%~dp0" cmd /k "set LIZHI_DEBUG=1&& set LIZHI_LOG_MODE=brief&& set LIZHI_LOG_STYLE=pretty&& set LIZHI_RAW_LOG=0&& set LIZHI_FIXTURE_LOG=0&& set LIZHI_FILE_LOG=1&& set LIZHI_LOG_DIR=%LOG_DIR%&& set LIZHI_VERSION=%CURRENT_BRANCH%-%CURRENT_SHA%-claude-red&& set LIZHI_PLAYER_NAME=%RED_PLAYER_NAME%&& echo [RED] playerId=%RED_PLAYER_ID% playerName=%RED_PLAYER_NAME% host=%HOST% port=%PORT%&& %PYTHON_CMD% main.py %RED_PLAYER_ID% %HOST% %PORT%"
timeout /t 1 /nobreak >nul
start "Claude BLUE %BLUE_PLAYER_ID%" /D "%~dp0" cmd /k "set LIZHI_DEBUG=1&& set LIZHI_LOG_MODE=brief&& set LIZHI_LOG_STYLE=pretty&& set LIZHI_RAW_LOG=0&& set LIZHI_FIXTURE_LOG=0&& set LIZHI_FILE_LOG=1&& set LIZHI_LOG_DIR=%LOG_DIR%&& set LIZHI_VERSION=%CURRENT_BRANCH%-%CURRENT_SHA%-claude-blue&& set LIZHI_PLAYER_NAME=%BLUE_PLAYER_NAME%&& echo [BLUE] playerId=%BLUE_PLAYER_ID% playerName=%BLUE_PLAYER_NAME% host=%HOST% port=%PORT%&& %PYTHON_CMD% main.py %BLUE_PLAYER_ID% %HOST% %PORT%"

echo [INFO] Two Claude client windows launched.
echo [INFO] If the server still stops at ready, check both client windows and logs.
pause
