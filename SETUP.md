# Lantern — Setup

A complete walk-through from a clean machine to a running first cycle.

If something goes wrong, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## 1. Prerequisites

| Tool | Minimum | Install | Verify |
|---|---|---|---|
| **Python** | 3.11+ | https://python.org/downloads | `python --version` |
| **Node.js** | 18+ | https://nodejs.org | `node --version` |
| **Ollama** | latest | https://ollama.com/download | `ollama --version` |
| **Git** | any | https://git-scm.com (Windows only — pre-installed elsewhere) | `git --version` |

**Disk budget:**
- ~5 GB minimum — `qwen3:8b` only
- ~14 GB recommended — `qwen3:8b` + `qwen3:14b`
- ~5 GB system RAM for the Python process and embedding model

---

## 2. Clone

```bash
git clone https://github.com/edwardjbaumel/lantern.git
cd lantern
```

**Check:** `ls` shows `lantern/`, `archive/`, `start.ps1`, `start.sh`, `README.md`.

---

## 3. Pull at least one Ollama model

The launcher starts Ollama automatically but will not download models for you.

**Minimum (one model, ~5 GB):**

```bash
ollama pull qwen3:8b
```

**Recommended (~14 GB total):**

```bash
ollama pull qwen3:8b
ollama pull qwen3:14b
```

`qwen3:8b` handles parse. `qwen3:14b` handles analyse, digest and cover letter. You can change per-task model assignments after launch from **Settings → Models**.

**Check:** `ollama list` shows the models you pulled.

---

## 4. Launch

The launcher creates the Python venv, installs dependencies, seeds `config.json` from the example template, builds the UI and opens a browser — all on first run.

**Windows**

```powershell
.\start.ps1
```

If PowerShell execution policy blocks the script, double-click `Start LANTERN.cmd` instead. See [TROUBLESHOOTING → Windows execution policy](TROUBLESHOOTING.md#windows-execution-policy-blocks-startps1).

**macOS / Linux**

```bash
chmod +x start.sh && ./start.sh
```

**First run output looks like this:**

```
No Python venv found — bootstrapping one (first-run setup)...
Installing Python dependencies (this takes ~2 min the first time)...
No config.json found — seeding from config.example.json (first-run setup)...
Installing dashboard dependencies (one-time)...
Building dashboard for single-port mode (:8099)...

  Lantern launcher
  - Python : /path/to/venv/bin/python
  - API dir: /path/to/lantern/api
  - UI dir : /path/to/lantern/ui
  - App    : http://127.0.0.1:8099

Ollama already running on :11434
Lantern is ready.
  Dashboard:  http://127.0.0.1:8099/#launch=...
```

**First run takes ~10–20 minutes.** Subsequent launches take ~1–2 minutes.

A browser tab should open at `http://127.0.0.1:8099` showing the **Brief** tab and an orange **Run Pipeline** button in the header.

If you see a red "Backend not reachable" banner, the Python process did not start — check the launcher window for the error message, then see [TROUBLESHOOTING → Backend not reachable](TROUBLESHOOTING.md#backend-not-reachable).

---

## 4a. GPU acceleration

This is the biggest performance lever. The match phase uses PyTorch + sentence-transformers to embed job descriptions — ~1 min on a consumer NVIDIA GPU, ~80 min on CPU.

The launcher prints one of three lines:

```
GPU: NVIDIA GeForce RTX 5070 Ti (torch 2.x+cu128, CUDA 12.8)   ← good
GPU: none detected (CPU mode — slower but functional)            ← no GPU
NVIDIA GPU detected but PyTorch is CPU-only.                     ← needs fix
```

The third case means you have a GPU but the default `pip install torch` installed the CPU-only wheel.

**Fix on Windows + NVIDIA:**

```powershell
.\venv\Scripts\python.exe -m pip uninstall -y torch torchvision torchaudio
.\venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
```

**Verify:**

```powershell
.\venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# Should print: True  NVIDIA GeForce RTX ...
```

**Linux + NVIDIA:** same commands using `./venv/bin/python`.

**macOS (Apple Silicon):** no CUDA — sentence-transformers uses MPS automatically. No action needed.

**No GPU:** CPU mode works for every stage, just slower. No action needed.

---

## 5. Configure from the dashboard

Open **Settings** and work through each section.

### Resume

Drag-and-drop your PDF or DOCX onto the upload area. The backend parses it into a structured profile (skills, years of experience, target roles). Takes 10–30 seconds.

**Check:** the Resume card shows your filename, parsed experience years and detected skills.

### Titles

Keywords every scraper uses to filter job titles. The defaults target PM and TPM roles — adjust for your search.

### Location

Drop pins on metros you would actually work from. Each pin is a region centre; the radius slider controls the geofence width.

### Companies

Three tenant lists — Greenhouse, Lever, Ashby. Ships with ~15–45 defaults each. Add or remove freely.

### Models

Dropdowns populated from whatever `ollama list` reports on your machine. Each task has its own picker.

**Defaults** (what `config.example.json` ships with):

| Task | Default | Notes |
|---|---|---|
| Parse | `qwen3:8b` | Structured extraction from HTML — 8B is accurate enough, faster than 14B |
| Match LLM fallback | `qwen3:14b` | Only used if sentence-transformers is not installed |
| Analyse | `qwen3:14b` | Fit/gap rationale on top-N matches |
| Digest | `qwen3:14b` | Short cycle summary prose |
| Cover letter | `qwen3:14b` | 3–4 paragraph tailored letter |

If only `qwen3:8b` is installed, set all tasks to `qwen3:8b`. Every stage will run; analysis prose will be lighter but not broken.

The panel also shows `ollama pull <model>` commands for any suggested model not yet installed.

### Save

One **Save** button at the bottom commits everything in a single round-trip.

---

## 6. Run your first cycle

Click **Run Pipeline** (orange button, header).

The status badge in the header steps through: `INGESTING → PARSING → SCORING → ANALYZING → IDLE`

Rough timings (GPU, recommended model set):

| Stage | First run | Later runs |
|---|---|---|
| Ingest | 30–60 s | 30–60 s |
| Parse | 3–6 min | seconds (cached) |
| Score | 30–60 s | 30–60 s |
| Analyse | 1–2 min | 1–2 min |

**Total first cycle: ~5–10 min on GPU, ~60–90 min on CPU.**

Matches appear in the **Matches** tab as the cycle progresses — you can click into rows before it finishes.

---

## 7. Day-to-day use

1. Open the **Matches** tab, sorted by Score (default)
2. Click a row to open the detail panel — full JD, fit/gap rationale, cover-letter generator
3. **Star** roles to revisit, **Pass** noise, **Apply** to open the real listing
4. Re-run the pipeline once or twice a day — only new postings get scored, so cycles stay fast

---

## 8. Updating

```bash
git pull
```

The launcher installs any new Python or npm dependencies on next start. `config.json` and `data/` are gitignored — a pull never overwrites your local state.

---

## Where things live

| What | Path |
|---|---|
| Live config | `lantern/api/config.json` (gitignored) |
| Matches, decisions, resume | `lantern/api/data/` (gitignored) |
| Backend logs | `lantern/api/logs/lantern.log` (gitignored) |
| Config template | `lantern/api/config.example.json` (tracked) |

For a clean slate, delete `lantern/api/data/` and `lantern/api/config.json` and relaunch.

---

## License

MIT — see [LICENSE](LICENSE).
