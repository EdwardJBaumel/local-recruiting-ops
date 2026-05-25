# Lantern

**Local-first job intelligence.** A self-hosted pipeline that scrapes ATS feeds, scores roles against your resume with embeddings + a local LLM, flags ghost jobs, and surfaces what the market is paying — without sending a byte of your data to anyone else's servers.

> Built for myself because the job market is a black box and every "AI job tool" wants my resume on their servers.

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
| **Scrape** | All Greenhouse / Lever / Ashby tenants you configure (40 / 2 / 16 by default), plus the Amazon JSON API, Google Careers (page 1 only — robots.txt-respectful), two Workday tenants (Nvidia, Adobe), and the public RemoteOK + Jobicy feeds. ~900–1000 raw postings per cycle. |
| **Parse** | Small local LLM (`qwen3:8b`) extracts structured fields (title, salary, YoE, location, work mode, tech stack) from raw HTML for sources that don't ship JSON. JSON-API sources skip parse entirely. |
| **Score** | Embedding similarity (`BAAI/bge-m3`) between resume and JD, with soft adjustments for title, location, salary, and years of experience. |
| **Detect ghosts** | Nine deterministic heuristics: post age, vague location, buzzword density, missing apply link, salary obfuscation, duplicate titles, missing fields, seniority/YoE conflicts, overloaded stack. Each role gets a 0–100 ghost score with per-signal severity caps so corporate boilerplate alone can't push a fresh posting into Suspect. |
| **Tier-aware freshness** | Mega-tech (Amazon, Google, public big-tech) keeps a 30-day window because their reqs are evergreen; decacorns (Stripe, Databricks, OpenAI…) get 14 days; everything else gets 7. Per-tier defaults in Settings. |
| **Summarise on demand** | A 3–4 sentence narrative summary of any JD via a single click on the match detail panel. Caches into the match registry so subsequent clicks are instant. |
| **Cover letters** | Generate a tailored cover letter for a specific job with one click. Runs `qwen3:30b-a3b` locally — grounded in your parsed resume profile, never invents experience. |
| **Surface** | A 3-tab dashboard: Brief (market overview), Matches (sortable table + detail panel + Apply / Star / Like / Pass), Settings (every knob). |

A full cycle of ~1000 postings takes **~3 minutes on a 16 GB consumer GPU** or **~8–12 minutes on CPU-only Ollama** (parse + match + analyze each adapt: small models, embedding-first scoring, two-pass match split — see the "Engineering choices" section below).

---

## How this differs from the 50 other "GenAI for jobs" projects

- **Local-only.** Ollama + sentence-transformers run on your hardware. No OpenAI, no Anthropic, no Hugging Face Inference. Your resume never leaves the box.
- **Ghost-aware.** I haven't seen another job tool that scores listings for ghost suspicion with explainable signals. It's a real category and it's the headline differentiator here.
- **Tunable, not magical.** Every weight is exposed: ghost penalty, salary weight, years-gap penalty, geographic radius, freshness windows per company tier. Drag any to 0 to turn it off. The system never tells you "trust me, this is a 92% match" — every score breaks down to inputs you can see.
- **Geographic pin filter.** Drop pins on the cities you'd actually work from. Roles outside the union of pin radii get dropped at scoring time. Combines with text allowlist via OR for cities the geocoder doesn't know.
- **TOS-respectful by design.** Every scraper hits a public structured feed. We deliberately did NOT ship scrapers for Apple, Meta, Microsoft, Tesla, Oracle, or LinkedIn because their TOS / robots.txt prohibits automated access — and the SPA-stealth tooling that would have powered them was removed before this hit GitHub.

---

## How it works

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. INGEST                                                          │
│     All Greenhouse + Lever + Ashby tenants you configure, plus      │
│     Amazon, Google (page 1), 2× Workday, RemoteOK, Jobicy           │
│     → ~900–1000 raw postings                                        │
├─────────────────────────────────────────────────────────────────────┤
│  2. MATCH (fast pass)                                               │
│     Embedding similarity (resume ↔ JD) on the ~900 JSON-parsed      │
│     postings. First match visible in the UI in ~3 minutes.          │
│     (BAAI/bge-m3, chunked encode in batches of 32)                  │
├─────────────────────────────────────────────────────────────────────┤
│  3. PARSE  (only the ~35 Google HTML cards)                         │
│     Small LLM extracts {title, salary, YoE, location, mode, tech}.  │
│     Runs in parallel with the fast-pass match results streaming     │
│     into the registry.   (qwen3:8b, ~3–8s per card)                 │
├─────────────────────────────────────────────────────────────────────┤
│  4. MATCH (slow pass)                                               │
│     Score the newly-parsed cards. Appends to the same registry.     │
├─────────────────────────────────────────────────────────────────────┤
│  5. GHOST-FOLD                                                      │
│     Nine deterministic signals → 0–100 ghost score per role,        │
│     folded into the final display score with explainable per-signal │
│     reasons surfaced in the match detail panel.                     │
├─────────────────────────────────────────────────────────────────────┤
│  6. ANALYZE  (top N matches only)                                   │
│     Mid-tier LLM writes a fit/gap report on the highest-scoring     │
│     roles only.   (gemma3:12b)                                      │
├─────────────────────────────────────────────────────────────────────┤
│  7. PERSIST                                                         │
│     Atomic JSON writes + per-path locking on Windows                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Engineering choices

### Why local LLMs (and not OpenAI)
A scoring loop over 1000 jobs would cost ~$3 per cycle on GPT-4o-mini and ~$25 on Sonnet. At one cycle per day for two months that's $600–1500 sunk into a tool used by one person. Local Ollama with `qwen3:8b` for parse, `qwen3:14b` for match-LLM-fallback, and `gemma3:12b` for analyse runs comfortably on a 16 GB consumer GPU and on CPU-only hardware too (slower, still finishes a cycle in 8–12 min). JSON-extraction quality is equivalent for this task — verified by spot-checking parses against manual ground truth.

### Why embeddings instead of LLM-as-judge for matching
LLM scoring drifts: ask the same model "is this a 0–100 fit" three times, get three different numbers. Embedding cosine similarity is deterministic, ~50× faster (200ms vs. 10s per role), and produces a stable distribution you can set thresholds against. The LLM is reserved for parsing (where structured output matters more than score consistency) and the post-hoc fit/gap rationale (where prose is the deliverable).

### Why ghost detection is rules-based, not ML
Two reasons. (1) I want to *show* the user which signals fired ("posted 92 days ago", "vague location 'Anywhere'", "missing apply link") so they can disagree. A black-box classifier doesn't give that. (2) ML on this would need labels I don't have. Nine heuristics, each weighted, summed to a 0–100 score with per-signal severity caps and a tunable threshold — boring and explainable beats clever and opaque.

### Why a pin-based geographic filter
Substring-matching "San Francisco" misses "Palo Alto", "Mountain View", "Menlo Park". A geocoder + radius treats the Bay Area as one region. Pins also let you express what you actually care about ("I'd consider these specific metros") instead of trying to enumerate every suburb. Falls back to text allowlist via OR for cities the static geocoder doesn't know.

### Why per-tier freshness windows
A single global "max age" is wrong for both ends of the spectrum. Big-tech reqs are evergreen — a 4-day filter would hide perfectly active Amazon openings. Growth-stage hires fast — the early-bird advantage is ~7× higher response rate in the first 4–7 days vs. day 30+. Three windows in Settings, one per tier, each value tunable.

### Why a two-pass match split
The single-pass version waited for PARSE to finish before any row scored. PARSE only runs on the ~35 HTML cards (Google Careers); the ~900 JSON-API postings already have structured fields. Single-pass meant the user stared at an empty Matches tab for 8 minutes while we LLM-parsed a tiny fraction of the data. The two-pass version scores the JSON-parsed batch immediately and runs PARSE in parallel with the slow-pass match — **time-to-first-match dropped from ~13 min to ~3 min** with the same total cycle time. Performance and *perceived* performance are different problems; this fixes the second.

### Why chunked embedding pre-encode
The naive batch path encoded all 900 jobs in one giant `embed_model.encode(...)` call before the per-row scoring loop fired any callbacks. On GPU that's ~10s of silence — fine. On CPU it's 5–15 minutes of silence with no progress indicator and the registry sitting at zero rows, which reads as "the app is broken." We encode in chunks of 32 now: each chunk's encode finishes in ~3–8s, then 32 rows immediately stream into the registry via the per-row `on_scored` callback, then the next chunk encodes. **First match visible within ~10 seconds** of the match stage starting. Same total wall time; massively better UX.

### Why a trapdoor pre-filter at ingest
The match-time experience filter rejects roles requiring more years than the candidate has. But on a sub-8-yoe profile, "Director of Product" and "VP, AI" and "Head of Growth" titles flow all the way through ingest → keyword filter → parse → match-scoring → reject. Each one burns one LLM parse call (~5–10s on `qwen3:8b`). A pre-filter at the *ingest* stage that recognises senior title patterns and drops them before parse saves ~2–3 min per cycle for free. Documented as a `_TRAPDOOR_TITLE_PATTERNS` list so the rule is explicit and turning it off is one config flag away.

### Why on-demand summary instead of summarising every JD at parse
LLM-summarising every JD at parse time would add ~3s per posting × 1000 = 50 extra minutes per cycle. 99% of those summaries would never be read because the user clicks on at most a dozen matches per cycle. The summarise endpoint fires only when the user clicks the "Summarize" button, caches the result into the match registry's payload, and serves the cached value instantly on subsequent visits to the same role. Same product value at 1% of the cost. The pattern (lazy compute, registry-cached, FE-rendered on next poll) is reusable for any per-row LLM artefact — fit-gap on demand, salary normalisation on demand, etc.

### What I deliberately didn't build
- **Auto-apply.** A recruiter can spot an LLM cover letter in 2 seconds. The score-and-rank step is the leverage; the apply step is yours.
- **Cloud version.** Defeats the privacy story and inflates the scope to nothing I'd ship.
- **Mobile.** Job-search is a desk task.
- **A "job board."** This is a personal pipeline, not a marketplace. Solving it for one user is the whole point.
- **Scrapers for TOS-restricted sources.** Removed before public release — see the TOS-respectful note above.

---

## Stack

| Layer | Tool | Why |
|---|---|---|
| Frontend | Vite + React 18 + TypeScript + Tailwind + shadcn/ui | Boring, fast, no design decisions to invent |
| State | Zustand (UI) + TanStack Query (server) + react-hook-form (forms) | Each layer has its own concern — no cross-contamination |
| Map | Leaflet + CartoDB dark tiles | No API key, no rate limits, works offline |
| Backend | Python stdlib `http.server` + threads | One file, zero framework cost. If it ever needs to scale, swap to FastAPI in an afternoon |
| LLM | Ollama (`qwen3:8b` parse, `qwen3:14b` match-LLM-fallback, `gemma3:12b` analyze + digest + summary, `qwen3:30b-a3b` cover letter) | Local, swappable, model-fallback chain handles 404s |
| Embeddings | sentence-transformers (`BAAI/bge-m3`) | Multilingual, top of MTEB at the size class, runs on CPU if no GPU |
| Scrapers | `requests` for ATS APIs + a few first-party JSON endpoints | Public structured feeds only — no headless browsers, no proxy rotation, no stealth tooling |
| Storage | `data/*.json` with per-path-locking atomic writes | No DB ceremony for one user. Migrate to SQLite if a v2 needs it |

---

## Repo layout

```
.
├── README.md              ← you are here
├── AGENTS.md              ← cloud-agent + contributor guide
├── LICENSE                ← MIT
├── start.ps1              ← Windows launcher (brings up backend + UI together)
├── Start LANTERN.cmd      ← double-click launcher for non-PowerShell users
├── lantern/
│   ├── README.md          ← orients between the two halves
│   ├── api/               ← Python backend on :8099 (see lantern/api/README.md)
│   └── ui/                ← Vite + React frontend on :3000 (see lantern/ui/README.md)
└── archive/               ← Frozen v1 ("Sentinel"), kept for reference (see archive/README.md)
```

---

## Setup

> **Want the full step-by-step including verification at each step?** See [SETUP.md](SETUP.md). The summary below covers the happy path; SETUP.md walks through what to expect, common gotchas, and steady-state usage. If you hit an error, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

### Prerequisites

- **Python 3.11+** ([python.org](https://python.org))
- **Node.js 18+** ([nodejs.org](https://nodejs.org))
- **[Ollama](https://ollama.com/download)** running locally
- A reasonably modern machine. Lantern is built around a 16 GB consumer GPU but the model picker (Settings → Models) lets you swap any task to a smaller model if VRAM is tight.

### Pull at least one model per task

The default config assumes these are installed; the launcher won't fetch them for you. Pull at least the first set; bigger models for cover letters / analysis are nice-to-have.

```bash
# Required for the basic pipeline
ollama pull qwen3:8b               # parse, default fallback, on-demand summary
ollama pull qwen3:14b              # match LLM-fallback (only used if sentence-transformers missing)
ollama pull gemma3:12b             # analyze (fit/gap) + digest prose

# Optional — best quality for the cover-letter feature
ollama pull qwen3:30b-a3b          # cover-letter generation (MoE — fits 16 GB GPU)
```

If you skip the optional model, switch the cover-letter task to `qwen3:14b` in **Settings → Models** after the dashboard is up. The model picker reads what's actually installed via `ollama list` so you can't accidentally point at something that isn't there.

### Launch

```powershell
# Windows
.\start.ps1
# (or double-click Start LANTERN.cmd)
```

```bash
# macOS / Linux
./start.sh
```

The launcher handles all first-run bootstrap automatically:
- Creates a Python venv at `./venv` if missing and installs `lantern/api/requirements.txt`
- Seeds `lantern/api/config.json` from `lantern/api/config.example.json` if no live config exists
- Runs `npm install` in `lantern/ui/` if `node_modules/` is missing
- Starts Ollama (if installed and not already running)
- Spawns the backend on port **8099** and the Vite dev server on port **3000**, then opens the dashboard

Ctrl-C in the launcher window stops everything cleanly.

### First-run flow inside the dashboard

1. **Settings tab → Resume.** Upload your resume (PDF/DOCX). The backend parses it into a structured profile and stamps it into the embedding model.
2. **Settings tab → Titles.** Adjust the role keywords to match what you're looking for.
3. **Settings tab → Location.** Drop pins on the metros you'd actually work from (defaults to no pins so first run shows everything globally).
4. **Settings tab → Models.** Confirm the model picks for each task work for your hardware. The dropdowns list what `ollama list` sees.
5. **Save.** One button at the bottom of Settings persists everything in parallel.
6. **Click Run Pipeline** in the header. First cycle takes 3–5 minutes; subsequent cycles only score new postings.

### Personal config stays local

`lantern/api/config.json` (your live config — could include a Discord webhook, your tuned thresholds, your location pins) is **gitignored**. The repo ships a sanitized `config.example.json` instead and the launcher seeds your local copy from that on first run. Pulling repo updates won't clobber your settings.

---

## License

**MIT** — see [LICENSE](LICENSE).

In plain English: do whatever you want with this code. Use it personally, fork it, modify it, build a commercial product on top of it, ship it inside your company's stack — all fine. The only requirement is that you keep the copyright notice intact in the source.

The MIT license is permissive on purpose. I'm publishing this as a portfolio piece and a tool other job-seekers can adopt; the value is in *running* it, not in restricting who can. If you build something interesting on top, I'd love to hear about it — [LinkedIn](https://www.linkedin.com/in/edwardbaumel/).

---

## Why this is on my GitHub

I'm a Senior PM looking for AI-platform / developer-tools roles. This project is what I do when I have an idea that won't leave my head. It demonstrates: product judgment (cut a dozen features I'd built before realising they didn't earn their place), technical depth (local LLM + embeddings + scoring math + scrape resilience), and shipping discipline (3 tabs, every knob earns its space, no engagement-bait UI).

If your team builds tools where the user is the product, not the data, I want to talk.

— Eddie

---

## Continue from your phone (Cloud Agents)

Desktop Agent chats stay on your PC. To pick up doc/test/refactor work on iPhone:

1. Push this repo to GitHub (see [AGENTS.md](AGENTS.md))
2. Open [cursor.com/agents](https://cursor.com/agents) in Safari → Add to Home Screen
3. Start a **Cloud** agent against the repo

Full pipeline runs still need Ollama on a local machine. See [AGENTS.md](AGENTS.md) for what cloud vs local can do.

---

## GitHub vs a portfolio site

**Ship GitHub first.** For AI/platform PM roles, hiring loops check repos for code, tests and README quality. This README is designed to stand alone.

A separate portfolio page helps when you need a 30-second product story with a demo GIF — link it from your resume, but treat GitHub as the source of truth. Data from developer hiring surveys consistently ranks public repos and README clarity above custom portfolio sites for technical credibility.
