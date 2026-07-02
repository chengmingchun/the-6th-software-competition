@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Launch two local clients against one local debug server.
rem Default local server: 127.0.0.1:30000
rem Default players are inferred from recent local-debug start message:
rem   RED  = 2765
rem   BLUE = 2779

cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=30000"
set "RED_PLAYER_ID=2765"
set "BLUE_PLAYER_ID=2779"

if not "%~1"=="" set "RED_PLAYER_ID=%~1"
if not "%~2"=="" set "BLUE_PLAYER_ID=%~2"
if not "%~3"=="" set "HOST=%~3"
if not "%~4"=="" set "PORT=%~4"

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

echo ============================================================
echo Launching two local clients
echo Server: %HOST%:%PORT%
echo RED:    %RED_PLAYER_ID%
echo BLUE:   %BLUE_PLAYER_ID%
echo Logs:   logs\%RED_PLAYER_ID%.jsonl and logs\%BLUE_PLAYER_ID%.jsonl
echo ============================================================
echo.
echo Make sure the local debug server is already listening.
echo.

start "lizhi RED %RED_PLAYER_ID%" cmd /k "%PYTHON_CMD% main.py %RED_PLAYER_ID% %HOST% %PORT%"
timeout /t 1 /nobreak >nul
start "lizhi BLUE %BLUE_PLAYER_ID%" cmd /k "%PYTHON_CMD% main.py %BLUE_PLAYER_ID% %HOST% %PORT%"

echo [INFO] Two client windows launched.
echo [INFO] If the server still stops at ready, check both windows and both log files.
pause
