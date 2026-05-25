# SENTINEL

Local-first job intelligence. Eight-stage agent pipeline, four local LLMs, one React dashboard. Zero cloud spend.

SENTINEL discovers product roles from 35+ career APIs, parses the listings with local LLMs, scores them against a resume profile using `BAAI/bge-m3` embeddings and an LLM re-rank, runs fit-gap analysis, generates a tailored ATS-safe PDF resume, and surfaces the shortlist in a keyboard-first triage dashboard. Runs unattended on a single workstation.

## Why it exists

The first hour of any senior PM search is filtering. 70%+ of surfaced listings are noise that only reads as noise after the full JD. SENTINEL pushes that work onto a machine that never gets bored, and lets the human spend their judgement on the shortlist.

The project also doubles as a working brief of the decisions it was built to support: tool-to-task matching, tight feedback loops, explicit prioritisation, and an honest roadmap.

## Architecture

```
[1] INGEST  [2] PARSE  [3] QA   [4] FAKE    [5] MATCH    [6] FIT-GAP   [7] RESUME    [8] TRACK
 35+ APIs   qwen2.5    rule     heuristic   bge-m3 +     deepseek-r1   qwen3:14b     atomic JSON,
 Remotive   :14b       filters  9-signal    qwen3:14b    :14b          (tailored     registry,
 fallback                        ghost det   re-rank                    PDF/HTML)     decisions
```

Backend (`sentinel/`, Python, API on `:8099`) drives the cycle. Frontend (`sentinel-ui/`, Vite + React, dev on `:3000`, served from `:8099` in prod builds) reads `/api/*` and renders live progress, matches, Blitz triage, fit-gaps, market intel, and a context-aware chat tab.

State is persisted as atomic JSON via `core/io_safe.py`. The match registry is keyed by `company || title || normalised_location` with a Jaccard-0.75 near-dupe fallback so "Senior PM, Ads" and "Senior PM, Ads Platform" at the same company collapse into one row.

### Model routing

Each stage is routed to the smallest model that handles it. All four run locally via Ollama.

| Stage             | Model              | Why                                                |
|-------------------|--------------------|----------------------------------------------------|
| Parse, QA         | `qwen2.5:14b`      | Structured HTML-to-JSON extraction, fast path      |
| Match re-rank     | `qwen3:14b`        | Nuanced judgement over the embedding shortlist     |
| Fit-gap analyse   | `deepseek-r1:14b`  | Reasoning trace over matched vs missing skills     |
| Weekly digest     | `gemma3:12b`       | Cheap prose generation over structured counts      |
| Chat, cover notes | `qwen3:14b`        | Same chat model reused end-to-end for cache hits   |

Matching itself is two-stage: `BAAI/bge-m3` cosine similarity shortlists, then `qwen3:14b` re-ranks the top slice. The threshold is unified at 0.45. Displayed scores are a piecewise-linear stretch of the raw 0.40–0.70 band into 5–98% so the UI spreads confidence without distorting the threshold logic.

Four 12-14B models in rotation is honest about the trade-off. Consolidating to two is on the roadmap. On an RTX 4070 Super the cycle fits in VRAM because only one model is resident at a time and Ollama handles the swap.

### Data sources

Public APIs only. No headless browsers, no proxy rotation, no stealth tooling.

- **Greenhouse**: 27 companies (Stripe, Airbnb, Figma, Databricks, Coinbase, Cloudflare, Discord, GitLab, Lyft, Pinterest, Robinhood, Duolingo, CoreWeave, Notion, DoorDash, Plaid and 11 more).
- **Lever**: Netflix, Spotify.
- **Ashby**: OpenAI, Ramp, Vercel.
- **Direct**: Apple (`jobs.apple.com/api`), Amazon (`amazon.jobs/.../search.json`).
- **HTML scrape + LLM parse**: Google, Meta, Microsoft careers.
- **Broad-catchment**: Remotive JSON (3 keywords, 40 jobs each per cycle).

Dead ATS slugs are tracked in `data/dead_slugs.json` with a 7-day rolling cooldown so a 404 pauses the slug instead of hammering it every 30 minutes. If it comes back alive the cooldown clears automatically.

## Features worth reading the code for

### Deterministic ghost-job detector

`core/fake_detector.py` scores listings against nine independent signals: scam phrases, hidden company, unrealistic salary, personal email, shortened URL, stale timestamp, vague location on a non-remote role, seniority contradictions, and stack overload. Threshold is user-tunable (`GHOST_SUSPECT_THRESHOLD = 0.45`, deliberately on a different axis from the match threshold of the same number).

### Feedback learner with cold-start discipline

`core/feedback_learner.py` keeps embeddings of starred and dismissed matches, then nudges future match scores toward what the user actually said yes or no to. It refuses to adjust anything until ≥3 samples exist in each bucket so a single stray click cannot poison the ranking.

### Blitz triage

Keyboard-first, one match per screen, slot-machine slide animation for the next card. Arrows map to skip, keep, maybe, undo. Rolling-60-second combo detection surfaces "8 keeps in a minute" overlays. A lightweight accountability pet (Pip) reacts to session pace and keeps the run human. Every decision is logged with a reason for later pattern analysis.

### Fit-gap and tailored resumes

Each shortlisted role gets a fit-gap report (matched skills, gaps with severity, mitigation paths, talking points). For the top-N the system rewrites the professional summary, reorders bullets by relevance, injects JD keywords, and emits an ATS-safe PDF via WeasyPrint, or HTML if the build does not ship WeasyPrint.

### Guided wizard

An eight-step in-dashboard modal (Welcome, Ready-check, Resume, Roles, Experience, Filters, Models, Review) triggers on first run when config or resume state is empty. Detects VRAM and recommends Full Power, Balanced, or Lightweight presets.

### Live dashboard and chat

Vite + React, editorial typography (IBM Plex Mono, Outfit, Instrument Serif), theme tokens for accent, good, warn, text, border. Brief, Matches, Blitz, Decisions, Fit-gap, Market, Chat, and Settings tabs. Chat reads matches, decisions, fit-gaps, resume, and market data in context so the user can ask "why did we pass on Stripe's PM-Ads role" and get a cited answer.

## Local resource budget

| Resource | Cost                                                                |
|----------|---------------------------------------------------------------------|
| VRAM     | ~10 GB peak, one model resident at a time via Ollama                |
| RAM      | ~2 GB Python, ~1 GB embedding model                                 |
| Disk     | ~25 GB models, ~50 MB state per month of operation                  |
| Network  | Per cycle: ~2 MB ATS JSON, ~500 KB HTML scrape, 0 bytes to any LLM  |
| Tokens   | 0 cloud tokens. Everything inferred locally                         |

## Tests

85 pytest cases covering preferences (location, salary, experience filters and scorers), sub-score dimensions (seniority, tech, domain, years, requirements), the ghost-job detector, the dead-slug cooldown, and the match registry near-dupe fallback.

```bash
pip install -r requirements-dev.txt
pytest
```

## Setup

### Prerequisites
- Python 3.11+
- [Ollama](https://ollama.com) running locally
- Node.js 18+

### Pull the models

```bash
ollama pull qwen2.5:14b
ollama pull qwen3:14b
ollama pull deepseek-r1:14b
ollama pull gemma3:12b
```

### Dev loop

From the repo root:

```powershell
.\start.ps1        # Windows
```

```bash
./start.sh         # macOS / Linux
```

Both servers come up together, the dashboard opens at `http://127.0.0.1:3000`, and Ctrl+C stops them cleanly. The launcher auto-creates a venv and installs `sentinel/requirements.txt` on first run.

### Packaged build (single EXE)

```powershell
.\build.ps1        # Windows
```

```bash
./build.sh         # macOS / Linux
```

Output is `dist/sentinel.exe` (Windows) or `dist/sentinel` (Linux/macOS). Ollama is not bundled. On first launch the binary writes a default `config.json` beside itself and creates `data/` for matches, resumes, and logs. Override the data location with `SENTINEL_HOME`. Pass `--dashboard-only` to host the UI without the scraper loop.

The EXE deliberately excludes WeasyPrint and sentence-transformers to keep the bundle small. Resume PDF gen falls back to HTML and matching falls back to the LLM-only scoring path, so both features still work.

### Configuration

Edit `sentinel/config.json` for companies, role keywords, match threshold, cycle interval, email (Gmail app password), Discord webhook, and digest frequency.

## Product decisions worth defending

**Location is a hard filter, salary is a soft weight.** Wrong city means wrong job. A role paying 10% under floor is still worth a conversation.

**Thumbs up or down on a match is tag-only, no ML.** Stars and dismissals feed a separate embedding-based learner. Keeping the channels separate stops a casual tag from nudging future ranks.

**Director/VP/CXO trap-door drops any senior+ role if the user has <10 years experience.** Prevents the matcher from wasting LLM cycles on listings that will never convert.

**Match threshold unified at 0.45 raw.** Displayed score is calibrated separately. Any debate about "is 60% actually good" collapses into one knob.

**Single 4.7k-line `App.jsx`.** Inline styles, no CSS-in-JS, no Tailwind. Easier to grep, faster to refactor under LLM assistance, no build-time surprises. Split is on the backlog for when the file crosses the pain threshold, not before.

**Auto-apply is not a feature.** A PM who mass-applies with a generic resume is demonstrating the opposite of prioritisation. Fit-gap helps the human tailor.

## Roadmap

In priority order, with honest reasons for not yet shipping.

- **LLM consolidation 4 models to 2.** Biggest single VRAM win. Needs evaluation pass to confirm quality holds when one model handles both parse and match re-rank.
- **App.jsx tab split.** Worth the refactor at the next major UI pass, not as churn for its own sake.
- **SQLite migration behind the `match_registry` facade.** JSON is fine at current volume. Swap becomes worthwhile around 10k persistent matches.
- **Embedding model swap bge-m3 to bge-base-en-v1.5.** ~40% smaller, minimal quality loss on English PM listings. Gated on A/B.
- **Brief-tab resource panel.** VRAM, token count, wall-clock per stage, live. Grounds the "is this worth running now?" question.
- **Pet (Pip) evolution.** Milestone animations, Discord re-engagement webhook, customisation. Feature work, not stability.
- **Playwright for a small set of protected boards.** Only if the API-first sources stop covering the target set.

## Built by

Eddie Baumel. Platform PM at Deloitte Digital, Irvine CA. 5 years experience. [LinkedIn](https://www.linkedin.com/in/edwardbaumel/) (or wherever you found this repo).

## License

MIT
