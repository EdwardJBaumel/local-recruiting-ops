---
title: Lantern
description: Local-first job intelligence — multi-agent pipeline, zero API cost
---

# Lantern

**Local-first job intelligence — designed and shipped by [Eddie Baumel](https://www.linkedin.com/in/edwardbaumel/).**

Lantern ingests ~1,000 job postings per cycle from public ATS feeds, scores them against your resume with local embeddings, flags ghost listings with explainable heuristics, and surfaces matches in a four-tab dashboard. No cloud API. No resume upload to a third party.

Built while job-searching in early 2026 — because aggregators optimise for engagement and every AI job tool wanted my CV on their servers.

**[View source on GitHub](https://github.com/edwardjbaumel/lantern)** · **[Full README](https://github.com/edwardjbaumel/lantern/blob/master/README.md)** · **[Setup guide](https://github.com/edwardjbaumel/lantern/blob/master/SETUP.md)**

---

## The problem

| Pain | What Lantern does |
|------|-------------------|
| Ghost jobs clogging the funnel | Nine deterministic signals → ghost score with inspectable reasons |
| Opaque "92% match" black boxes | Tunable weights and per-dimension breakdown |
| Privacy | Ollama + sentence-transformers on your hardware; PII stays local |
| Slow, empty UI during long cycles | Two-pass match → first results in ~3 minutes on a 16 GB GPU |

---

## What this demonstrates

**Product** — Scoped v1 down to a shippable v2; cut auto-apply, cloud hosting and TOS-violating scrapers before public release.

**AI / agents** — Orchestrated ingest → parse → match → ghost-fold → analyse pipeline with task-specific local models and embedding-first scoring.

**Engineering** — Python backend, React/TS frontend, 256 automated tests, CI, atomic JSON persistence, frozen v1 beside v2 in the repo.

---

## Numbers

- **~900–1,000** postings ingested per cycle
- **~3 min** time-to-first-match (GPU) · **~8–12 min** CPU-only
- **$0** per cycle vs hosted LLM APIs at the same volume
- **190** pytest · **66** vitest

---

## Architecture

```
ATS feeds → INGEST → MATCH (fast pass, embeddings)
                  ↘ PARSE (HTML only) → MATCH (slow pass)
                  → GHOST-FOLD → ANALYZE (top N) → registry + history + dashboard
```

Stack: Python · Ollama · `BAAI/bge-m3` · Vite · React · TypeScript

---

## Try it

Requires Python 3.11+, Node 18+, Ollama and two model pulls (~14 GB disk).

```powershell
git clone https://github.com/edwardjbaumel/lantern.git
cd lantern
ollama pull qwen3:8b
ollama pull qwen3:14b
.\start.ps1
```

Opens [http://127.0.0.1:8099](http://127.0.0.1:8099) — upload a resume in Settings, then Run Pipeline.

---

## About the author

Senior PM targeting AI platform and developer-tools roles. I built Lantern because the job market is a black box and I wanted a tool where **the user is not the product**.

**[LinkedIn](https://www.linkedin.com/in/edwardbaumel/)** · **[GitHub repo](https://github.com/edwardjbaumel/lantern)** · **MIT License**
