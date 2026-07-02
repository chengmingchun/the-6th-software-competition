@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Launch two local clients against one local debug server.
rem Default local server: 127.0.0.1:30000
rem Default players are inferred from local-debug start message:
rem   RED  = 2765
rem   BLUE = 2779
rem
rem Important:
rem   Local server may bind/deduplicate players by playerName as well as playerId.
rem   Therefore this launcher gives the two clients distinct playerName values.

cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=30000"
set "RED_PLAYER_ID=2765"
set "BLUE_PLAYER_ID=2779"

if not "%~1"=="" set "RED_PLAYER_ID=%~1"
if not "%~2"=="" set "BLUE_PLAYER_ID=%~2"
if not "%~3"=="" set "HOST=%~3"
if not "%~4"=="" set "PORT=%~4"

set "RED_PLAYER_NAME=lz-red-%RED_PLAYER_ID%"
set "BLUE_PLAYER_NAME=lz-blue-%BLUE_PLAYER_ID%"

if not exist logs mkdir logs >nul 2>nul

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

echo ============================================================
echo Launching two local clients
echo Server: %HOST%:%PORT%
echo RED:    id=%RED_PLAYER_ID%  name=%RED_PLAYER_NAME%
echo BLUE:   id=%BLUE_PLAYER_ID% name=%BLUE_PLAYER_NAME%
echo Logs:   logs\%RED_PLAYER_ID%.jsonl and logs\%BLUE_PLAYER_ID%.jsonl
echo ============================================================
echo.
echo Make sure the local debug server is already listening.
echo.

start "lizhi RED %RED_PLAYER_ID%" cmd /k "set LIZHI_DEBUG=1 && set LIZHI_RAW_LOG=1 && set LIZHI_FILE_LOG=1 && set LIZHI_VERSION=1.0 && set LIZHI_PLAYER_NAME=%RED_PLAYER_NAME% && echo [RED] playerId=%RED_PLAYER_ID% playerName=%RED_PLAYER_NAME% host=%HOST% port=%PORT% && %PYTHON_CMD% main.py %RED_PLAYER_ID% %HOST% %PORT%"
timeout /t 1 /nobreak >nul
start "lizhi BLUE %BLUE_PLAYER_ID%" cmd /k "set LIZHI_DEBUG=1 && set LIZHI_RAW_LOG=1 && set LIZHI_FILE_LOG=1 && set LIZHI_VERSION=1.0 && set LIZHI_PLAYER_NAME=%BLUE_PLAYER_NAME% && echo [BLUE] playerId=%BLUE_PLAYER_ID% playerName=%BLUE_PLAYER_NAME% host=%HOST% port=%PORT% && %PYTHON_CMD% main.py %BLUE_PLAYER_ID% %HOST% %PORT%"

echo [INFO] Two client windows launched.
echo [INFO] Expected server logs:
echo        Registered player ... playerId=%RED_PLAYER_ID% ... playerName=%RED_PLAYER_NAME%
echo        Registered player ... playerId=%BLUE_PLAYER_ID% ... playerName=%BLUE_PLAYER_NAME%
echo [INFO] If the server still stops at ready, check both client windows and both log files.
pause
