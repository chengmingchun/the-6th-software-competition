@echo off
setlocal

rem Windows local launcher for development.
rem Usage:
rem   start.bat <playerId> <host> <port>
rem
rem The official Linux runner still uses start.sh. This file is only for
rem quickly reproducing connection and strategy logs on a Windows machine.

cd /d "%~dp0"

if "%~3"=="" (
  echo Usage: %~nx0 ^<playerId^> ^<host^> ^<port^> 1>&2
  exit /b 1
)

python main.py %1 %2 %3
exit /b %ERRORLEVEL%
