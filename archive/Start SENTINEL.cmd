@echo off
REM =====================================================================
REM  SENTINEL - double-click launcher.
REM
REM  Launch precedence (first match wins):
REM    1. If a local venv exists (venv\Scripts\python.exe) AND has
REM       sentence-transformers installed, hand off to start.ps1. That
REM       path gives the fast embedding matcher, live UI from Vite, and
REM       matches what developers run day-to-day.
REM    2. Otherwise, launch the packaged dist\sentinel.exe (slower LLM
REM       fallback matching; no live UI rebuilds). Build it first if
REM       missing.
REM
REM  Make sure Ollama is installed and running (ollama serve) with
REM  gemma4:e4b, gemma4:26b, and qwen3:8b pulled.
REM =====================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV_PY=venv\Scripts\python.exe"
set "EXE=dist\sentinel.exe"

REM --- 1. Prefer the dev launcher when a ready venv is available ------
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sentence_transformers" >nul 2>&1
    if !errorlevel! EQU 0 (
        echo.
        echo Detected local venv with sentence-transformers. Using dev launcher
        echo for live UI and embedding matching.
        echo.
        powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
        exit /b !errorlevel!
    )
)

REM --- 2. Fall back to the packaged exe -------------------------------
if exist "%EXE%" goto :launch

echo.
echo ================================================================
echo  First-time setup: building SENTINEL.
echo  This runs once. Subsequent clicks launch straight away.
echo ================================================================
echo.

REM Run the PowerShell build script with execution policy bypassed so
REM users don't have to touch their system settings.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
if errorlevel 1 (
    echo.
    echo BUILD FAILED. Scroll up for the error.
    echo Common causes: Python or Node not on PATH, or no internet for npm/pip.
    echo.
    pause
    exit /b 1
)

if not exist "%EXE%" (
    echo BUILD FINISHED but %EXE% is missing. Something went wrong.
    pause
    exit /b 1
)

:launch
echo.
echo Launching SENTINEL (packaged build)... the Command Center will open in your browser.
echo Note: LLM matching fallback in use. For the faster embedding path, run start.ps1
echo which installs sentence-transformers into a local venv.
echo Close this window or press Ctrl+C to stop.
echo.
"%EXE%"

REM If the exe exited with an error, keep the window open so the user can read it.
if errorlevel 1 (
    echo.
    echo SENTINEL exited with an error. See the log above.
    pause
)
