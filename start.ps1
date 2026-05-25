# Lantern dev launcher (Windows / PowerShell)
# Boots Lantern on one visible local URL by default: http://127.0.0.1:8099.
# Set LANTERN_DEV_UI=1 to also run the Vite dashboard on :3000 for UI work.
# Press Ctrl+C in this window to stop both.
#
# Usage:
#   pwsh -ExecutionPolicy Bypass -File .\start.ps1
#   ...or, if execution policy is already permissive:
#   .\start.ps1
#
# IMPORTANT: this file is plain ASCII. Windows PowerShell 5.1 reads .ps1
# files as the system codepage (not UTF-8) when there is no BOM, which
# means non-ASCII characters in the source -- em-dashes, arrows, smart
# quotes -- get corrupted and break the parser. If you edit this file,
# stick to ASCII or save with a UTF-8 BOM.
#
# The previous-generation Sentinel launcher lives at
# `archive/start-sentinel.ps1` if you ever need to compare behaviour.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- Paths -- point everything at lantern/, NOT sentinel/ -------------
$apiDir = Join-Path $root 'lantern\api'
$uiDir  = Join-Path $root 'lantern\ui'
if (-not (Test-Path $apiDir)) {
    Write-Host "ERROR: $apiDir not found. Did the rebuild finish?" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $uiDir 'package.json'))) {
    Write-Host "ERROR: $uiDir\package.json not found." -ForegroundColor Red
    exit 1
}
$devUi = (($env:LANTERN_DEV_UI -as [string]).Trim().ToLower() -in @('1', 'true', 'yes', 'on'))

# --- Resolve Python (prefer the local venv, create one on first run) ---
# venv lives at the repo root. First-run bootstrap: if no venv exists,
# create it with the system `python` and install the API requirements.
# Past behaviour was to fail with "venv not found" -- bad first-run UX
# for someone who just cloned the repo.
$python = $null
foreach ($candidate in @('venv\Scripts\python.exe', '.venv\Scripts\python.exe')) {
    $p = Join-Path $root $candidate
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) {
    Write-Host "No Python venv found -- bootstrapping one (first-run setup)..." -ForegroundColor Cyan
    $sysPython = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $sysPython) {
        $sysPython = (Get-Command python3 -ErrorAction SilentlyContinue).Source
    }
    if (-not $sysPython) {
        Write-Host "ERROR: No 'python' on PATH. Install Python 3.11+ from https://python.org and re-run." -ForegroundColor Red
        exit 1
    }
    $venvDir = Join-Path $root 'venv'
    & $sysPython -m venv $venvDir
    $python = Join-Path $venvDir 'Scripts\python.exe'
    if (-not (Test-Path $python)) {
        Write-Host "ERROR: venv creation failed at $venvDir." -ForegroundColor Red
        exit 1
    }
    Write-Host "Installing Python dependencies (this takes ~2 min the first time)..." -ForegroundColor Cyan
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r (Join-Path $apiDir 'requirements.txt')
}

# --- First-run config bootstrap ---------------------------------------
# config.json is gitignored (it can hold a Discord webhook + SMTP creds).
# On a fresh clone, copy the sanitized config.example.json into place
# so the backend has something to read. Subsequent runs leave the user's
# personal config alone -- pulling repo updates won't clobber it.
$liveConfig = Join-Path $apiDir 'config.json'
$exampleConfig = Join-Path $apiDir 'config.example.json'
if ((-not (Test-Path $liveConfig)) -and (Test-Path $exampleConfig)) {
    Write-Host "No config.json found -- seeding from config.example.json (first-run setup)..." -ForegroundColor Cyan
    Copy-Item $exampleConfig $liveConfig
    Write-Host "  -> Edit lantern\api\config.json to customise companies / models / preferences." -ForegroundColor DarkGray
    Write-Host "  -> Or use the dashboard's Settings tab once it is up." -ForegroundColor DarkGray
}

# --- Resolve Node ------------------------------------------------------
$npm = (Get-Command npm -ErrorAction SilentlyContinue).Source
if (-not $npm) {
    Write-Host "ERROR: npm not found. Install Node.js 18+ from https://nodejs.org" -ForegroundColor Red
    exit 1
}

# --- Install UI deps once if needed ------------------------------------
if (-not (Test-Path (Join-Path $uiDir 'node_modules'))) {
    Write-Host "Installing dashboard dependencies (one-time)..." -ForegroundColor Cyan
    Push-Location $uiDir
    try { & $npm install } finally { Pop-Location }
}
if (-not $devUi) {
    Write-Host "Building dashboard for single-port mode (:8099)..." -ForegroundColor Cyan
    Push-Location $uiDir
    try { & $npm run build } finally { Pop-Location }
}

Write-Host ""
Write-Host "  Lantern launcher" -ForegroundColor Cyan
Write-Host "  - Python : $python"
Write-Host "  - API dir: $apiDir"
Write-Host "  - UI dir : $uiDir"
Write-Host "  - App    : http://127.0.0.1:8099"
if ($devUi) {
    Write-Host "  - Dev UI : http://127.0.0.1:3000"
}
Write-Host ""

# --- Start Ollama if not running ---------------------------------------
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
        $ollama = Start-Process -FilePath $ollamaExe `
            -ArgumentList 'serve' `
            -WindowStyle Hidden `
            -PassThru
    } else {
        Write-Host "Ollama not found on PATH. Install from https://ollama.com/download." -ForegroundColor Yellow
    }
}

# --- GPU acceleration probe (CPU torch = 50-100x slower cycles) --------
# The match agent uses sentence-transformers + PyTorch to embed JDs.
# A plain `pip install torch` on Windows installs the CPU-only wheel,
# which makes a 1000-job match phase take ~80 min instead of ~1 min on
# a consumer NVIDIA card. We probe both sides — does the box have a
# GPU? Does this Python's torch see it? — and emit a yellow warning
# when there's a mismatch.
#
# Why a temp file instead of a here-string: PowerShell's here-string
# parser kept tripping over the Python `try:/except:` block (interpreting
# the colon-newline boundaries as PS syntax), producing parse-time
# "Missing closing '}'" errors that crashed the launcher before any
# output. Writing the snippet to a temp .py and running it sidesteps
# the entire here-string question.
$gpuProbeScript = Join-Path ([System.IO.Path]::GetTempPath()) ("lantern-gpu-probe-" + [Guid]::NewGuid().ToString('N') + ".py")
@'
import json, shutil
out = {"has_nvidia_smi": False, "cuda_available": False, "gpu": None, "torch_ver": None, "torch_cuda": None}
if shutil.which("nvidia-smi"):
    out["has_nvidia_smi"] = True
try:
    import torch
    out["torch_ver"] = torch.__version__
    out["torch_cuda"] = torch.version.cuda
    if torch.cuda.is_available():
        out["cuda_available"] = True
        out["gpu"] = torch.cuda.get_device_name(0)
except Exception:
    pass
print(json.dumps(out))
'@ | Set-Content -Path $gpuProbeScript -Encoding UTF8

$gpuProbe = & $python $gpuProbeScript 2>$null
try { Remove-Item -Path $gpuProbeScript -ErrorAction SilentlyContinue } catch {}

try {
    $probe = $gpuProbe | ConvertFrom-Json
    if ($probe.cuda_available) {
        Write-Host "GPU: $($probe.gpu) (torch $($probe.torch_ver), CUDA $($probe.torch_cuda))" -ForegroundColor Green
    } elseif ($probe.has_nvidia_smi) {
        Write-Host ""
        Write-Host ("=" * 72) -ForegroundColor Yellow
        Write-Host "  NVIDIA GPU detected but PyTorch is CPU-only." -ForegroundColor Yellow
        Write-Host "  Match phase will be 50-100x slower than necessary." -ForegroundColor Yellow
        Write-Host "" -ForegroundColor Yellow
        Write-Host "  Current torch: $($probe.torch_ver)  (no CUDA build)" -ForegroundColor Yellow
        Write-Host "" -ForegroundColor Yellow
        Write-Host "  To fix:" -ForegroundColor Yellow
        Write-Host "    & '$python' -m pip uninstall -y torch" -ForegroundColor Yellow
        Write-Host "    & '$python' -m pip install torch --index-url https://download.pytorch.org/whl/cu128" -ForegroundColor Yellow
        Write-Host "" -ForegroundColor Yellow
        Write-Host "  See SETUP.md -> 'GPU acceleration' for full instructions." -ForegroundColor Yellow
        Write-Host ("=" * 72) -ForegroundColor Yellow
        Write-Host ""
    } else {
        Write-Host "GPU: none detected (CPU mode - slower but functional)" -ForegroundColor DarkGray
    }
} catch {
    # Probe failed (torch missing, JSON malformed, etc.) - non-fatal.
    Write-Host "GPU probe skipped: torch not yet installed or probe errored." -ForegroundColor DarkGray
}

# --- Spawn backend (+ optional frontend dev server) --------------------
# LANTERN_NO_BROWSER=1 stops the backend from opening its own tab. The
# launcher opens the one canonical dashboard URL once the process is ready.
$env:LANTERN_NO_BROWSER = '1'
# Manual mode by default -- the orchestrator waits for /api/run-cycle
# instead of auto-firing every interval. Matches the v1 fix that landed
# in config.json: pipeline.auto_start=false.
if (-not $env:LANTERN_MANUAL_MODE) { $env:LANTERN_MANUAL_MODE = '1' }

$backend = Start-Process -FilePath $python `
    -ArgumentList 'main.py' `
    -WorkingDirectory $apiDir `
    -PassThru -NoNewWindow

$frontend = $null
if ($devUi) {
    # npm.cmd needs cmd.exe wrapper; Start-Process won't exec .cmd directly.
    $npmArgs = @('/c', 'npm', 'run', 'dev')
    $frontend = Start-Process -FilePath "$env:ComSpec" `
        -ArgumentList $npmArgs `
        -WorkingDirectory $uiDir `
        -PassThru -NoNewWindow
}

# --- Wait for app ready, then open browser -----------------------------
# Default: one visible localhost (:8099). Dev UI mode waits for both Vite
# (:3000) and the backend (:8099), then opens Vite so HMR works.
function Test-Port($port) {
    foreach ($probe in @('127.0.0.1', '::1')) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $iar = $c.BeginConnect($probe, $port, $null, $null)
            $ok  = $iar.AsyncWaitHandle.WaitOne(300)
            if ($ok) { $c.EndConnect($iar); $c.Close(); return $true }
            $c.Close()
        } catch { }
    }
    return $false
}

$dashOpened = $false
$dashStamp  = [DateTimeOffset]::Now.ToUnixTimeSeconds()
$dashPort   = if ($devUi) { 3000 } else { 8099 }
$dashUrl    = "http://127.0.0.1:$dashPort/#launch=$dashStamp"
$waitMsgShown = $false

# --- Ctrl+C cleanup ----------------------------------------------------
$cleanup = {
    Write-Host ""
    Write-Host "Stopping Lantern..." -ForegroundColor Yellow
    foreach ($p in @($backend, $frontend, $ollama)) {
        if ($p -and -not $p.HasExited) {
            try { & taskkill /T /F /PID $p.Id 2>$null | Out-Null } catch {}
        }
    }
}
[Console]::TreatControlCAsInput = $false
Register-EngineEvent PowerShell.Exiting -Action $cleanup | Out-Null

try {
    while ($true) {
        if ($backend.HasExited)  { Write-Host "Backend exited (code $($backend.ExitCode))." -ForegroundColor Red; break }
        if ($frontend -and $frontend.HasExited) { Write-Host "Frontend exited (code $($frontend.ExitCode))." -ForegroundColor Red; break }

        if (-not $dashOpened) {
            $viteReady = (-not $devUi) -or (Test-Port 3000)
            $apiReady  = Test-Port 8099
            if ($devUi -and $viteReady -and -not $apiReady -and -not $waitMsgShown) {
                Write-Host "Vite ready on :3000. Waiting for backend on :8099 (embedding model + Ollama prewarm)..." -ForegroundColor Cyan
                $waitMsgShown = $true
            } elseif ((-not $devUi) -and -not $apiReady -and -not $waitMsgShown) {
                Write-Host "Waiting for Lantern on :8099 (embedding model + Ollama prewarm)..." -ForegroundColor Cyan
                $waitMsgShown = $true
            }
            if ($viteReady -and $apiReady) {
                # Three browser-open attempts in order of reliability,
                # because Windows URL-launching is fragile:
                #
                #   1. [Process]::Start with UseShellExecute=true.
                #      Uses the same ShellExecute machinery as
                #      double-clicking a URL in Explorer. Most reliable
                #      when a default browser is correctly registered.
                #   2. `explorer.exe <url>`. Explorer dispatches URLs
                #      through its own resolver — often wins when
                #      ShellExecute trips on a corrupt registry entry.
                #   3. `cmd.exe /c start "" "<url>"`. The empty-string
                #      window-title arg is REQUIRED — without it, `start`
                #      interprets the URL itself as the title and opens
                #      a blank window. Common silent-failure mode on PS.
                #
                # If all three return without throwing AND no browser
                # actually appears, the user is told to click the URL.
                # We print it in bold green so it's easy to spot.
                $opened = $false

                # Attempt 1: ShellExecute via .NET
                try {
                    $psi = New-Object System.Diagnostics.ProcessStartInfo
                    $psi.FileName        = $dashUrl
                    $psi.UseShellExecute = $true
                    [System.Diagnostics.Process]::Start($psi) | Out-Null
                    $opened = $true
                } catch {
                    Write-Host "  (browser open attempt 1 failed: $($_.Exception.Message))" -ForegroundColor DarkGray
                }

                # Attempt 2: explorer.exe URL dispatch
                if (-not $opened) {
                    try {
                        & explorer.exe $dashUrl
                        $opened = $true
                    } catch {
                        Write-Host "  (browser open attempt 2 failed: $($_.Exception.Message))" -ForegroundColor DarkGray
                    }
                }

                # Attempt 3: cmd /c start with REQUIRED empty title arg
                if (-not $opened) {
                    try {
                        & cmd.exe /c "start """" ""$dashUrl"""
                        $opened = $true
                    } catch {
                        Write-Host "  (browser open attempt 3 failed: $($_.Exception.Message))" -ForegroundColor DarkGray
                    }
                }

                Write-Host ""
                Write-Host "Lantern is ready." -ForegroundColor Green
                Write-Host "  Dashboard:  $dashUrl" -ForegroundColor Green
                if ($devUi) {
                    Write-Host "  Backend:    http://127.0.0.1:8099" -ForegroundColor DarkGray
                }
                if (-not $opened) {
                    Write-Host ""
                    Write-Host "  Note: couldn't auto-open a browser. Click the dashboard URL above (Ctrl+Click in most terminals)." -ForegroundColor Yellow
                }
                Write-Host ""
                $dashOpened = $true
            }
        }

        Start-Sleep -Seconds 1
    }
} finally {
    & $cleanup
}
