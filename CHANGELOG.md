# Changelog

All notable changes to Lantern are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] — 2026-05-25

### Added

- **Brief → Match rate** tile (`matches ÷ ingested` on the last cycle)
- **Last cycle** panel with plain labels (Fetched, Ghosts removed, Matched your profile, New listings, Top matches analysed)
- **Lantern mark** in the header (replaces the floating middot)
- Atmospheric background and card depth on the dashboard shell

### Changed

- **README** and **SETUP.md** — launch-first flow, hardware-aware model picks (`qwen3:4b` / `8b` / `14b`)
- Brief metric strip order: Registry → Match rate → Ghost rate → Cycles run → timing tiles
- Removed recruiter pitch section from README (product docs only)

### Fixed

- Salary chart **Your target** label clipped at the top of the chart
- **SETUP.md** model table (removed non-existent Chat task; defaults match `config.example.json`)
- Run Pipeline button flicker during cycle start
- Reverted a short-lived muted palette experiment — restored high-contrast WCAG-friendly tokens

### Security

- Removed `docs/resume-bullets.md` from the public repo and scrubbed it from git history
- Gitignored local-only files: `docs/resume-bullets.md`, `docs/cursor-models.md`, `push-to-github.ps1`
- Runtime PII paths remain gitignored: `lantern/api/config.json`, `lantern/api/data/`, logs

---

## [0.1.0] — 2026-05-25

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
