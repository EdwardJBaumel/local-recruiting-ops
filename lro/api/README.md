# lro/api — Python backend

The pipeline. Scrapes ATS feeds, parses raw HTML / JSON into structured jobs, scores each posting against the user's resume with embeddings + soft adjustments, runs ghost detection, and persists everything as atomic JSON.

Runs on **port 8099** (the UI's Vite dev server proxies `/api/*` here). Single Python process; no DB, no message broker, no framework. The whole HTTP layer is `http.server` from the stdlib.

## Architecture at a glance

```
   ┌────────────┐    ┌────────────┐    ┌────────────┐    ┌────────────┐
 → │  INGEST    │ →  │   PARSE    │ →  │   MATCH    │ →  │  ANALYZE   │ →  persist
   │ scrapers   │    │ HTML→JSON  │    │ embed +    │    │ fit/gap    │
   │            │    │ via LLM    │    │ ghost fold │    │ rationale  │
   └────────────┘    └────────────┘    └────────────┘    └────────────┘
        ATS                qwen3            bge-m3 +         phi4-
        APIs               :14b             qwen3:14b        reasoning:14b
```

A "cycle" walks the four stages once. Triggered by `POST /api/run-cycle` from the UI's "Run Pipeline" button (or from the orchestrator's auto-loop, which is OFF by default in `config.json`).

## Layout

```
api/
├── main.py              ← thin entrypoint — boots config, the orchestrator, the HTTP server
├── server.py            ← all HTTP handlers (~1500 lines, single file by design — easy to grep)
├── orchestrator.py      ← cycle runner; threads stages together; tracks progress for /api/status
├── config.json          ← user-editable runtime config (ATS tenants, models, scoring weights, prefs)
├── requirements.txt
│
├── agents/              ← one module per pipeline stage
│   ├── ingest.py        ← all scrapers (Greenhouse, Lever, Ashby, Workday, Amazon, Google, RemoteOK, Jobicy, Remotive)
│   ├── parse.py         ← LLM-based HTML→JSON extraction with circuit-breaker
│   ├── match.py         ← embedding similarity + title/location/salary/years adjustments + ghost fold
│   ├── analyzer.py      ← fit/gap rationale on top-N matches
│   ├── archetype.py     ← "what kind of PM role is this" classifier
│   ├── fakejob.py       ← compose ghost-detection signals onto packets (calls into core/fake_detector)
│   ├── qa.py            ← post-parse sanitiser
│   ├── resume.py        ← resume profile parsing
│   └── playwright_runner.py  ← kept ONLY for HTML→PDF rendering (resume export). SPA scraping plumbing was removed.
│
├── core/                ← shared utilities, no pipeline state
│   ├── fake_detector.py ← the 9-signal ghost-job composer (per-signal severity caps, calibration)
│   ├── resume_profile.py← profile cache + user-override merge
│   ├── llm.py           ← Ollama client + model-fallback chain
│   ├── scraper_session.py ← realistic UA pool + polite_get/polite_post + jittered backoff
│   ├── io_safe.py       ← per-path-locking atomic JSON write
│   └── (market aggregates written by orchestrator._save_market_intel → data/market_intel.json)
│   ├── match_registry.py← URL-hash + (company, title, location) dedupe across cycles
│   └── …
│
├── data/                ← runtime state (JSON files; gitignored)
│   ├── config.json
│   ├── match_registry.json
│   ├── market_intel.json
│   ├── ingest_sources.json
│   └── cover_letters/
│
└── logs/lro.log     ← rolling log
```

## Model assignments

Every stage routes to the smallest model that handles it cleanly. All run locally via Ollama.

| Stage             | Model              | Why                                                 |
|-------------------|--------------------|-----------------------------------------------------|
| Parse             | `qwen3:8b`         | Structured HTML-to-JSON extraction. 8B is plenty for mechanical extraction; 14B doesn't move accuracy. |
| Match             | `qwen3:14b`        | LLM fallback when sentence-transformers unavailable |
| Analyze           | `gemma3:12b`       | Fit-gap rationale. Was phi4-reasoning:14b — its chain-of-thought added 20-30s per call without a quality lift on PM JDs. |
| Digest            | `gemma3:12b`       | Cheap prose generation over structured counts       |
| Cover letter      | `qwen3:30b-a3b`    | MoE: 30B params total, 3B active. Quality lift over 12B-class for the prose deliverable, still fits a 16 GB GPU. |

Matching itself prefers the embedding path (`BAAI/bge-m3` cosine similarity) for speed and determinism. The LLM fallback only fires when sentence-transformers isn't installed (e.g. a packaged build that excludes it).

## Data sources (the scrape list)

Public APIs only. **No headless browsers, no proxy rotation, no stealth tooling.** Every scraper here either hits a public structured feed or a documented JSON endpoint that the company's own career page uses to render itself.

- **Greenhouse**: every tenant in `config.json:ingest.greenhouse_companies`. JSON.
- **Lever**: every tenant in `config.json:ingest.lever_companies`. JSON.
- **Ashby**: every `(display_name, slug)` pair in `config.json:ingest.ashby_companies`. JSON.
- **Amazon**: `amazon.jobs/.../search.json`, paginated.
- **Google**: page 1 of `google.com/about/careers/applications/jobs/results` per keyword. **Single page only** — Google's robots.txt explicitly disallows the paginated URL pattern, and we respect that.
- **Workday tenants**: Nvidia, Adobe, Salesforce, IBM, Cisco, Intel — generic scraper hits the standardised `/wday/cxs/{tenant}/{board}/jobs` endpoint. One scraper, six (or more) companies.
- **Free job boards**: RemoteOK, Jobicy, Remotive — public JSON, no auth.

### What was deliberately NOT built (or removed)

- **Apple** — site TOS bans automated access.
- **Meta** — robots.txt blocks ClaudeBot and every named AI bot.
- **Microsoft** — service agreement prohibits scraping.
- **Tesla / Oracle** — same family of restrictions.
- **Netflix** — moved to a JS-only Phenom platform with no clean public API; their old `/api/search` endpoint is dead and their Lever board has zero active postings. The fetcher is shelved (`enable_netflix=false` in default config) until Netflix exposes a public structured feed again.

The Playwright SPA scraper that powered some of these in v1 was removed entirely from this codebase — there's no remaining caller and shipping unused anti-bot tooling sends the wrong signal in a public repo. The `agents/playwright_runner.py` file is kept ONLY for HTML→PDF rendering of resume exports.

## Where to look for specific things

| What | Where |
|---|---|
| Ghost-detection signals + severity caps | `core/fake_detector.py` |
| Per-tier freshness windows (UI side) | `lro/ui/src/lib/companyTier.ts` |
| Match scoring math | `agents/match.py` |
| Cover letter prompt + endpoint | `server.py` (search `/api/cover-letter`) |
| Rate-limit handling + UA rotation | `core/scraper_session.py` |
| Atomic JSON writes (Windows-safe) | `core/io_safe.py` |
| Dead-slug cooldown | `agents/ingest.py` (search `dead_slug`) |

## Configuration

All runtime config lives in `config.json` next to `server.py`. The UI's Settings tab edits it via `POST /api/config`; you can also hand-edit and the server picks up changes on the next request that reads it.

Key sections:
- `ingest.role_keywords` — the search terms used by every scraper that supports keyword filtering
- `ingest.greenhouse_companies` / `lever_companies` / `ashby_companies` — the tenant lists
- `ingest.enable_*` — per-source toggles for the bespoke scrapers
- `match.threshold` — minimum embedding similarity to surface
- `fake_detection.ghost_weight` — how hard the ghost penalty bites the final score
- `preferences.location_pin_areas` — geographic pins
- `preferences.freshness_window_*_days` — per company-size tier
- `pipeline.auto_start` — set to `true` to run cycles on a fixed cadence; default `false` (manual via Run Pipeline button only)

## Local resource budget

| Resource | Cost                                                                |
|----------|---------------------------------------------------------------------|
| VRAM     | ~6 GB resident (qwen3:8b prewarm); peak ~11 GB during analyze       |
| RAM      | ~2 GB Python, ~1 GB embedding model                                 |
| Disk     | ~25 GB models, ~50 MB runtime state per month of operation          |
| Network  | Per cycle: ~2 MB ATS JSON, ~500 KB HTML scrape, **0 bytes to any LLM** |
| Tokens   | 0 cloud tokens. Everything inferred locally.                        |

## License

MIT — see [/LICENSE](../../LICENSE) at the repo root.
