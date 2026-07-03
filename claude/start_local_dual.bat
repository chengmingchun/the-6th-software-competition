@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Launch two local clients from the same current working tree against one local debug server.
rem Use this for same-version self play. For different versions, start each version's
rem single-client launcher manually from its own branch/worktree.
rem
rem Default local server: 127.0.0.1:30000
rem Default players are inferred from local-debug start message:
rem   RED  = 2765
rem   BLUE = 2779
rem
rem Usage:
rem   start_local_dual.bat [RED_PLAYER_ID] [BLUE_PLAYER_ID] [HOST] [PORT]

cd /d "%~dp0"

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
echo Launching same-version local dual clients
echo Server: %HOST%:%PORT%
echo Version: branch=%CURRENT_BRANCH% sha=%CURRENT_SHA%
echo RED:    id=%RED_PLAYER_ID%  name=%RED_PLAYER_NAME%
echo BLUE:   id=%BLUE_PLAYER_ID% name=%BLUE_PLAYER_NAME%
echo Logs:   %LOG_DIR%
echo ============================================================
echo.
echo Make sure the local debug server is already listening.
echo.

start "lizhi RED %RED_PLAYER_ID%" /D "%~dp0" cmd /k "set LIZHI_DEBUG=1&& set LIZHI_RAW_LOG=1&& set LIZHI_FILE_LOG=1&& set LIZHI_FIXTURE_LOG=1&& set LIZHI_LOG_DIR=%LOG_DIR%&& set LIZHI_VERSION=%CURRENT_BRANCH%-%CURRENT_SHA%-red&& set LIZHI_PLAYER_NAME=%RED_PLAYER_NAME%&& echo [RED] playerId=%RED_PLAYER_ID% playerName=%RED_PLAYER_NAME% host=%HOST% port=%PORT%&& %PYTHON_CMD% main.py %RED_PLAYER_ID% %HOST% %PORT%"
timeout /t 1 /nobreak >nul
start "lizhi BLUE %BLUE_PLAYER_ID%" /D "%~dp0" cmd /k "set LIZHI_DEBUG=1&& set LIZHI_RAW_LOG=1&& set LIZHI_FILE_LOG=1&& set LIZHI_FIXTURE_LOG=1&& set LIZHI_LOG_DIR=%LOG_DIR%&& set LIZHI_VERSION=%CURRENT_BRANCH%-%CURRENT_SHA%-blue&& set LIZHI_PLAYER_NAME=%BLUE_PLAYER_NAME%&& echo [BLUE] playerId=%BLUE_PLAYER_ID% playerName=%BLUE_PLAYER_NAME% host=%HOST% port=%PORT%&& %PYTHON_CMD% main.py %BLUE_PLAYER_ID% %HOST% %PORT%"

echo [INFO] Two same-version client windows launched.
echo [INFO] Expected server logs:
echo        Registered player ... playerId=%RED_PLAYER_ID% ... playerName=%RED_PLAYER_NAME%
echo        Registered player ... playerId=%BLUE_PLAYER_ID% ... playerName=%BLUE_PLAYER_NAME%
echo [INFO] If the server still stops at ready, check both client windows and both log files.
pause
