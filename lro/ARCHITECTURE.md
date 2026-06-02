# Local Recruiting Ops architecture decisions

Interview-ready record of why the system is shaped the way it is. Local-first recruiting ops: scrape public ATS feeds, score against your resume, flag ghost jobs, surface matches in a three-tab dashboard.

## Scoring: lean path (default)

| Stage | What | Purpose |
|-------|------|---------|
| **Hard filters** | Location, country, experience, blocked titles | Drop wrong jobs before any ML |
| **Bi-encoder** | `BAAI/bge-m3` on all survivors | Cosine fit vs resume |
| **Ghost fold** | 9-signal detector × configurable weight | Penalise stale/suspicious postings |
| **Feedback learner** | Star/dismiss embeddings (≥3 samples) | Personalise toward your taste |
| **Match tier + cap** | Raw ≥0.40 embed + top **80** stay "match" | Shortlist, not 800 rows |
| **Analyze LLM** | `qwen3:8b` on top **8** only | One-sentence "why fit / gap" in UI |

**Cross-encoder rerank** is optional (`match.cross_encoder.enabled`, default **off**). Enable for sharper ordering on top-N; costs model download + cycle time. Not required for the product story.

**Removed from score path:** ProfileFit penalties, title-keyword boosts, maybe-tier rows in the registry.

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
cd lro/api
python scripts/spike_cross_encoder.py --pairs 50
```

Compares embedding-only vs cross-encoder separation on your starred vs dismissed set. Run after you have ≥3 of each label.

## Tradeoffs summary (talk track)

1. **Local-first** — zero API cost, privacy, recruiter-demoable on your laptop; tradeoff is you own GPU/VRAM tuning
2. **Hybrid retrieval** — bi-encoder recall + cross-encoder precision beats either alone or heuristic penalty stacks
3. **Incremental UX** — two-pass match + streaming registry beats monolithic batch for perceived speed
4. **JSON state** — simple, debuggable, gitignored PII; not built for 100k-job scale
5. **Trimmed feature set** — no auto-apply, no cloud, no TOS-violating scrapers; portfolio clarity over feature count
