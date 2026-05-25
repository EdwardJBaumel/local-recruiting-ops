# LANTERN — Claude Code briefing

Local-first job-intelligence system. Python backend on port 8099, Vite + React + TypeScript frontend on port 3000 in dev. Zero API costs — everything runs on the user's machine via Ollama and sentence-transformers.

## Paths

- Backend: `lantern/api/` (Python, API on :8099)
- Frontend: `lantern/ui/` (Vite + React + TS, dev :3000)
- Launchers: `start.ps1` / `start.sh`, `Start LANTERN.cmd`
- Frozen v1 reference: `archive/sentinel/` and `archive/sentinel-ui/` (do not extend)
- Data: `lantern/api/data/` — match registry, decisions, resume, market intel (gitignored)

## User preferences

British English. No Oxford commas. No em dashes. Metric measurements only. Maximum conciseness. Never use AskQuestion — make reasonable assumptions and proceed. Audit local-resource cost (VRAM, CPU, RAM, network, LLM tokens) on every change.

## Tech stack

- Embeddings: `BAAI/bge-m3`, cosine similarity
- Ollama models per `config.json`: parse `qwen3:8b`, match LLM fallback `qwen3:14b`, analyze/digest `gemma3:12b`, cover letter `qwen3:30b-a3b`
- Atomic JSON writes via `core/io_safe.py` (never raw `write_text` for state)
- Match registry keyed by dedupe_key at `core/match_registry.py`
- Feedback learner at `core/feedback_learner.py` — starred/dismissed embeddings adjust scores once ≥3 samples
- Ghost detection: `core/fake_detector.py` (9 signals, `GHOST_SUSPECT_THRESHOLD = 0.45`)
- Frontend: Vite, React 18, TypeScript, Tailwind, shadcn/ui, Zustand + TanStack Query

## Locked product decisions

a. Location = hard filter (drop if violated). Salary = soft scoring weight.
b. Experience: hard-drop on level gap ≥3, years gap ≥8, or Director/VP/CXO trap-door when user <10 years.
c. Thumbs up/down = tag-only log. Starred/dismissed feed the feedback learner.
d. Match threshold 0.45 raw. Ghost suspect threshold 0.45 is a different axis.
e. No auto-apply, no cloud version, no TOS-violating scrapers.

## Key files

- `lantern/api/orchestrator.py` — pipeline driver
- `lantern/api/agents/match.py` — embedding + scoring + calibration
- `lantern/api/agents/ingest.py` — ATS fetchers, dead-slug tracking
- `lantern/api/server.py` — HTTP API
- `lantern/ui/src/views/` — Brief, Matches, Settings tabs

## Cloud agents

Docs/tests/README work in Cursor Cloud Agents. Full pipeline cycles need Ollama + GPU on a local machine. See `AGENTS.md`.
