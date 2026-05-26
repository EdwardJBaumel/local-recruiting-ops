# Lantern architecture decisions

Interview-ready record of why the system is shaped the way it is. Local-first job intelligence: scrape public ATS feeds, score against your resume, flag ghost jobs, surface matches in a three-tab dashboard.

## Scoring: embed wide, cross-encode narrow, LLM explain smallest

Three tiers of compute cost and precision:

| Stage | Model | Scope | Input size | Purpose |
|-------|--------|-------|------------|---------|
| **Bi-encoder** | `BAAI/bge-m3` | All jobs passing hard filters | ~1,500 char JD slice | Fast cosine recall — cast a wide net |
| **Cross-encoder** | `BAAI/bge-reranker-v2-m3` | Top 60 by embed score | ~150 tokens (`job_signature`) | Pairwise rerank — precision on shortlist |
| **Fit-gap LLM** | `qwen3:8b` (Ollama) | Top 10 matches | Profile + job signature | Human-readable gaps for UI only |

**Why not stack more heuristics?** ProfileFit and title-keyword boosts were fighting the embedding model. Cosine similarity bunches every PM-ish JD into 0.42–0.58; adding `-0.22 seniority` and `+0.20 title boost` penalties produced brittle, hard-to-debug scores (e.g. ScaleAI at 24%, Group PM at 100%). Cross-encoders are trained for relevance ranking and separate starred from dismissed pairs measurably better than another `preferences.py` rule.

**What we removed from the score path:**
- `ProfileFitScorer.adjust()` — dimensional scores (`seniority_fit`, `lane_fit`, etc.) remain on the payload for UI badges only
- Title keyword **boosts** — blocked-discipline titles still hard-penalise (`engineer`, `designer`, …)
- Full JD in analyze prompt — replaced with `job_signature`

**What stays as user-controlled soft/hard gates:**
- Location, country, experience (hard filters)
- Salary, location preference (soft weights)
- Ghost score (multiplicative fold into final score)
- Feedback learner (embedding nudge from starred/dismissed, ≥3 samples)

## Job signatures

Built at ingest/parse time in `core/job_signature.py` — deterministic, no extra LLM call.

```
Senior PM, Platform @ Stripe (senior; hybrid; SF) | Stack: k8s, python | Own the developer platform roadmap…
```

~600 chars (~150 tokens). Extracts responsibilities/requirements sections; drops "About us", EEO, benefits boilerplate. Same trimming philosophy as the frontend `jdTrim.ts`, implemented server-side for rerank input.

## Two-pass match + incremental registry

```
INGEST → QA → FAKE → DEDUPE
    ├─ PASS 1: JSON-API jobs → MATCH (matches appear in ~seconds)
    ├─ PARSE: HTML cards (Google) → LLM extract
    ├─ PASS 2: parsed HTML → MATCH
    └─ RERANK: cross-encoder on top-60 → registry upsert
         → ANALYZE top-10 → DIGEST (optional)
```

Pass 1 exists because Greenhouse/Lever/Ashby return structured JSON — no parse LLM needed. The UI polls `match_registry.json`; `on_scored` flushes each row so the Matches tab fills during scoring, not after a 40-minute wall.

## Embeddings and cache

- Model: `BAAI/bge-m3`, GPU when CUDA PyTorch is installed
- Cache: SHA-1 of embedding input text, disk-persisted in `data/embedding_cache.pt`
- Chunked encode: 32 jobs per batch so first matches appear before the full corpus finishes
- Profile embedding computed once per cycle; job embeddings reused across cycles when JD text unchanged

## Ghost detection (separate axis from fit)

Nine deterministic signals in `core/fake_detector.py`. Threshold 0.45 = suspect badge. **Not** the same as match threshold 0.45.

Ghost score folds multiplicatively: `final = fit × (1 − ghost_weight × ghost_score)`. Default `ghost_weight = 0.35`. A 92% fit / 85% ghost posting ranks below an 80% fit / 15% ghost one.

## LLM task routing (Ollama, all local)

| Task | Default model | Rationale |
|------|---------------|-----------|
| Parse | `qwen3:8b` | Mechanical JSON extraction |
| Match fallback | `qwen3:14b` | Only when embeddings unavailable |
| Analyze | `qwen3:8b` | Top-10 one-liner fit-gap; saves VRAM vs 14B |
| Cover letter | `qwen3:14b` | Quality prose; fits 16 GB GPU at q4 |
| Digest | `qwen3:14b` | Shares VRAM with analyze |

Consolidated from an earlier sprawl (gemma3:12b, qwen3:30b-a3b MoE) to reduce Ollama model swapping on 16 GB VRAM.

## Storage (JSON, not SQLite — deferred)

- `match_registry.json` — live union keyed by `dedupe_key`; seen/starred/dismissed survive cycles
- Atomic writes via `core/io_safe.py` (Windows retry on `WinError 5`)
- SQLite migration proposed and deferred: current volume (~1–3k jobs) fits JSON; migration cost > benefit for portfolio scope

## Frontend stack

Vite + React 18 + TypeScript + Tailwind + shadcn/ui. Zustand (UI state), TanStack Query (server state), react-hook-form (settings). StrictMode enabled. No map (removed after Leaflet StrictMode bugs); multi-select location dropdown instead.

Polling is selective: status/matches during cycles, not full-page remounts — so editing settings or reading a JD is not clobbered mid-session.

## TOS and scraping

Public GitHub portfolio — no Apple/Meta/Microsoft/Tesla/Oracle scrapers (TOS risk). Sources: Greenhouse, Lever, Ashby, Workday tenants, Amazon, Google public APIs. Realistic headers/delays via `scraper_session.py`.

## Validating rerank quality

Spike script (no Ollama):

```powershell
cd lantern/api
python scripts/spike_cross_encoder.py --pairs 50
```

Compares embedding-only vs cross-encoder separation on your starred vs dismissed set. Run after you have ≥3 of each label.

## Tradeoffs summary (talk track)

1. **Local-first** — zero API cost, privacy, recruiter-demoable on your laptop; tradeoff is you own GPU/VRAM tuning
2. **Hybrid retrieval** — bi-encoder recall + cross-encoder precision beats either alone or heuristic penalty stacks
3. **Incremental UX** — two-pass match + streaming registry beats monolithic batch for perceived speed
4. **JSON state** — simple, debuggable, gitignored PII; not built for 100k-job scale
5. **Trimmed feature set** — no auto-apply, no cloud, no TOS-violating scrapers; portfolio clarity over feature count
