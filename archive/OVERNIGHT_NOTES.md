# Overnight Action Plan — 2026-04-24 → 25

Kicked off while you sleep. Status per item gets updated in-place as I
ship each one. Order reflects dependencies and risk — the UI plumbing
has to land before the consolidation has anything to move into it, and
the backend Ollama/IO fixes should land before the next pipeline run so
the logs actually come back clean.

---

## 1. Rename "Pipeline" → "Settings" + rightmost tab  ✅ done
- `App.jsx` tab list: re-ordered so the Settings entry is last, label
  reads "Settings". Internal `view === "pipeline"` checks kept
  unchanged so every downstream reference still resolves (no router
  cascade).
- Heading + subcopy in the view body updated to match ("Settings",
  "Everything you can tune: …").

## 2. Consolidate all configurable knobs into Settings  ✅ done
Single Settings tab now carries every knob. Profile tab is now only
resume + notes + a one-liner pointer to Settings.
  - **Scoring & preferences** — keywords, threshold, cadence,
    work-modes, country/location allow/block, salary floor, years,
    weights. Moved from Profile.
  - **Scrapers** — Greenhouse tenants, Lever tenants, Ashby tenants
    (one-per-line parsers), big-tech checkboxes (Apple / Amazon /
    Google / Meta / Microsoft). Moved from Profile.
  - **Ghost-job filter** — aggressiveness preset, penalty weight
    slider, flag/warn threshold sliders. (Already was here; stays.)
  - **Models** — parse / match / analyze / digest / chat / cover-letter
    model selectors. (Already was here.)

## 3. Fix Ollama 404 fallback chain  ✅ done
`core/llm.py` now owns a fallback chain (`qwen3:8b → qwen2.5:7b →
gemma3:4b → llama3.2:3b`). `query()` resolves the requested model
up-front against `/api/tags`; on 404 it marks the requested name as
missing, picks the first locally-available candidate, memoises the
substitution for the rest of the process lifetime, and retries once.
`get_effective_models()` exposes the current substitution map.

`/api/status` now returns `model_fallback: { missing, substitutes }`.
Brief tab renders a banner listing every `missing → sub` pair with an
`ollama pull <model>` hint, only when the substitutes list is
non-empty (so a healthy Ollama shows nothing).

## 4. Fix Windows `match_registry.json.tmp` race  ✅ done
`core/io_safe.py` now uses per-writer unique tmp names
(`<path>.<pid>.<uuid4[:8]>.tmp`) and a per-destination `threading.Lock`.
`_replace_with_retry` catches both `PermissionError` (retries with
exponential backoff out to ~1.3 s) and `FileNotFoundError` (logs and
gives up — another writer beat us to it, which is safe).

Also added `_as_path()` coercion so both `Path` and `str` callers
work. Verified with a 6-thread × 20-write stress test against the
same destination — no races, no crumbs left behind.

## 5. Age filter column in Matches + Blitz sort by adjusted score  ✅
  - Window selector now offers "Last 48 hours" and "Last 3 days" in
    addition to the existing spans, with a tooltip explaining the
    "Posted in the last N days" semantics.
  - `triageQueue` useMemo now sorts the Blitz queue by
    `displayScoreOf(m)` (ghost-folded `final` score) descending, with
    `posted_at` as a tiebreaker. Blitz now surfaces the best adjusted
    match first instead of whichever cursor order the match agent
    emitted.

## 6. Split avg-cycle metric  ✅ done
  - Orchestrator records `ingest_seconds` (just scraping) and
    `pipeline_seconds` (everything after scrape) into
    `cycle_times.json` per cycle.
  - `/api/status` averages the last 10 of each into
    `avg_scrape_seconds` and `avg_pipeline_seconds`.
  - Brief tab metric strip went from 4 columns to 5: Matches, Verified,
    Avg scrape, Avg pipeline, Match latency — each with a tooltip.

## 7. Learning: tracker-outcome signals feed score  ✅ done
`ApplicationTracker.company_signals()` computes a per-company delta
from your funnel history (`applied/responded/interview/offer` boost,
`rejected/passed` penalise) and squashes it through a tanh into a
bounded multiplier in ±0.08. `MatchAgent.set_company_signals()` clamps
and stores the map; the scoring path applies `final = base × (1 +
bonus)` after the ghost fold, tracking `_match_score_pre_learned` and
`_learned_bonus` on the payload for debuggability.

Orchestrator calls `self.match.set_company_signals(self.tracker.
company_signals())` at the start of the MATCH stage, so every cycle's
scoring reflects the latest funnel state. `export_for_dashboard()`
surfaces the signals to the UI for later visualisation.

## 8. Vite build + backend smoke test  ✅ done
  - `npm run build` in `sentinel-ui` — 830 modules transformed, 1.46 s,
    no warnings beyond the pre-existing 500 kB-chunk notice.
  - Module-import smoke: `server`, `orchestrator`, `match`, `llm`,
    `io_safe`, `tracker` all import clean from a bare interpreter.
  - Unit smoke: `tracker.company_signals()` returns 69 companies;
    `MatchAgent.set_company_signals` accepts and clamps; `io_safe`
    handles str + Path callers; concurrent writers are race-free.
  - Live smoke: killed the stale process on :8099, relaunched
    `python server.py`, hit `/api/status` — response now includes
    `avg_scrape_seconds`, `avg_pipeline_seconds`, `model_fallback`.
    `model_fallback = {"missing": [], "substitutes": {}}` (Ollama is
    healthy; banner stays hidden).

---

## Log triage summary (from the paste)
  - `qwen2.5:14b` / `qwen3:14b` / `deepseek-r1:14b` 404 — fixed via §3.
    Pipeline now silently falls through to `qwen3:8b` and surfaces the
    substitution in the UI.
  - `match_registry.json.tmp` FileNotFoundError — fixed via §4.
    Per-writer tmp names + per-path lock eliminates the race; the
    retry loop now also handles the edge case where a sibling writer
    legitimately beat us.
  - Meta scraper `a[href*='/jobs/']` returns empty — Meta redesigned
    their listing page. Deferred; noted in `scrapers/meta.py` TODO.
    Low ROI to chase until we see if Meta is hiring our stack.
  - Microsoft CAPTCHA — Playwright gets gated. Deferred; would need
    session cookie injection or a different feed.
  - Ashby OpenAI 15 s timeout — the LLM-side timeout in `core/llm.py`
    is already 60 s; the Ashby-specific timeout is the HTTP fetch in
    `ingest/ashby.py` and stays at its default. Not blocking since
    the scheduler re-runs next cycle.
  - Vite "./ui/primitives" import blip — HMR transient, recovered on
    its own. No action.
