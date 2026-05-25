# Lantern

**Multi-agent, local-first job intelligence — designed, built and shipped end-to-end.**

[Portfolio page](docs/index.md) · [LinkedIn](https://www.linkedin.com/in/edwardbaumel/) · [Setup](SETUP.md) · MIT License

> A self-hosted pipeline that scrapes public ATS feeds, scores roles against your resume with embeddings + local LLMs, flags ghost jobs with explainable signals, and surfaces matches in a three-tab dashboard — without sending your data to anyone else's servers.

---

## For recruiters (30-second skim)

**What it is:** A working product, not a mockup. Multi-stage agent pipeline + React dashboard, built solo while job-searching.

**Why it exists:** Ghost jobs, opaque aggregators and cloud-only "AI recruiters" that want your full work history.

**What it proves:**

| Skill | Evidence |
|-------|----------|
| Product judgment | Cut auto-apply, cloud version, TOS-violating scrapers and v1 bloat before GitHub; three tabs, every setting earns its place |
| AI / agents | Ingest → parse → match → ghost-fold → analyse orchestrator; task-specific Ollama models; embedding-first scoring |
| Technical depth | Python backend, TS frontend, 256 tests, CI, atomic JSON state, two-pass match (13 min → 3 min time-to-first-result) |
| Iteration | Frozen v1 in `archive/sentinel/` beside production v2 — credible v1→v2 story |

**Scope:** ~1,000 jobs/cycle · $0 API cost · 16 GB GPU or CPU-only · [Full portfolio write-up →](docs/index.md)

I'm a Senior PM looking for AI-platform / developer-tools roles. If your team builds tools where the user is the product, not the data, [let's talk](https://www.linkedin.com/in/edwardbaumel/).

---

## The problem

Applying to Senior PM roles in early 2026 surfaced three patterns:

1. **Ghost jobs.** A meaningful share of postings had been up for 60+ days with no movement — pure funnel-padding.
2. **Aggregators.** LinkedIn / Indeed / Wellfound bury matches behind paywalls and email digests timed for engagement, not relevance.
3. **Privacy.** Every AI-powered alternative wants the full work history uploaded to their cloud.

Lantern fixes all three with one tool that runs entirely on your machine.

---

## What it does

| | |
|---|---|
| **Scrape** | Greenhouse / Lever / Ashby tenants you configure, plus Amazon JSON, Google Careers (page 1, robots.txt-respectful), Workday tenants, RemoteOK and Jobicy. ~900–1,000 raw postings per cycle. |
| **Parse** | Local LLM (`qwen3:8b`) extracts structured fields from HTML sources. JSON-API sources skip parse. |
| **Score** | Embedding similarity (`BAAI/bge-m3`) + soft adjustments for title, location, salary and experience. |
| **Detect ghosts** | Nine deterministic heuristics with per-signal severity caps and explainable reasons in the UI. |
| **Surface** | Brief (market), Matches (sortable table + detail), Settings (every knob exposed). |

Full cycle: **~3 minutes on a 16 GB GPU** or **~8–12 minutes CPU-only**.

---

## How this differs from other "GenAI for jobs" projects

- **Local-only.** Ollama + sentence-transformers. No OpenAI bill. Resume never leaves the box.
- **Ghost-aware.** Explainable 0–100 ghost score — headline differentiator.
- **Tunable, not magical.** Every weight exposed; scores decompose to inputs you can inspect.
- **TOS-respectful.** Public structured feeds only; stealth scrapers removed before public release.

---

## How it works

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. INGEST     → ~900–1,000 raw postings from configured sources    │
│  2. MATCH      → fast pass on JSON-parsed batch (embeddings)        │
│  3. PARSE      → HTML-only cards in parallel (qwen3:8b)             │
│  4. MATCH      → slow pass on newly parsed cards                    │
│  5. GHOST-FOLD → nine signals → explainable ghost score              │
│  6. ANALYZE    → fit/gap on top N only (gemma3:12b)                 │
│  7. PERSIST    → atomic JSON writes + per-path locking (Windows)     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Engineering choices

<details>
<summary><strong>Why local LLMs, embeddings, rules-based ghosts, two-pass match, and what I did not build</strong> (click to expand)</summary>

### Why local LLMs (and not OpenAI)
~1,000 jobs/cycle costs ~$3 on GPT-4o-mini and ~$25 on Sonnet. At one cycle/day for two months that's $600–1,500. Local Ollama runs on a 16 GB GPU or CPU-only in 8–12 min per cycle.

### Why embeddings instead of LLM-as-judge
Cosine similarity is deterministic and ~50× faster. LLM reserved for parsing and prose deliverables (fit/gap, cover letters).

### Why ghost detection is rules-based
Explainable signals ("posted 92 days ago", "missing apply link") beat a black-box classifier with no labels.

### Why a two-pass match split
Single-pass meant an empty Matches tab for ~8 minutes. Two-pass dropped **time-to-first-match from ~13 min to ~3 min**.

### Why chunked embedding pre-encode
First match visible within ~10 seconds of match stage start on CPU (32-job encode chunks + streaming callbacks).

### What I deliberately didn't build
Auto-apply · cloud version · mobile app · job board marketplace · TOS-violating scrapers

</details>

---

## Stack

| Layer | Tool |
|---|---|
| Frontend | Vite · React 18 · TypeScript · Tailwind · shadcn/ui |
| Backend | Python stdlib `http.server` · threaded orchestrator |
| LLM | Ollama (qwen3:8b / 14b / 30b-a3b, gemma3:12b) |
| Embeddings | sentence-transformers (`BAAI/bge-m3`) |
| Tests | pytest (190) · vitest (66) · GitHub Actions CI |

---

## Repo layout

```
.
├── README.md              ← you are here
├── docs/index.md          ← resume / portfolio page (GitHub Pages)
├── AGENTS.md              ← cloud-agent guide
├── lantern/api/           ← Python backend (:8099)
├── lantern/ui/            ← React frontend (:3000)
└── archive/               ← frozen v1 (Sentinel), kept for reference
```

---

## Setup

**Full walkthrough:** [SETUP.md](SETUP.md) · **Troubleshooting:** [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

**Prerequisites:** Python 3.11+ · Node 18+ · [Ollama](https://ollama.com/download)

```bash
ollama pull qwen3:8b
ollama pull qwen3:14b
ollama pull gemma3:12b
```

```powershell
.\start.ps1          # Windows
./start.sh           # macOS / Linux
```

Launcher bootstraps venv, `npm install`, seeds `config.json` from `config.example.json`, starts backend + UI.

Personal config and match data live in `lantern/api/data/` and are **gitignored**.

---

## GitHub Pages (optional)

To publish the portfolio page at `https://YOUR_USERNAME.github.io/lantern/`:

1. Push this repo to GitHub
2. **Settings → Pages → Build from branch → `main` → `/docs`**
3. The landing page is [docs/index.md](docs/index.md)

**Suggested repo About line:** `Local-first multi-agent job pipeline — embeddings, Ollama, ghost-job detection. Portfolio piece.`

**Suggested topics:** `ollama` `local-llm` `job-search` `embeddings` `multi-agent` `python` `react` `portfolio-project`

---

## License

MIT — see [LICENSE](LICENSE). Fork it, run it, build on it. Keep the copyright notice.

Built by [Eddie Baumel](https://www.linkedin.com/in/edwardbaumel/).
