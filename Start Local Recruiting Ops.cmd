@echo off
REM =====================================================================
REM  LRO - double-click launcher.
REM
REM  Hands off to start.ps1 which uses the local venv to run Local Recruiting Ops on
REM  one visible URL (:8099). Make sure Ollama
REM  is running (ollama serve). Pull at least qwen3:8b; see README for
REM  per-task picks (benchmark: scripts\benchmark_models.py).
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
    echo Local Recruiting Ops backend deps installed. Create one with:
    echo.
    echo   python -m venv venv
    echo   venv\Scripts\python.exe -m pip install -r lro\api\requirements.txt
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "RC=!errorlevel!"
if not "!RC!"=="0" (
    echo.
    echo Start LRO failed ^(exit !RC!^). See errors above.
    pause
    exit /b !RC!
)
exit /b 0
