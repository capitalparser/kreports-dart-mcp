@echo off
setlocal

set "KREPORTS_ROOT=%~dp0.."
for %%I in ("%KREPORTS_ROOT%") do set "KREPORTS_ROOT=%%~fI"

if exist "%KREPORTS_ROOT%\.env" (
  for /f "usebackq tokens=1,* delims==" %%A in (`findstr /R "^[A-Za-z_][A-Za-z0-9_]*=" "%KREPORTS_ROOT%\.env" 2^>nul`) do (
    set "%%A=%%B"
  )
)

if "%KREPORTS_PYTHON%"=="" set "KREPORTS_PYTHON=python"
if "%PYTHONPATH%"=="" (
  set "PYTHONPATH=%KREPORTS_ROOT%"
) else (
  set "PYTHONPATH=%KREPORTS_ROOT%;%PYTHONPATH%"
)
set "DART_HEADLESS=1"
set "KREPORTS_HEADLESS=1"

cd /d "%KREPORTS_ROOT%"
"%KREPORTS_PYTHON%" -m kreports.mcp %*
