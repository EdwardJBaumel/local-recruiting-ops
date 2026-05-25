# Lantern

**Local-first job intelligence — designed, built and shipped by [Eddie Baumel](https://www.linkedin.com/in/edwardbaumel/).**

Lantern is a multi-agent pipeline that ingests ~1,000 job postings per cycle from public ATS feeds, scores them against your resume with local embeddings, flags ghost listings with explainable heuristics, and surfaces the best matches in a three-tab dashboard. No cloud API. No resume upload to a third party. Zero per-cycle API cost.

Built while job-searching in early 2026 because every aggregator optimises for engagement and every AI job tool wanted my CV on their servers.

---

## The problem I solved

| Pain | What Lantern does |
|------|-------------------|
| Ghost jobs clogging the funnel | Nine deterministic signals → 0–100 ghost score with reasons you can inspect |
| Opaque "92% match" black boxes | Tunable weights, calibrated display scores, per-dimension breakdown |
| Privacy | Ollama + sentence-transformers on your hardware; PII stays in gitignored `data/` |
| Slow, empty UI during long cycles | Two-pass match + chunked embeddings → first results in ~3 minutes |

---

## What this demonstrates (for hiring)

**Product**

- Scoped v1 ("Sentinel", in `archive/`) down to a shippable v2 with three tabs and no engagement bait
- Cut auto-apply, cloud hosting, TOS-violating scrapers and a dozen half-used features before public release
- Per-tier freshness windows, geographic pin filter, lazy on-demand LLM summaries (1% of the compute cost)

**AI / agents**

- Orchestrated pipeline: ingest → parse → match → ghost-fold → analyse → persist
- Model routing by task (8B parse, 14B fallback, 12B analyse, 30B MoE cover letters)
- Embedding-first matching for determinism; LLM reserved for structure extraction and prose
- Feedback learner from starred/dismissed roles once ≥3 samples exist

**Engineering**

- Python backend (stdlib HTTP + threaded orchestrator), React/TS frontend, 256 automated tests
- Atomic JSON persistence with per-path locking on Windows
- CI on push (pytest + vitest)
- Honest docs: engineering trade-offs, what I deliberately did *not* build, frozen v1 beside v2

---

## Numbers that matter

- **~900–1,000** raw postings ingested per cycle (Greenhouse, Lever, Ashby, Amazon, Google page 1, Workday, RemoteOK, Jobicy)
- **~3 min** time-to-first-match on a 16 GB consumer GPU; **~8–12 min** CPU-only
- **$0** per cycle vs ~$3–25 on hosted LLM APIs at the same volume
- **190** backend unit tests · **66** frontend tests
- **0** OpenAI/Anthropic keys required

---

## Architecture (one glance)

```
ATS feeds → INGEST → MATCH (fast pass, embeddings)
                  ↘ PARSE (HTML only) → MATCH (slow pass)
                  → GHOST-FOLD → ANALYZE (top N) → JSON registry + dashboard
```

Stack: Python · Ollama · `BAAI/bge-m3` · Vite · React · TypeScript · Tailwind · shadcn/ui

---

## v1 → v2 story

The repo includes **`archive/sentinel/`** — the original 7k-line single-file React app and monolithic backend. Lantern is the rewrite: split UI, typed frontend, trimmed scope, tests, TOS-respectful scrape list. Keeping both side by side is deliberate; it shows iteration, not a one-shot demo.

---

## Run it locally

Full setup: [SETUP.md](../SETUP.md) in the repo root.

```powershell
git clone https://github.com/YOUR_USERNAME/lantern.git
cd lantern
.\start.ps1
```

Requires Python 3.11+, Node 18+, [Ollama](https://ollama.com/download) with at least `qwen3:8b` and `gemma3:12b`.

---

## About the author

Senior PM targeting AI platform and developer-tools roles. I built Lantern because the job market is a black box and I wanted a tool where **the user is not the product**.

**[LinkedIn](https://www.linkedin.com/in/edwardbaumel/)** · **[Full README & technical deep-dive](../README.md)** · **MIT License**

If your team cares about local-first AI, explainable scoring and shipping over slide decks — I'd like to talk.
