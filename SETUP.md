# Lantern — Setup

A complete walk-through of installing Lantern on a clean machine. Each step ends with a "what success looks like" check so you can verify before moving on.

If something goes wrong, jump to [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## 1. Prerequisites

Install these once. Lantern won't run without them.

| Tool | Minimum | How to install | Verify |
|---|---|---|---|
| **Python** | 3.11+ | https://python.org/downloads | `python --version` |
| **Node.js** | 18+ | https://nodejs.org | `node --version` |
| **Ollama** | latest | https://ollama.com/download | `ollama --version` |
| **Git** | any recent | https://git-scm.com (Windows) / pre-installed elsewhere | `git --version` |

**Disk + memory budget:**
- ~25 GB free disk for the LLM model files (this is the big one)
- ~16 GB GPU VRAM is the comfortable target. Lantern will run on smaller GPUs if you swap to smaller models in **Settings → Models**.
- ~5 GB system RAM for the Python process + embeddings.

---

## 2. Pull the repo

```bash
git clone https://github.com/edwardjbaumel/lantern.git
cd lantern
```

**Success check:** `ls` shows `lantern/`, `archive/`, `start.ps1`, `start.sh`, `README.md`, `LICENSE`.

---

## 3. Pull Ollama models

Lantern uses two models in steady state — `qwen3:8b` (parse + always-resident default) and `qwen3:14b` (analyze + digest + cover-letter + match LLM-fallback). The launcher won't fetch them for you so you control disk usage.

### Recommended install (~14 GB total)

```bash
ollama pull qwen3:8b               # parse + default fallback (~5 GB)
ollama pull qwen3:14b              # analyze + digest + cover letter + match-LLM-fallback (~9 GB)
```

That's it. Two models, both fit comfortably on a 16 GB consumer NVIDIA GPU, Ollama keeps the most-recently-used one warm.

### Optional alternatives

If you have specific preferences, swap any task to a different model from the Settings → Models picker after launch:

```bash
# Slightly stronger conversational prose for digest / cover letters (warm tone)
ollama pull gemma3:12b              # ~8 GB

# Quality ceiling for cover letters if you have >18 GB VRAM
ollama pull qwen3:30b-a3b           # MoE — ~17 GB on disk, partial CPU spill on <18 GB cards (slower)
```

### Verify

```bash
ollama list
```

Should show every model you pulled with size + age. If `ollama list` errors, see [TROUBLESHOOTING → Ollama not running](TROUBLESHOOTING.md#ollama-not-running).

---

## 3a. GPU acceleration (CRITICAL — read this even if you "just" want CPU)

**The biggest performance lever in Lantern is whether PyTorch is using your GPU.** A 1,000-job match phase that takes ~80 minutes on CPU runs in under 1 minute on a consumer NVIDIA card (5070 Ti tested). The pipeline ships configured to use whatever PyTorch can see — but the default `pip install torch` on Windows installs the **CPU-only** wheel.

### Check what you have

After the launcher's first run (which creates the venv + installs requirements), the launcher console will print one of these lines:

```
GPU: NVIDIA GeForce RTX 5070 Ti (torch 2.x+cu128, CUDA 12.8)     ← good
GPU: none detected (CPU mode — slower but functional)             ← no GPU, CPU is fine
NVIDIA GPU detected but PyTorch is CPU-only.                      ← bad — fix below
```

The third case means you have a GPU but installed the wrong PyTorch wheel. Fix it.

### Install the CUDA build (Windows + NVIDIA GPU)

From the project root, with the venv activated:

```powershell
# 1. Drop the CPU build
.\venv\Scripts\python.exe -m pip uninstall -y torch torchvision torchaudio

# 2. Install the CUDA build matching your GPU's compute capability:
#    - sm_120 (Blackwell — 5000-series, RTX 5070+, 5090, Project DIGITS): use cu128
#    - sm_89 / sm_90 (Ada / Hopper — 4000-series, H100):                  use cu124 or cu128
#    - sm_86 / sm_80 (Ampere — 3000-series, A100):                        use cu121 or cu124
#    - sm_75 (Turing — 2000-series):                                       use cu121
# When in doubt, cu128 covers everything sm_75 and newer.
.\venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128

# 3. Verify
.\venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# Should print: True NVIDIA GeForce RTX <something>
```

If the install fails for your specific Python version (e.g. 3.13 / 3.14 wheels not yet on stable cu128), try the nightly index:

```powershell
.\venv\Scripts\python.exe -m pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu128
```

### Install the CUDA build (macOS / Linux)

- **macOS:** no CUDA support (Apple silicon uses MPS, which sentence-transformers handles automatically — you should see `Embedding model loaded on mps.` in the backend log if you're on M-series). No action needed.
- **Linux + NVIDIA:** same as Windows but with the Linux wheels:
  ```bash
  ./venv/bin/python -m pip uninstall -y torch
  ./venv/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
  ```

### Don't have an NVIDIA GPU

CPU mode is fully functional, just slower:

- 1,000-job match phase: ~80 min on CPU vs ~1 min on a consumer GPU
- Parse / analyze / digest / cover-letter LLM calls: bottlenecked on Ollama, which auto-detects GPU and falls back to CPU. ~5–15 s per call on CPU vs ~1–3 s on GPU.

If you want to run Lantern on CPU, no action needed — every stage works. Just expect cycles to take 30–90 minutes instead of 5–10.

### Why this isn't automatic

PyTorch's CUDA wheels are ~3 GB each and architecture-specific. Auto-installing them on every clone would: (a) bloat the bootstrap from 30 seconds to 5+ minutes, (b) pull CUDA libraries onto machines that don't need them, and (c) require us to ship per-GPU-arch logic. The launcher warning + this doc is the better trade.

---

## 4. Launch

The launcher does everything else automatically — venv creation, dependency install, config seeding, npm install, starting Ollama, opening the browser.

### Windows

```powershell
.\start.ps1
```

(Or double-click `Start LANTERN.cmd` if PowerShell execution policy is blocking the script — see [TROUBLESHOOTING → Windows execution policy](TROUBLESHOOTING.md#windows-execution-policy-blocks-startps1).)

### macOS / Linux

```bash
./start.sh
```

### What success looks like

The launcher prints something like:

```
No Python venv found — bootstrapping one (first-run setup)...
Installing Python dependencies (this takes ~2 min the first time)...
No config.json found — seeding from config.example.json (first-run setup)...
Installing dashboard dependencies (one-time)...
Building dashboard for single-port mode (:8099)...

  Lantern launcher
  - Python : C:\Users\you\lantern\venv\Scripts\python.exe
  - API dir: C:\Users\you\lantern\lantern\api
  - UI dir : C:\Users\you\lantern\lantern\ui
  - App    : http://127.0.0.1:8099

Ollama already running on :11434
Lantern is ready.
  Dashboard:  http://127.0.0.1:8099/#launch=...
```

Your default browser should open `http://127.0.0.1:8099` and you should see the Lantern dashboard with the **Brief** tab selected and the orange "Run Pipeline" button in the header.

If you see a destructive-coloured banner saying *"Backend not reachable on localhost:8099"*, the Python process didn't start — check the launcher window for the Python error, then see [TROUBLESHOOTING → Backend not reachable](TROUBLESHOOTING.md#backend-not-reachable).

**First-run timing:** the launcher takes ~2-5 minutes the first time (Python deps, npm packages, Ollama warmup). Subsequent launches are 10-15 seconds.

---

## 5. Configure Lantern from the dashboard

All the user-facing tuning happens in the **Settings** tab. Walk top to bottom:

### 5a. Resume

Drag-and-drop your resume PDF or DOCX onto the upload area. The backend parses it into a structured profile (skills, technologies, domains, target roles, summary, years of experience). This takes 10-30 seconds for the first parse.

**Success check:** the Resume card flips from "Upload your resume" to a card showing your filename, parsed years of experience, and a list of detected skills. You can click into it to inspect and edit any field.

### 5b. Titles

Comma-separated list of role keywords every scraper uses to filter results. Defaults are decent for PM-track searches; tighten / loosen for your role.

**Success check:** the comma-separated list reflects what you typed. No save needed yet — there's a single Save button at the bottom of Settings that handles all sections together.

### 5c. Match scoring

Sliders for the match threshold, ghost penalty, salary floor, etc. Defaults are research-backed (see [README](README.md) for rationale); tune later after you've seen a few cycles of results.

### 5d. Freshness windows

Three sliders, one per company-size tier. Defaults (30d / 14d / 7d) are tuned for big-tech evergreen reqs vs. startup early-bird advantage. See [README → Per-tier freshness](README.md) for the rationale.

### 5e. Location

Drop pins on a map of metros you'd actually work from. Each pin is a centre-of-region reference — the radius slider controls how wide each pin's geofence is. The text fields below let you also include locations the static geocoder doesn't know about.

**Success check:** pins appear as orange circles on the map. The "Location filter on" badge appears on the Matches tab once you save.

### 5f. Companies

Three lists — Greenhouse, Lever, Ashby tenants. Add or remove freely. Each list ships with ~15-45 sensible defaults. There's also a **Custom sources** panel for the per-source toggles (Amazon, Google, Workday tenants).

### 5g. Models

Pick which local Ollama model handles each task. The dropdowns list whatever `ollama list` actually has installed on this machine. If Ollama isn't running, you'll see a destructive banner with the fix.

**Recommended model picks** (assumes you pulled the recommended set in Step 3):

| Task | Pick | Why |
|---|---|---|
| Parse | `qwen3:14b` | Fast structured extraction |
| Match | `qwen3:14b` | Only used as LLM fallback when sentence-transformers isn't installed |
| Analyze | `phi4-reasoning:14b` | Reasoning trace for fit/gap rationale |
| Digest | `gemma3:12b` | Cheap prose for cycle summaries |
| Chat | `qwen3:8b` | Lightweight, fast responses |
| Cover letter | `qwen3:30b-a3b` | MoE — quality lift for prose without breaking 16 GB GPU |

If you only pulled the minimum (qwen3:14b + qwen3:8b), set **all** tasks to one of those two. The pipeline will run; analyze and cover-letter quality will be lower but not broken.

### 5h. Save

One **Save** button at the bottom commits everything (config + resume profile) in a single network round-trip.

---

## 6. Run your first pipeline cycle

Click **Run Pipeline** in the header (orange button, top right).

### What success looks like

In the header, a status badge appears: `INGESTING → PARSING → SCORING → ANALYZING → IDLE`. Each stage takes roughly:

- **Ingest**: 30-60s (HTTP calls to all the configured tenants, polite delays between)
- **Parse**: 3-6 min on first run (LLM extracts structured fields from HTML cards that don't ship JSON; cached for subsequent cycles)
- **Score**: 30-60s (embedding similarity + adjustments + ghost fold)
- **Analyze**: 1-2 min (LLM rationale on top-N matches)

Total **first cycle: 5-10 minutes**. Subsequent cycles only score new postings (~2-3 min).

While the cycle is running, the Brief tab shows live counts ("Cycle in progress, more landing live"). The Matches tab populates incrementally — you can click into rows as they appear.

---

## 7. Steady-state usage

Once the first cycle finishes, your typical session is:

1. Click **Matches** in the header
2. Sort by **Score** (already the default)
3. Click into the top few rows; each one opens the right-rail detail panel with the full JD + fit/gap rationale + cover-letter generator
4. **Star** ones you want to revisit (yellow), **Pass** the noise (X)
5. Hit **Apply** when you've decided — opens the real listing on the company's site

Re-run the pipeline once or twice a day. Cycles only score new postings, so the cost is bounded.

---

## 8. Updating Lantern

```bash
git pull
```

The launcher will install any new Python or npm dependencies on the next start. Your `config.json`, `data/` folder, and resume are gitignored so a pull never clobbers your local state.

---

## Where things live

| What | Where |
|---|---|
| Your config | `lantern/api/config.json` (gitignored — local only) |
| Your match registry, decisions, resume | `lantern/api/data/` (gitignored — local only) |
| Backend logs | `lantern/api/logs/lantern.log` (gitignored — local only) |
| Backend code | `lantern/api/` |
| Frontend code | `lantern/ui/` |
| Default config template | `lantern/api/config.example.json` (tracked — what new clones get) |

If you want a totally fresh start, delete `lantern/api/data/` and `lantern/api/config.json` and re-run the launcher. It'll re-seed config from the example and recreate empty state.

---

## License

MIT — see [LICENSE](LICENSE).
