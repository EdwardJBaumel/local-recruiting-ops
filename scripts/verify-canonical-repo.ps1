# Fail fast if Lantern is not opened/run from the git repo root.
# Prevents editing a stale partial copy (e.g. C:\Users\zonka\AI_recruiter).

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot | Split-Path -Parent
$issues = @()

if (-not (Test-Path (Join-Path $root '.git'))) {
    $issues += 'No .git at repo root. This folder is not the canonical Lantern clone.'
}

$serverPy = Join-Path $root 'lantern\api\server.py'
if (-not (Test-Path $serverPy)) {
    $issues += "Missing $serverPy. Partial or stale tree; use dev\projects\lantern."
}

$canonical = 'C:\Users\zonka\dev\projects\lantern'
$resolvedRoot = (Resolve-Path $root).Path
$resolvedCanonical = (Resolve-Path $canonical -ErrorAction SilentlyContinue).Path
if ($resolvedCanonical -and ($resolvedRoot -ne $resolvedCanonical)) {
    $issues += "Repo root is $resolvedRoot but canonical clone is $resolvedCanonical."
}

if ($resolvedRoot -match '\\AI_recruiter(\\|$)') {
    $issues += 'AI_recruiter is a stale partial copy. Open dev\projects\lantern in Cursor instead.'
}

if ($issues.Count -gt 0) {
    Write-Host ''
    Write-Host 'LANTERN REPO CHECK FAILED' -ForegroundColor Red
    foreach ($i in $issues) { Write-Host "  - $i" -ForegroundColor Yellow }
    Write-Host ''
    Write-Host 'Canonical repo: C:\Users\zonka\dev\projects\lantern' -ForegroundColor Cyan
    Write-Host 'See dev\AGENTS.md and AGENTS.md in that repo.' -ForegroundColor Cyan
    exit 1
}

Write-Host "Lantern repo OK: $resolvedRoot" -ForegroundColor DarkGray
exit 0
