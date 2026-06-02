# Changelog

All notable changes to Local Recruiting Ops are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.2.0] — 2026-05-26

Lean match path and faster cycles: skip dead LLM work when Ollama models are missing, cap the Matches tab at 80 rows, and keep Google off by default.

### Added

- **Match list cap** — after scoring, only the top **80** jobs stay in match tier (`match.match_list_cap` in `config.example.json`)
- **`llm.task_llm_ready()`** — parse, archetype and fit-gap stages skip cleanly when no model is pulled (one log line, no per-row 404 spam)
- **Auto-pull `qwen3:8b`** on launch when Ollama is up and the model is missing (`start.ps1`; set `LRO_SKIP_MODEL_PULL=1` to skip)
- **`scripts/verify-canonical-repo.ps1`** — launcher fails fast if you are not in the git repo (guards against editing the stale `AI_recruiter` copy)
- **Match filter module** — `matchFilters.ts`, `useFilteredMatches.ts` and vitest coverage
- **pytest** — `test_llm_ready.py`, `test_match_list_cap.py` (223 tests total)

### Changed

- **Default scoring path** — tighter embed tiers (**0.40 / 0.28**), cross-encoder rerank **off**, analyse **top 8** with `qwen3:8b` (`config.example.json`)
- **Google ingest** — **off by default** (each card is a separate LLM parse call; ATS sources need no parse step)
- **Matches tab** — registry and `/api/matches` serve **match tier only**; maybe-tier rows no longer flood the UI (starred rows you already touched are kept)
- **Fit-gap analyser** — shorter prompt: one-sentence verdict + up to three matched/missing skills
- **ARCHITECTURE.md** — documents the lean path as default; rerank is optional
- **AGENTS.md** — canonical workspace path and stale-folder warning
- **Settings → Companies** — Google toggle note warns about parse cost

### Fixed

- **Brief match rate** — early-exit cycles (all URLs already seen) now record funnel stats; ingested count uses fetched total not post-dedupe zero
- **Start Local Recruiting Ops.cmd** — pauses on failure so the window stays open when launch errors
- **History.tsx** — removed unused import (TS6133)

### Upgrade notes (1.1.x → 1.2.0)

Existing installs keep your gitignored `config.json`. To pick up lean defaults, merge from `config.example.json`:

- `match.match_list_cap`: **80**
- `match.tiers.embed.match` / `maybe`: **0.40** / **0.28**
- `match.cross_encoder.enabled`: **false**
- `analyze.top_n`: **8**, `analyze.model`: **`qwen3:8b`**
- `ingest.enable_google`: **false** (optional — turn on only after `ollama pull qwen3:8b`)

Then run **Reset data** once if the Matches tab still shows hundreds of stale rows from old tier settings.

---

## [1.1.0] — 2026-05-25

### Added

- **Brief → Match rate** tile (`matches ÷ ingested` on the last cycle)
- **Last cycle** panel with plain labels (Fetched, Ghosts removed, Matched your profile, New listings, Top matches analysed)
- **Local Recruiting Ops mark** in the header (replaces the floating middot)
- Atmospheric background and card depth on the dashboard shell
- **CHANGELOG.md** and GitHub release notes

### Changed

- **README** and **SETUP.md** — launch-first flow, hardware-aware model picks (`qwen3:4b` / `8b` / `14b`)
- Brief metric strip order: Registry → Match rate → Ghost rate → Cycles run → timing tiles
- Removed recruiter pitch from README and trimmed GitHub Pages copy to product-only

### Fixed

- Salary chart **Your target** label clipped at the top of the chart
- **SETUP.md** model table (removed non-existent Chat task; defaults match `config.example.json`)
- Run Pipeline button flicker during cycle start
- Reverted a short-lived muted palette experiment — restored high-contrast WCAG-friendly tokens

### Security

- Removed `docs/resume-bullets.md` from the public repo and scrubbed it from git history
- Gitignored local-only files: `docs/resume-bullets.md`, `docs/cursor-models.md`, `push-to-github.ps1`, `archive/sentinel/config.json`
- Runtime PII paths remain gitignored: `lro/api/config.json`, `lro/api/data/`, logs

---

## [1.0.0] — 2026-05-25

Initial public release on GitHub.

### Added

- Multi-agent pipeline: ingest → parse → match → ghost detection → analyse
- React dashboard: **Brief**, **Matches**, **History**, **Settings**
- Local embeddings (`BAAI/bge-m3`) + Ollama task routing
- Single-port launcher (`start.ps1` / `start.sh` → `:8099`)
- pytest (190) + vitest (66) + GitHub Actions CI
- Frozen v1 reference under `archive/sentinel/`

### Notes

- Personal config and match data are created on first run and never committed
- Copy `config.example.json` → `config.json` locally; edit via Settings or the file directly
