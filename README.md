# Lantern

**Local-first job intelligence — scrape, score, flag ghosts, all on your machine.**

[Setup](SETUP.md) · [Troubleshooting](TROUBLESHOOTING.md) · MIT License

Ingests public ATS feeds, scores roles against your resume with embeddings and local LLMs, flags ghost jobs with explainable signals, surfaces matches in a dashboard. Nothing leaves your machine.

---

## Quick start

### Requirements

| | |
|---|---|
| Python 3.11+ | |
| Node 18+ | |
| [Ollama](https://ollama.com/download) | |
| ~5 GB disk minimum | `qwen3:8b` alone is enough to start |
| ~14 GB disk recommended | adds `qwen3:14b` for better analysis quality |
| NVIDIA GPU optional | ~3 min/cycle on GPU · 30–90 min on CPU |

**First run takes 10–20 minutes** (venv, npm build, model warmup). Subsequent starts take ~1–2 minutes.

---

### Step 1 — Clone

```bash
git clone https://github.com/edwardjbaumel/lantern.git
cd lantern
```

---

### Step 2 — Pull at least one model

```bash
ollama pull qwen3:8b
```

Or both for the recommended default:

```bash
ollama pull qwen3:8b
ollama pull qwen3:14b
```

You can change model assignments at any time in **Settings → Models** after the app is running. The UI shows which models are installed and what each task uses them for.

---

### Step 3 — Launch

**Windows**

```powershell
.\start.ps1
```

**macOS / Linux**

```bash
chmod +x start.sh && ./start.sh
```

Opens: [http://127.0.0.1:8099](http://127.0.0.1:8099)

---

### Step 4 — Configure and run

1. **Settings → Resume** — upload your CV
2. **Settings → Models** — verify model assignments (auto-detected from Ollama)
3. **Settings → Companies** — add or remove ATS tenants to scrape
4. Click **Run Pipeline** in the header

Match data and personal config are written to `lantern/api/data/` which is gitignored.

Full walkthrough: [SETUP.md](SETUP.md)

---

## What you get

| Tab | |
|-----|---|
| **Brief** | Market charts, source health, skill gaps, last-cycle funnel |
| **Matches** | Sortable registry with ghost badges, fit/gap detail, cover letters |
| **History** | Cycle timeline — ingest counts, match counts, run durations |
| **Settings** | Resume, companies, scoring weights, models, danger zone |

**Per cycle (~900–1,000 postings):**

- **Ingest** — Greenhouse, Lever, Ashby tenants + Amazon, Google, Workday, RemoteOK, Jobicy
- **Match** — `BAAI/bge-m3` embeddings, tunable location/salary/experience filters
- **Parse** — local LLM on HTML-only sources (`qwen3:8b`)
- **Ghost detect** — nine explainable signals, not a black-box score
- **Analyse** — fit/gap rationale on top-N matches (`qwen3:14b`)

$0 API cost. Resume never leaves your machine.

---

## How it works

```
1. INGEST     → fetch ~900–1,000 postings from configured sources
2. MATCH      → fast embedding pass on JSON-structured postings
3. PARSE      → LLM extracts fields from HTML-only postings
4. MATCH      → second embedding pass on newly parsed cards
5. GHOST-FOLD → nine signals → explainable ghost score per posting
6. ANALYZE    → fit/gap rationale on top N matches
7. PERSIST    → registry + cycle history + market intel
```

<details>
<summary><strong>Design decisions</strong></summary>

**Local LLMs, not hosted APIs** — ~$3–25/cycle at this volume on OpenAI; Ollama runs free.

**Embeddings first, LLM second** — cosine similarity is ~50× faster and deterministic; LLM is reserved for parse and prose tasks.

**Rules-based ghost detection** — nine interpretable signals beat a classifier trained on no labelled data.

**Two-pass match** — fast pass on pre-parsed JSON, slow pass only on newly parsed HTML. Cut time-to-first-result from ~13 min to ~3 min.

**Deliberate scope cuts** — no auto-apply, no cloud version, no TOS-violating scrapers.

</details>

---

## Stack

| Layer | |
|---|---|
| Frontend | Vite · React 18 · TypeScript · Tailwind · shadcn/ui |
| Backend | Python · threaded orchestrator · stdlib HTTP server |
| LLM runtime | Ollama |
| Embeddings | sentence-transformers · `BAAI/bge-m3` |
| Tests | pytest (190) · vitest (66) · GitHub Actions CI |

---

## Repo layout

```
lantern/api/         Python backend (:8099)
lantern/ui/          React source (built into backend on launch)
archive/sentinel/    Frozen v1 — reference only, do not extend
start.ps1            Windows launcher
start.sh             macOS / Linux launcher
SETUP.md             Full install walkthrough
AGENTS.md            Cloud-agent guide
```

---

## Develop and test

```bash
cd lantern/api
pip install -r requirements.txt -r requirements-dev.txt
pytest

cd ../ui
npm ci && npm test
```

**UI dev mode** (hot reload on `:3000`):

```powershell
$env:LANTERN_DEV_UI = "1"; .\start.ps1
```

Cloud agents: see [AGENTS.md](AGENTS.md)

---

## License

MIT — see [LICENSE](LICENSE).  
Built by [Eddie Baumel](https://www.linkedin.com/in/edwardbaumel/).
