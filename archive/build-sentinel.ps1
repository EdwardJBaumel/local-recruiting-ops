# Build SENTINEL as a single-file Windows exe.
#
# Requires: Python 3.10+, Node 18+, npm, and pip on PATH. Run from
# AI_recruiter\. Output: dist\sentinel.exe
#
# First time (also when requirements.txt or package.json changes):
#   .\build.ps1
# Subsequent rebuilds (skip dep reinstall for speed):
#   .\build.ps1 -NoInstall

[CmdletBinding()]
param(
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"

# Resolve to the folder this script lives in so it works no matter where
# the user runs it from.
Set-Location -Path $PSScriptRoot

Write-Host "== SENTINEL build ==" -ForegroundColor Cyan

# 1. Python deps
if (-not $NoInstall) {
    Write-Host "`n[1/4] Installing Python deps..." -ForegroundColor Yellow
    # Use the same venv the dev launcher creates if it exists.
    $venvPython = if (Test-Path "venv\Scripts\python.exe") { "venv\Scripts\python.exe" }
                  elseif (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" }
                  else { "python" }
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r sentinel\requirements.txt
    & $venvPython -m pip install pyinstaller
    $script:Python = $venvPython
} else {
    $script:Python = if (Test-Path "venv\Scripts\python.exe") { "venv\Scripts\python.exe" }
                     elseif (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" }
                     else { "python" }
}

# 2. Frontend build
Write-Host "`n[2/4] Building React UI..." -ForegroundColor Yellow
Push-Location sentinel-ui
try {
    if (-not $NoInstall -or -not (Test-Path "node_modules")) {
        npm install
    }
    npm run build
} finally {
    Pop-Location
}

# 3. PyInstaller
Write-Host "`n[3/4] Packaging with PyInstaller..." -ForegroundColor Yellow
# Clean previous build artefacts so stale files don't sneak in.
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $script:Python -m PyInstaller sentinel.spec --clean --noconfirm

# 4. Summary
Write-Host "`n[4/4] Done." -ForegroundColor Green
$exe = Join-Path $PSScriptRoot "dist\sentinel.exe"
if (Test-Path $exe) {
    $size = (Get-Item $exe).Length / 1MB
    Write-Host ("  -> {0} ({1:N1} MB)" -f $exe, $size) -ForegroundColor Green
    Write-Host "`nNext: start Ollama (ollama serve), then double-click sentinel.exe."
} else {
    Write-Host "ERROR: build produced no exe. Check PyInstaller output above." -ForegroundColor Red
    exit 1
}
