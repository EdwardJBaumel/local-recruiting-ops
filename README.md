# Lantern

**Local-first job intelligence — scrape, score, flag ghosts, all on your machine.**

[Portfolio page](https://edwardjbaumel.github.io/lantern/) · [Setup](SETUP.md) · [Troubleshooting](TROUBLESHOOTING.md) · MIT License

> A self-hosted pipeline that ingests public ATS feeds, scores roles against your resume with embeddings + local LLMs, flags ghost jobs with explainable signals, and surfaces matches in a dashboard — without sending your data to anyone else's servers.

---

## Quick start (for users)

Lantern is a **local power tool**, not a hosted SaaS. If you can install Ollama and run one startup script, you get a private job pipeline with no API bills.

### Requirements

| Need | Notes |
|------|--------|
| Python 3.11+ | Backend |
| Node 18+ | Builds the dashboard on first run |
| [Ollama](https://ollama.com/download) | Local LLM runtime |
| ~14 GB model disk | `qwen3:8b` + `qwen3:14b` recommended |
| GPU optional | ~3 min/cycle on 16 GB VRAM · ~8–12 min CPU-only |

**First run:** expect 10–20 minutes (deps, UI build, model warmup). **After that:** ~1–2 minutes to launch.

### Install

```bash
git clone https://github.com/edwardjbaumel/lantern.git
cd lantern

ollama pull qwen3:8b
ollama pull qwen3:14b
```

**Windows**

```powershell
.\start.ps1
```

**macOS / Linux**

```bash
chmod +x start.sh
./start.sh
```

Opens **one app URL:** [http://127.0.0.1:8099](http://127.0.0.1:8099)

Then: **Settings → upload resume → Run Pipeline**.

Personal config and match data live in `lantern/api/data/` and are **gitignored**.

**UI dev mode** (hot reload on `:3000`):

```powershell
$env:LANTERN_DEV_UI = "1"; .\start.ps1
```

Full walkthrough: [SETUP.md](SETUP.md)

---

## What you get

| Tab | Purpose |
|-----|---------|
| **Brief** | Market charts, source health, skill gaps, last-cycle funnel |
| **Matches** | Sortable registry with ghost badges, fit/gap detail, cover letters |
| **History** | Long-run cycle timeline (ingest → matches per run) |
| **Settings** | Resume, companies, scoring weights, models, reset |

**Pipeline per cycle (~900–1,000 raw postings):**

- **Scrape** — Greenhouse, Lever, Ashby tenants you configure, plus Amazon, Google (page 1), Workday, RemoteOK, Jobicy
- **Match** — `BAAI/bge-m3` embeddings + tunable soft filters (location, salary, experience)
- **Parse** — Local LLM on HTML-only sources (`qwen3:8b`)
- **Ghost detect** — Nine explainable signals (not a black-box score)
- **Analyse** — Fit/gap on top N matches only (`qwen3:14b`)

**$0 API cost.** Resume never leaves your machine.

---

## For recruiters (30-second skim)

**What it is:** A working product, not a mockup. Multi-stage agent pipeline + React dashboard, built solo while job-searching.

**Why it exists:** Ghost jobs, opaque aggregators and cloud-only "AI recruiters" that want your full work history.

| Skill | Evidence |
|-------|----------|
| Product judgment | Cut auto-apply, cloud version, TOS-violating scrapers and v1 bloat before GitHub; four tabs, every setting earns its place |
| AI / agents | Ingest → parse → match → ghost-fold → analyse orchestrator; task-specific Ollama models; embedding-first scoring |
| Technical depth | Python backend, TS frontend, 256 tests, CI, atomic JSON state, two-pass match (13 min → 3 min time-to-first-result) |
| Iteration | Frozen v1 in `archive/sentinel/` beside production v2 |

**Scope:** ~1,000 jobs/cycle · $0 API cost · 16 GB GPU or CPU-only · [Portfolio page →](https://edwardjbaumel.github.io/lantern/)

I'm a Senior PM looking for AI-platform / developer-tools roles. [LinkedIn](https://www.linkedin.com/in/edwardbaumel/)

---

## How it works

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. INGEST     → ~900–1,000 raw postings from configured sources    │
│  2. MATCH      → fast pass on JSON batch (embeddings)               │
│  3. PARSE      → HTML-only cards (qwen3:8b)                          │
│  4. MATCH      → slow pass on newly parsed cards                    │
│  5. GHOST-FOLD → nine signals → explainable ghost score             │
│  6. ANALYZE    → fit/gap on top N (qwen3:14b)                      │
│  7. PERSIST    → match registry + cycle history + market intel      │
└─────────────────────────────────────────────────────────────────────┘
```

<details>
<summary><strong>Engineering trade-offs</strong> (click to expand)</summary>

**Local LLMs, not OpenAI** — ~$3–25/cycle on hosted APIs at this volume; Ollama runs free locally.

**Embeddings, not LLM-as-judge** — Deterministic cosine similarity ~50× faster; LLM reserved for parse and prose.

**Rules-based ghosts** — Explainable signals beat a classifier with no labels.

**Two-pass match** — Time-to-first-match dropped from ~13 min to ~3 min.

**Not built:** auto-apply · cloud version · TOS-violating scrapers · mobile app

</details>

---

## Stack

| Layer | Tool |
|-------|------|
| Frontend | Vite · React 18 · TypeScript · Tailwind · shadcn/ui |
| Backend | Python stdlib `http.server` · threaded orchestrator |
| LLM | Ollama (`qwen3:8b`, `qwen3:14b`) |
| Embeddings | sentence-transformers (`BAAI/bge-m3`) |
| Tests | pytest (190) · vitest (66) · GitHub Actions CI |

---

## Repo layout

```
.
├── README.md              ← you are here
├── docs/index.md          ← GitHub Pages portfolio landing
├── SETUP.md               ← full install walkthrough
├── AGENTS.md              ← cloud-agent guide
├── start.ps1 / start.sh   ← one-command launcher
├── lantern/api/           ← Python backend (:8099)
├── lantern/ui/            ← React source (built into backend on launch)
└── archive/               ← frozen v1 (Sentinel), reference only
```

---

## Develop & test

```bash
cd lantern/api && pip install -r requirements.txt -r requirements-dev.txt && pytest
cd ../ui && npm ci && npm test
```

Cloud agents: see [AGENTS.md](AGENTS.md)

---

## GitHub Pages

Portfolio landing: [docs/index.md](docs/index.md)

After push, enable **Settings → Pages → Deploy from branch → `master` → `/docs`**.

Live URL: `https://edwardjbaumel.github.io/lantern/`

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Eddie Baumel](https://www.linkedin.com/in/edwardbaumel/).
