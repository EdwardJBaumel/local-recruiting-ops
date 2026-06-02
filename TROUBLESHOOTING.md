# Local Recruiting Ops — Troubleshooting

If you hit one of these and the fix isn't here, check `lro/api/logs/sentinel.log` for the backend's view, the launcher window for the Python error, or your browser DevTools console for the frontend's view. Open an issue with all three and I'll add it.

---

## Install / launch issues

### Windows execution policy blocks `start.ps1`

**Symptom:** PowerShell refuses to run the script:

```
.\start.ps1 cannot be loaded because running scripts is disabled on this system.
```

**Fix (one-time):** double-click `Start Local Recruiting Ops.cmd` instead — it sets the policy for that single invocation. Alternatively, allow scripts globally:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### `python: command not found` (or wrong version)

**Symptom:** the launcher exits with `No 'python' on PATH` or installs against Python 2.x.

**Fix:** install Python 3.11+ from https://python.org/downloads. On Windows, tick "Add Python to PATH" during install. Verify with `python --version` (should report 3.11.x or higher).

### `npm: command not found`

**Symptom:** launcher exits with `npm not found. Install Node.js 18+ from https://nodejs.org`.

**Fix:** install Node.js LTS from https://nodejs.org. Verify with `node --version` (should report v18.x or higher).

### `pip install` hangs or fails on `sentence-transformers`

**Symptom:** the first-run pip install hangs at the sentence-transformers / torch download. Or fails with a "wheel" error.

**Why:** `sentence-transformers` pulls `torch`, which is ~2 GB and ships compiled wheels. On unusual platforms (ARM Linux, very old Windows) you may not have a matching prebuilt wheel.

**Fix:** open a separate terminal, activate the venv, and try installing torch alone first to see the real error:

```bash
# Windows
venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu

# macOS / Linux
source venv/bin/activate
pip install torch
```

If that succeeds, re-run the launcher.

### WeasyPrint warnings on Windows about `libgobject-2.0-0`

**Symptom:** when you try to export a tailored resume as PDF, the backend logs:

```
WeasyPrint could not import some external libraries.
OSError: cannot load library 'libgobject-2.0-0'
```

**Why:** WeasyPrint needs GTK system libraries that Windows doesn't ship with. The Python package installs but the native libraries are missing.

**Impact:** PDF resume export falls back to HTML output — you still get a usable resume file, just `.html` instead of `.pdf`. Open in Word / Pages / a browser and Save As PDF.

**Fix (only if you really need PDF on Windows):** install GTK from https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases — this is the maintained Windows GTK runtime. WeasyPrint will then find the libraries on next import. Mac/Linux don't have this issue (GTK is system-installed or trivially apt/brew-installable).

---

## Runtime issues

### Backend not reachable

**Symptom:** the dashboard shows a destructive-coloured banner above the navbar:

> ⚠ Backend not reachable on `localhost:8099`. Run `start.ps1` (or double-click `Start Local Recruiting Ops.cmd`) to bring up the Python API.

**Fix:** check the launcher window — the Python process probably crashed or never started. Common causes:

1. **Port 8099 already in use.** Another Local Recruiting Ops instance, or some other service. Kill it (Task Manager / `lsof -ti :8099 | xargs kill`) and re-run.
2. **Bad `config.json`.** A half-saved Settings change can leave it as invalid JSON. The Python launcher should catch this and tell you, but if it doesn't, delete `lro/api/config.json` and re-run — the launcher will reseed from `config.example.json`.
3. **Missing core dep.** Run `venv\Scripts\python -c "import server"` and read the import error.

### Ollama not running

**Symptom:** the **Settings → Models** section shows a destructive banner:

> ⚠ Ollama not reachable on http://127.0.0.1:11434

Or the pipeline fails at the parse stage with `ConnectionError`.

**Fix:**

```bash
# Start Ollama if it's installed but not running
ollama serve
```

Then click **retry** in the Models banner, or refresh the page.

If `ollama` isn't installed at all, get it from https://ollama.com/download.

### Pipeline fails partway: "Model 'X' not found"

**Symptom:** logs show `Ollama 404 for model 'qwen3:8b'` or fit-gap / parse stages skip with "model not pulled".

**Why:** the running Ollama server does not have that tag installed. Local Recruiting Ops checks `/api/tags`, not files in a folder you browsed in Explorer.

**Fix:** pull the missing model in the **same environment** the launcher uses:

```bash
ollama pull qwen3:8b
```

…or open **Settings → Models** and point each task at a model `ollama list` already shows.

Default config expects **`qwen3:8b`** (parse, analyse) and optionally **`qwen3:14b`** (digest, cover letter). See [SETUP → Ollama models](SETUP.md#3-ollama-models).

### "Missing qwen3:8b" on launch but I already pulled it

**Symptom:** `start.ps1` prints `Ollama is up but missing: qwen3:8b` and pulls again, even though you see a manifest under a custom `.ollama\models` path.

**Why:** two common causes:

1. **Different model directory** — you pulled into `OLLAMA_MODELS=A` but `ollama serve` (tray app or launcher) is serving from the default `%USERPROFILE%\.ollama\models`.
2. **Windows `-NoProfile`** — `Start Local Recruiting Ops.cmd` does not load your PowerShell profile, so a profile-only `OLLAMA_MODELS` is ignored unless it is a **User** env var in System Settings.

**Fix:**

```powershell
# What the running server sees:
ollama list
[Environment]::GetEnvironmentVariable('OLLAMA_MODELS','User')
```

Set **User** `OLLAMA_MODELS` to your `...\models` folder, quit Ollama completely, relaunch via `start.ps1`. After that, `ollama list` and the launcher should agree.

The pull you see may be **verify-only** (same SHA256 digests, no re-download) if blobs were already present in the directory that server uses.

### Settings save shows "Saved" but values revert on reload

**Symptom:** you change a value, click Save, see the green "Saved" indicator, but on next page load it's back to the old value.

**Cause:** stale build of the frontend that doesn't include the nested-shape config save fix. Or browser-cached old bundle.

**Fix:** in the dev server, hit Ctrl-F5 to hard-refresh and bust the cache. If that doesn't work, kill the launcher, run `npm run build` in `lro/ui/`, and re-launch.

### "Backend not reachable" loops endlessly with hundreds of console errors

**Symptom:** the dev server logs are flooded with:

```
[vite] http proxy error: /api/status
Error: connect ECONNREFUSED 127.0.0.1:8099
```

…repeated dozens of times per second.

**Cause:** you ran `npm run dev` directly instead of `start.ps1` / `start.sh`. The UI runs but the Python backend is missing. TanStack Query polls `/api/status` every 2s; each fail triggers retries, which compound across all the other queries that auto-fetch on mount.

**Fix:** stop the dev server (Ctrl-C in the npm window), then use the proper launcher (`start.ps1` / `start.sh`) which brings BOTH processes up together.

This is also caught by the heartbeat banner now (top of the navbar) so you don't have to scroll the dev console to know what's wrong.

---

## Empty / stale data

### Brief tab shows "0 postings ingested" even after a successful cycle

**Cause:** you're looking at a cached older market_intel entry, OR the backend's market_intel writer didn't record this cycle.

**Fix:** click **Run Pipeline** once more. Market intel is written at the end of each cycle. If still empty after two cycles, check `lro/api/data/market_intel.json` — if the file is there with cycle entries, the backend is writing but the UI isn't reading them; refresh the page (Ctrl-F5). If the file is empty / missing, check `lro/api/logs/sentinel.log` for `MarketIntel` errors.

### Matches tab is empty after a successful cycle

**Cause:** the freshness window in **Settings → Freshness windows** is too tight, OR the location filter is dropping everything.

**Fix:** read the chip row at the top of the Matches view. It tells you how many rows were dropped by each filter. If "Freshness filter dropped 200" jumps out, drop the relevant tier slider in Settings to 0 (no filter). If "Location filter dropped 200", check your pin radius and allowed-locations text list.

### Multiple Google rows highlight at once when I click one

**Cause:** legacy data — older Google rows in your registry have `url=null` because the HTML cleaner stripped the `<a href>` before the LLM saw it. Multiple null-URL rows shared the same selection key.

**Fix:** this is fixed in the current build (URL is extracted deterministically by the fetcher now). But existing rows from before the fix are stuck with null URLs. Delete `lro/api/data/match_registry.json` and re-run the pipeline — the new entries will have URLs.

---

## Privacy / sharing

### How do I make sure nothing personal goes to GitHub?

The `.gitignore` excludes:

- `lro/api/data/` — your match registry, decisions, resume, parsed profile, cover letter drafts, user.json
- `lro/api/logs/` — your activity log
- `lro/api/config.json` — your live config (could contain a Discord webhook, your tuned thresholds, your home pins)
- `venv/`, `node_modules/` — machine-specific install state

What IS committed: the sanitized `lro/api/config.example.json` template, the source code, the README, the LICENSE.

**To audit what would actually be pushed:**

```bash
git status --ignored          # shows excluded files for sanity
git diff --cached             # shows what's about to be committed (after `git add`)
```

### I committed a secret (Discord webhook, API key) by accident

**Don't just delete it and recommit.** Once a secret is in git history, removing it from the latest commit isn't enough — anyone who clones can `git log` and recover it.

1. **Revoke the secret immediately** at the source (Discord → Webhooks → Delete; OpenAI → Keys → Revoke; etc.)
2. Generate a new secret and add it via env var, NOT the config file.
3. Optionally: rewrite history with [git-filter-repo](https://github.com/newren/git-filter-repo) to remove the secret from old commits, then `git push --force`. Note that GitHub may still cache the secret in old PR diffs / search index for some time — assume the old secret is permanently compromised.

### How do I reset my local install to a totally clean state?

```bash
# From the repo root:
rm -rf venv node_modules lro/ui/node_modules
rm -rf lro/api/data lro/api/logs
rm lro/api/config.json
./start.ps1   # or ./start.sh
```

The launcher rebuilds everything from scratch.

---

## Last resort

If none of the above helps, capture three things and open an issue:

1. The launcher window output (full text from start to the failure).
2. The contents of `lro/api/logs/sentinel.log` (last ~200 lines).
3. The browser DevTools console (Network + Console tabs, screenshot or text).

Local Recruiting Ops is a one-developer project and the surface is small enough that most issues either get caught by the bootstrap, the heartbeat banner, or the model picker's reachability check. Real bugs are rarer than configuration drift.
