# SENTINEL dev launcher (Windows / PowerShell)
# Starts the Python pipeline + API on :8099 and the Vite dashboard on :3000.
# Press Ctrl+C in this window to stop both.
#
# Usage:
#   pwsh -ExecutionPolicy Bypass -File .\start.ps1
#   ...or, if execution policy is already permissive:
#   .\start.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- Resolve Python (prefer the local venv) --------------------------
$python = $null
foreach ($candidate in @('venv\Scripts\python.exe', '.venv\Scripts\python.exe')) {
    $p = Join-Path $root $candidate
    if (Test-Path $p) { $python = $p; break }
}
# --- Auto-create venv if none exists so first-run users aren't stuck
# without sentence-transformers and friends. Only runs when the user
# hasn't already provisioned their own venv/.venv.
if (-not $python) {
    $systemPython = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $systemPython) { $systemPython = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
    if (-not $systemPython) {
        Write-Host "ERROR: Python not found. Install Python 3.11+ from https://python.org" -ForegroundColor Red
        exit 1
    }
    $venvPath = Join-Path $root 'venv'
    Write-Host "Creating Python venv at $venvPath (one-time)..." -ForegroundColor Cyan
    & $systemPython -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: failed to create venv. Try running 'python -m venv venv' manually." -ForegroundColor Red
        exit 1
    }
    $python = Join-Path $venvPath 'Scripts\python.exe'
}

# --- Install Python requirements when they're missing or stale -------
# We key the marker on requirements.txt's mtime so edits force a refresh.
$reqsFile = Join-Path $root 'sentinel\requirements.txt'
if (-not (Test-Path $reqsFile)) {
    Write-Host "WARNING: $reqsFile not found; skipping dependency install." -ForegroundColor Yellow
} else {
    $marker = Join-Path (Split-Path $python) '..\..\.deps-installed'
    $reqMtime = (Get-Item $reqsFile).LastWriteTimeUtc
    $needInstall = $true
    if (Test-Path $marker) {
        $markerMtime = (Get-Item $marker).LastWriteTimeUtc
        if ($markerMtime -ge $reqMtime) { $needInstall = $false }
    }
    if ($needInstall) {
        Write-Host "Installing Python dependencies from requirements.txt (this may take a minute)..." -ForegroundColor Cyan
        & $python -m pip install --upgrade pip --quiet
        & $python -m pip install -r $reqsFile
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: pip install failed. Check your network or requirements.txt." -ForegroundColor Red
            exit 1
        }
        New-Item -ItemType File -Path $marker -Force | Out-Null
    }
}

# --- Resolve Node ----------------------------------------------------
$npm = (Get-Command npm -ErrorAction SilentlyContinue).Source
if (-not $npm) {
    Write-Host "ERROR: npm not found. Install Node.js 18+ from https://nodejs.org" -ForegroundColor Red
    exit 1
}

# --- Pick the canonical UI folder (the outer one is the live one) ----
$uiDir = Join-Path $root 'sentinel-ui'
if (-not (Test-Path (Join-Path $uiDir 'package.json'))) {
    Write-Host "ERROR: $uiDir\package.json not found." -ForegroundColor Red
    exit 1
}

# --- Install UI deps once if needed ----------------------------------
if (-not (Test-Path (Join-Path $uiDir 'node_modules'))) {
    Write-Host "Installing dashboard dependencies (one-time)..." -ForegroundColor Cyan
    Push-Location $uiDir
    try { & $npm install } finally { Pop-Location }
}

Write-Host ""
Write-Host "  SENTINEL launcher" -ForegroundColor Cyan
Write-Host "  - Python : $python"
Write-Host "  - UI dir : $uiDir"
Write-Host "  - Backend: http://127.0.0.1:8099"
Write-Host "  - Dash   : http://127.0.0.1:3000"
Write-Host ""

# --- Start Ollama if installed but not running ----------------------
# The pipeline LLM calls all go through Ollama on :11434. If the user
# has Ollama installed (common case) but forgot to start it, we spawn
# `ollama serve` as a child process so a fresh launch gives a working
# LLM stack without a separate terminal. We only spawn if :11434 isn't
# already bound, to avoid fighting an existing Ollama instance. PID is
# tracked so Ctrl+C cleans it up (only if WE started it).
function Test-Port11434 {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect('127.0.0.1', 11434, $null, $null)
        $ok  = $iar.AsyncWaitHandle.WaitOne(300)
        if ($ok) { $c.EndConnect($iar); $c.Close(); return $true }
        $c.Close()
    } catch {}
    return $false
}
$ollama = $null
if (Test-Port11434) {
    Write-Host "Ollama already running on :11434" -ForegroundColor DarkGray
} else {
    $ollamaExe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if ($ollamaExe) {
        Write-Host "Starting Ollama (ollama serve)..." -ForegroundColor Cyan
        # WindowStyle Hidden keeps the console out of the user's face;
        # output still flows to the launcher's child-process tree.
        $ollama = Start-Process -FilePath $ollamaExe `
            -ArgumentList 'serve' `
            -WindowStyle Hidden `
            -PassThru
    } else {
        Write-Host "Ollama not found on PATH. Install from https://ollama.com/download - the pipeline will skip LLM steps until it's running." -ForegroundColor Yellow
    }
}

# --- Spawn both as child processes so Ctrl+C kills both --------------
# Set SENTINEL_NO_BROWSER=1 for the backend so it does NOT open
# http://127.0.0.1:8099 automatically - that URL serves the built dist/
# bundle, which can lag behind source. In the dev launcher path we want
# the live Vite URL (:3000) instead, opened by us below once Vite is up.
$env:SENTINEL_NO_BROWSER = '1'
# Dev launcher is browser-first, pipeline-manual: the UI opens,
# user hits Run Pipeline when ready. Override with $env:SENTINEL_MANUAL_MODE='0'
# before calling this script if you want the scheduled interval loop.
if (-not $env:SENTINEL_MANUAL_MODE) { $env:SENTINEL_MANUAL_MODE = '1' }
$backend = Start-Process -FilePath $python `
    -ArgumentList 'main.py' `
    -WorkingDirectory (Join-Path $root 'sentinel') `
    -PassThru -NoNewWindow

# npm on Windows is usually npm.cmd (a batch wrapper). Start-Process with
# -NoNewWindow refuses to execute .cmd/.bat files directly ("%1 is not a
# valid Win32 application"), so we go through cmd.exe. /c ensures the
# helper exits when npm does, so our process handle tracks npm correctly.
$npmArgs = @('/c', 'npm', 'run', 'dev')
$frontend = Start-Process -FilePath "$env:ComSpec" `
    -ArgumentList $npmArgs `
    -WorkingDirectory $uiDir `
    -PassThru -NoNewWindow

# --- Helper: is Vite listening on :3000 yet? -------------------------
# Test-NetConnection runs a slow ping first, so we use TcpClient with a
# short async wait. Returns $true if the socket accepted, $false otherwise.
function Test-Port3000 {
    # Probe both IPv4 and IPv6 loopback. Vite historically defaulted to
    # IPv6-only on Node 17+, and although we now force host='127.0.0.1'
    # in vite.config.js, this fallback keeps the launcher working if the
    # user's Vite config drifts or a newer Vite changes defaults again.
    foreach ($probe in @('127.0.0.1', '::1')) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $iar = $c.BeginConnect($probe, 3000, $null, $null)
            $ok  = $iar.AsyncWaitHandle.WaitOne(300)
            if ($ok) {
                $c.EndConnect($iar)
                $c.Close()
                return $true
            }
            $c.Close()
        } catch { }
    }
    return $false
}

# Cache-bust the URL so the browser treats it as a new URL and opens a
# fresh tab every launch, even if the user already has one or two
# SENTINEL tabs open. Without this, most browsers focus an existing tab
# at http://127.0.0.1:3000 rather than opening a new one, and the user
# gets a silent "nothing happened" experience. The query param is
# ignored by Vite and the SPA router.
$dashOpened   = $false
$dashTries    = 0
$dashMaxTries = 5
# ToUnixTimeSeconds gives an int64 directly - avoids Get-Date -UFormat %s
# which can return a locale-formatted decimal on non-English machines and
# then blow up [double]::Parse under a comma-decimal locale.
$dashStamp    = [DateTimeOffset]::Now.ToUnixTimeSeconds()
# Use URL hash (#launch=...) instead of query param (?s=...). Browsers
# treat a hash-only change on an already-open URL as a same-document
# navigation - they focus the existing tab and fire 'hashchange' in the
# SPA rather than opening a duplicate. Query params reload the page in
# most browsers, which is why users were seeing a fresh tab every launch.
$dashUrl      = "http://127.0.0.1:3000/#launch=$dashStamp"

# --- Ctrl+C handler --------------------------------------------------
$cleanup = {
    Write-Host ""
    Write-Host "Stopping SENTINEL..." -ForegroundColor Yellow
    # Include $ollama only if WE started it (existing external instance
    # stays up, which matches user expectations).
    foreach ($p in @($backend, $frontend, $ollama)) {
        if ($p -and -not $p.HasExited) {
            # taskkill /T walks the child process tree. npm.cmd spawns node
            # as a grandchild; Stop-Process on the npm PID would orphan it.
            try { & taskkill /T /F /PID $p.Id 2>$null | Out-Null } catch {}
        }
    }
}
[Console]::TreatControlCAsInput = $false
Register-EngineEvent PowerShell.Exiting -Action $cleanup | Out-Null

try {
    while ($true) {
        if ($backend.HasExited)  { Write-Host "Backend exited (code $($backend.ExitCode))." -ForegroundColor Red; break }
        if ($frontend.HasExited) { Write-Host "Frontend exited (code $($frontend.ExitCode))." -ForegroundColor Red; break }

        # Open the dashboard as soon as Vite accepts a TCP connection.
        # We do this inline (not as a background job) so it's visible in
        # the launcher output and can't silently fail.
        #
        # The cache-busting query param on $dashUrl guarantees a fresh
        # tab every launch (see $dashUrl construction above). We only
        # flip $dashOpened to $true *after* Start-Process succeeds, so a
        # transient opener failure (UAC, locked profile, default-browser
        # glitch) retries on the next loop iteration rather than going
        # silent for the rest of the session.
        if (-not $dashOpened -and (Test-Port3000)) {
            $dashTries += 1
            Write-Host "Opening dashboard at $dashUrl" -ForegroundColor Green
            try {
                Start-Process $dashUrl | Out-Null
                $dashOpened = $true
            } catch {
                if ($dashTries -ge $dashMaxTries) {
                    Write-Host "Could not auto-open browser after $dashTries tries. Visit $dashUrl manually." -ForegroundColor Yellow
                    # Give up so we don't log-spam, but don't kill the cycle.
                    $dashOpened = $true
                } else {
                    Write-Host "Open failed (attempt $dashTries/$dashMaxTries). Retrying in 2s." -ForegroundColor Yellow
                    Start-Sleep -Seconds 2
                }
            }
        }

        Start-Sleep -Seconds 1
    }
} finally {
    & $cleanup
}
