# Agent guide — Local Recruiting Ops

> **Machine layout:** read `C:\Users\zonka\dev\AGENTS.md` first if you need paths outside this repo.

Instructions for Cursor Cloud Agents and other coding agents working on this repo.

## What this project is

**Local Recruiting Ops** is a local-first recruiting ops pipeline: scrape public ATS feeds → parse with a small local LLM → score against your resume with embeddings → flag ghost jobs → surface matches in a 3-tab dashboard. Built as a portfolio piece demonstrating multi-agent orchestration, local LLM economics and product judgment.

v1 was called Sentinel (`archive/`). Local Recruiting Ops is the active codebase.

## What you can do in the cloud

Safe without Ollama or a GPU:

- README, SETUP, TROUBLESHOOTING, doc fixes
- TypeScript UI changes (`lro/ui/`)
- Python unit tests (`cd lro/api && pytest`)
- Vitest (`cd lro/ui && npm test`)
- Refactors, dead-code removal, lint fixes
- `.github/workflows` CI that runs pytest + vitest only

## What requires Eddie's local PC

- Full ingest/match/analyse cycles (`POST /api/run-cycle`)
- Ollama model pulls and VRAM tuning
- Sentence-transformers embedding path smoke tests
- End-to-end verification against live ATS APIs

Do not claim E2E pipeline success without runtime evidence on a machine with Ollama running.

## Repo layout

```
lro/api/     Python backend (orchestrator, agents/, core/, server.py)
lro/ui/      React frontend
archive/         Frozen Sentinel v1 — reference only, do not port backward
start.ps1        Windows dev launcher
scripts/         verify-canonical-repo.ps1 — fail fast if wrong folder
                 benchmark_models.py — optional Ollama A/B for model picks (see README)
```

## Canonical workspace (avoid duplicate copies)

**Open this repo in Cursor:** `C:\Users\zonka\dev\projects\local-recruiting-ops`

Do **not** use `C:\Users\zonka\AI_recruiter` — it is a stale partial copy (no `.git`, no `server.py`). Agents editing there caused fixes to land in the wrong tree. Run `.\scripts\verify-canonical-repo.ps1` before `start.ps1` or when unsure.

## Conventions

- British English in user-facing copy
- State files: always write via `core/io_safe.py`
- Never commit `lro/api/config.json` or `lro/api/data/` (gitignored PII)
- `config.example.json` is the sanitised template
- Do not use AskQuestion — proceed with reasonable assumptions
- Minimal diffs; match existing style

## Running tests (cloud-friendly)

```bash
cd lro/api
pip install -r requirements.txt -r requirements-dev.txt
pytest

cd ../ui
npm ci
npm test
```

## Continue this work from your phone

1. Push this repo to GitHub (see below if not done yet)
2. On iPhone: open [cursor.com/agents](https://cursor.com/agents) → sign in → Add to Home Screen
3. New agent → select **Cloud** → point at the GitHub repo
4. Paste a task, e.g. "Run pytest and vitest, fix any failures" or "Polish README for recruiters"

Desktop Agent chats do **not** sync to mobile. Cloud Agents on cursor.com/agents do.

## GitHub vs portfolio site

For technical and PM hiring loops, **GitHub is the priority**:

- Recruiters and hiring managers check GitHub for code quality and shipping evidence
- A strong README (this repo has one) beats an empty profile
- Optional: a one-page site (e.g. eddy.dev/local-recruiting-ops) that embeds a demo GIF and links to the repo — useful for PM roles where you need a product narrative in 30 seconds

Recommended: ship GitHub first, link it from resume and LinkedIn. Add a portfolio page only if you want a curated story beyond what README provides.

## First-time GitHub push

```powershell
cd c:\Users\zonka\dev\projects\local-recruiting-ops
git init
git add .
git commit -m "Initial public release of Local Recruiting Ops"
gh repo create lro --public --source=. --push
```

Replace `lro` with your chosen repo name. Use a private repo if you prefer; Cloud Agents work with either.

## Suggested cloud-agent task queue

1. Ensure pytest + vitest pass in CI
2. Scan for stale Sentinel references in active code/docs
3. Add a 30-second screen recording GIF to README (local task — agent can prep markdown placeholder)
4. Review `archive/` size — confirm gitignore excludes runtime data and node_modules
