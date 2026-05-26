@echo off
REM =====================================================================
REM  LANTERN - double-click launcher.
REM
REM  Hands off to start.ps1 which uses the local venv to run Lantern on
REM  one visible URL (:8099). Make sure Ollama
REM  is running (ollama serve) with qwen3:14b, qwen3:8b, gemma3:12b,
REM  and phi4-reasoning:14b pulled.
REM
REM  Previous-generation launcher (Sentinel) lives at
REM  archive\Start SENTINEL.cmd if you need it.
REM =====================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV_PY=venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo.
    echo ERROR: venv not found at %VENV_PY%.
    echo.
    echo The launcher expects a Python venv at the repo root with the
    echo Lantern backend deps installed. Create one with:
    echo.
    echo   python -m venv venv
    echo   venv\Scripts\python.exe -m pip install -r lantern\api\requirements.txt
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "RC=!errorlevel!"
if not "!RC!"=="0" (
    echo.
    echo Start LANTERN failed ^(exit !RC!^). See errors above.
    pause
    exit /b !RC!
)
exit /b 0
