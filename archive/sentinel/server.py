"""
SENTINEL API SERVER
Serves data + controls the pipeline from the dashboard.
"""

import io
import json
import mimetypes
import threading
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

from core import app_paths
from core.io_safe import write_text_atomic as _atomic_write_text


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Per-request threading so the dashboard's burst of /api/* calls
    don't queue behind one slow handler (e.g. /api/status hitting Ollama)."""
    daemon_threads = True
    allow_reuse_address = True

logger = logging.getLogger("sentinel.server")

DATA_DIR = Path("data")
CONFIG_FILE = Path("config.json")
DASHBOARD_FILE = DATA_DIR / "dashboard.json"
MARKET_FILE = DATA_DIR / "market_intel.json"
TRACKER_FILE = DATA_DIR / "tracker.json"
DECISIONS_FILE = DATA_DIR / "decision_log.json"

# Shared state for pipeline control. _state_lock guards both the
# check-then-set on cycle_in_progress (POST /api/run-cycle) and
# concurrent read-modify-write of CONFIG_FILE (POST /api/config).
_state_lock = threading.Lock()
_pipeline_state = {
    "running": False,
    "cycle_in_progress": False,
    "current_tiers": None,  # list[str] while running; None when idle
    "last_cycle": None,
    "orchestrator": None,
    "thread": None,
}


def load_json(path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return default


def _run_single_cycle(tiers=("fast",)):
    """Run one pipeline cycle in a background thread.
    Caller (POST /api/run-cycle or /api/run-scraper) has already set
    cycle_in_progress=True under _state_lock; we are responsible for
    clearing it on the way out.

    `tiers`:
      - ("fast",)  — default Run Pipeline. ATS + direct HTTP sources only.
      - ("slow",)  — Run Scraper. Playwright SPA fetchers only.
      - ("fast","slow") — full run. Rarely used.
    """
    _pipeline_state["running"] = True
    _pipeline_state["current_tiers"] = list(tiers)
    try:
        orch = _pipeline_state.get("orchestrator")
        if not orch:
            logger.error("No orchestrator available")
            return
        stats = orch.run_cycle(tiers=tiers)
        _pipeline_state["last_cycle"] = stats
    except Exception as e:
        logger.error("Cycle failed: %s", e)
    finally:
        with _state_lock:
            _pipeline_state["cycle_in_progress"] = False
            _pipeline_state["running"] = False
            _pipeline_state["current_tiers"] = None


# ─── EXPORT BUNDLE ──────────────────────────────────────────────────────
#
# One-click export so the user can walk away with a portable copy of their
# SENTINEL state: preferences, resume, match history, job postings seen,
# digests, etc. Everything is already local — this just zips the pieces so
# they can feed another LLM or back the folder up.
#
# Hard skip list: files that would blow up the zip for no training value
# (log tailing beyond a cap, _pycache_, venv) or that never existed.

_EXPORT_JSON_FILES = [
    "user.json",
    "tracker.json",
    "match_registry.json",
    "seen_urls.json",
    "seen_jobs.json",
    "fake_jobs.json",
    "decision_log.json",
    "cycle_times.json",
    "dashboard.json",
    "market_intel.json",
]

_EXPORT_SUBDIRS = [
    "resume",    # uploaded CV + extracted text + parsed profile
    "resumes",   # rendered resume variants (html/pdf) if any
    "digests",   # weekly markdown digests
    "fit_gaps",  # per-match gap analyses
    "matches",   # raw saved match artefacts
    "parsed",    # parsed job postings - useful for training another LLM
]

_EXPORT_README = """\
# SENTINEL export bundle

Generated {generated_at}.

## What's in here

Top-level
- `config.json` - pipeline settings (role keywords, threshold, preferences,
  ghost-job aggressiveness, etc.)

`data/` JSON files
- `user.json` - profile the wizard captured (name, years of experience,
  current level, setup_completed flag)
- `tracker.json` - application tracker (what you marked applied, liked,
  dismissed, with timestamps)
- `match_registry.json` - persistent cross-cycle match store. Every match
  ever produced with state (seen, starred, dismissed) and `_match_score`,
  `_dimensions`, `_match_reasoning`, `_fake` ghost-scoring sub-object.
- `seen_urls.json` - canonicalised URLs already ingested. Cross-cycle
  dedupe registry (capped at 50k).
- `seen_jobs.json` - (legacy) company||title||location dedupe keys.
- `fake_jobs.json` - postings flagged as ghosts by the scorer.
- `decision_log.json` - every pipeline pass/fail decision with reasons.
- `cycle_times.json` - wall-clock durations and match counts per cycle.
- `dashboard.json`, `market_intel.json` - aggregated views.

`data/` folders
- `resume/` - your uploaded CV plus extracted text and the Ollama-parsed
  structured profile.
- `digests/` - weekly markdown digests.
- `fit_gaps/` - per-match gap analyses (skills/experience the posting
  wants that you don't yet claim).
- `matches/`, `parsed/` - raw artefacts per cycle. `parsed/` is the
  richest training feedstock - every job posting SENTINEL saw, parsed
  into a structured JSON.

## How to use

- Feed `matches/`, `parsed/`, and `decision_log.json` to another model if
  you want to train or fine-tune on your own filtering decisions.
- Copy the folder to a new machine to carry your history forward.
- Nothing in here is encrypted - it's the same JSON the app reads from
  disk. Do not share the bundle with anyone you wouldn't hand your CV to.

No telemetry, no network calls - everything was on your machine already.
"""


def _build_export_zip(data_dir: Path, config_file: Path) -> bytes:
    """Assemble the export zip in-memory. Small enough (<5 MB in practice
    unless parsed/ is huge) that a BytesIO is fine. If it ever gets large
    we can swap to tempfile.SpooledTemporaryFile without changing the
    endpoint shape."""
    buf = io.BytesIO()
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # README with context
        zf.writestr(
            "SENTINEL_EXPORT_README.md",
            _EXPORT_README.format(generated_at=generated_at),
        )
        # Root-level config
        if config_file.exists():
            try:
                zf.write(config_file, "config.json")
            except Exception as e:
                logger.warning("export: could not add config.json: %s", e)
        # Top-level JSON files under data/
        for name in _EXPORT_JSON_FILES:
            p = data_dir / name
            if p.exists() and p.is_file():
                try:
                    zf.write(p, f"data/{name}")
                except Exception as e:
                    logger.warning("export: could not add %s: %s", p, e)
        # Sub-directories - walk recursively, preserve relative paths.
        for sub in _EXPORT_SUBDIRS:
            sub_path = data_dir / sub
            if not sub_path.exists() or not sub_path.is_dir():
                continue
            for f in sub_path.rglob("*"):
                if not f.is_file():
                    continue
                # Skip __pycache__ and hidden files that crept in.
                if any(part.startswith((".", "__")) for part in f.relative_to(sub_path).parts):
                    continue
                try:
                    rel = f.relative_to(data_dir)
                    zf.write(f, f"data/{rel.as_posix()}")
                except Exception as e:
                    logger.warning("export: could not add %s: %s", f, e)
    return buf.getvalue()


class SentinelHandler(BaseHTTPRequestHandler):

    # Exceptions raised when the browser aborts a request before we're
    # done writing. Expected under the dashboard's adaptive polling (a
    # fast tick can race the previous slow response). Benign - we swallow
    # them so the socketserver error handler doesn't spew a traceback per
    # aborted poll. WinError 10053/10054 surface as ConnectionAbortedError
    # / ConnectionResetError; *nix surfaces BrokenPipeError.
    _CLIENT_GONE = (BrokenPipeError, ConnectionAbortedError,
                    ConnectionResetError, ConnectionError)

    def _send_json(self, data, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        except self._CLIENT_GONE:
            # Browser went away mid-response. Nothing to do.
            pass

    def handle_one_request(self):
        """Swallow the same class of benign disconnects at the socket
        level so aborted requests don't reach socketserver.handle_error."""
        try:
            super().handle_one_request()
        except self._CLIENT_GONE:
            self.close_connection = True

    def log_message(self, fmt, *args):
        """Route access logs through our logger at DEBUG so normal
        operation doesn't shout at stdout."""
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/dashboard":
            self._send_json(load_json(DASHBOARD_FILE, {}))

        elif path == "/api/market":
            self._send_json(load_json(MARKET_FILE, []))

        elif path == "/api/tracker":
            self._send_json(load_json(TRACKER_FILE, []))

        elif path == "/api/decisions":
            from core import decision_store
            # Legacy consumers expect a plain list of pass-decisions. We
            # now also return reactions. Keep both in one payload.
            data = decision_store.list_all(DATA_DIR)
            self._send_json(data)

        elif path == "/api/reactions":
            from core import decision_store
            parsed_q = parse_qs(parsed.query)
            action = parsed_q.get("action", [None])[0]
            self._send_json(decision_store.list_reactions(DATA_DIR, filter_action=action))

        elif path == "/api/market-tier1":
            from core import market_intel
            self._send_json(market_intel.tier1_bundle(DATA_DIR))

        elif path == "/api/market-tier2":
            from core import market_intel
            self._send_json(market_intel.tier2_bundle(DATA_DIR))

        elif path == "/api/cycle-times":
            self._send_json(load_json(DATA_DIR / "cycle_times.json", []))

        elif path == "/api/story-bank":
            # Plain-markdown story bank, rendered as-is for the UI. The
            # file is populated by core.story_bank.append_stories every
            # time a fit-gap analysis runs. Empty string if file missing.
            from core.story_bank import DEFAULT_FILENAME
            bank_path = DATA_DIR / DEFAULT_FILENAME
            try:
                text = bank_path.read_text(encoding="utf-8") if bank_path.exists() else ""
            except Exception as e:
                text = f"<!-- read error: {e} -->"
            self._send_json({
                "path": str(bank_path),
                "exists": bank_path.exists(),
                "text": text,
                "size_bytes": len(text.encode("utf-8")),
            })

        elif path == "/api/cycle-history":
            # Same source as /api/cycle-times but newest-first and capped
            # by ?n= (default 50). The History tab renders this as a
            # timeline; the Brief tab keeps using /api/cycle-times for
            # the rolling-average arithmetic.
            all_cycles = load_json(DATA_DIR / "cycle_times.json", [])
            try:
                n = int(parse_qs(parsed.query).get("n", ["50"])[0])
            except Exception:
                n = 50
            n = max(1, min(n, 200))
            # newest first
            cycles_sorted = list(reversed(all_cycles))[:n]
            self._send_json(cycles_sorted)

        elif path == "/api/triage/learned":
            # Aggregated negative-keyword signal computed from the user's
            # own keep/skip reactions. Surfaced on the Triage tab as
            # suggestions the user can copy into their blocklist -
            # deliberately advisory, not auto-applied.
            from core import triage
            self._send_json(triage.learned_keywords(DATA_DIR))

        elif path == "/api/logs":
            # In-app log tail. Mirrors `tail -n` with a level filter.
            # Query: ?n=200&level=INFO|DEBUG|WARNING|ERROR
            # Returns {lines:[{ts,level,logger,message,raw}], available:bool}
            from core import log_tail
            q = parse_qs(parsed.query)
            try:
                n = int(q.get("n", ["200"])[0])
            except Exception:
                n = 200
            level = (q.get("level", ["INFO"])[0] or "INFO").upper()
            self._send_json(log_tail.tail(n=n, min_level=level))

        elif path == "/api/matches":
            # Registry-backed: one row per role across all cycles, with
            # per-user state (seen/dismissed/starred). Falls back to the
            # old concat-last-10 behaviour only if the registry file is
            # missing - that window lets us keep showing matches before
            # the first cycle re-runs against the registry.
            try:
                from core.match_registry import get_registry
                reg = get_registry(DATA_DIR)
                reg.reload()
                entries = reg.all_entries()
            except Exception as e:
                logger.warning("match_registry read failed, falling back: %s", e)
                entries = []

            # Join in applied state from tracker. Tracker keys on
            # lower(title)||lower(company) (looser than the registry's
            # dedupe key, which also includes location), so we rebuild
            # the tracker key per entry and look it up.
            tracker_rows = load_json(TRACKER_FILE, []) or []
            applied_keys = set()
            for row in tracker_rows:
                if not isinstance(row, dict):
                    continue
                t = (row.get("title") or "").strip().lower()
                c = (row.get("company") or "").strip().lower()
                if t and c:
                    applied_keys.add(f"{t}||{c}")

            out = []
            if entries:
                for e in entries:
                    payload = e.get("payload") or {}
                    t = (payload.get("title") or "").strip().lower()
                    c = (payload.get("company") or "").strip().lower()
                    applied = f"{t}||{c}" in applied_keys if t and c else False
                    # Back-compat: UI historically consumed the raw
                    # payload (flat dict with _match_score). Keep that
                    # shape and sprinkle registry fields onto it. No
                    # migration needed on the frontend side.
                    row = dict(payload)
                    row["_registry_key"] = e.get("key")
                    row["_score"] = e.get("score", row.get("_match_score", 0))
                    row["_seen"] = bool(e.get("seen"))
                    row["_dismissed"] = bool(e.get("dismissed"))
                    row["_starred"] = bool(e.get("starred"))
                    row["_removed"] = bool(e.get("removed"))
                    row["_applied"] = bool(applied)
                    row["_first_seen_at"] = e.get("first_seen_at")
                    row["_last_seen_at"] = e.get("last_seen_at")
                    row["_cycle_count"] = e.get("cycle_count")
                    row["_profile_version"] = e.get("profile_version")
                    out.append(row)
            else:
                # Fallback path - original behaviour.
                matches_dir = DATA_DIR / "matches"
                if matches_dir.exists():
                    for f in sorted(matches_dir.glob("*.json"), reverse=True)[:10]:
                        try:
                            out.extend(json.loads(f.read_text()))
                        except Exception:
                            pass
            self._send_json(out)

        elif path == "/api/match-registry-stats":
            try:
                from core.match_registry import get_registry
                reg = get_registry(DATA_DIR)
                reg.reload()
                self._send_json(reg.stats())
            except Exception as e:
                self._send_json({"error": str(e)})

        elif path == "/api/fit-gaps":
            gaps_dir = DATA_DIR / "fit_gaps"
            all_gaps = []
            if gaps_dir.exists():
                for f in sorted(gaps_dir.glob("*.json"), reverse=True)[:10]:
                    try:
                        all_gaps.extend(json.loads(f.read_text()))
                    except Exception:
                        pass
            self._send_json(all_gaps)

        elif path == "/api/digests":
            digest_dir = DATA_DIR / "digests"
            digests = []
            if digest_dir.exists():
                for f in sorted(digest_dir.glob("*.txt"), reverse=True)[:5]:
                    try:
                        digests.append({"file": f.name, "text": f.read_text()})
                    except Exception:
                        pass
            self._send_json(digests)

        elif path == "/api/fake-jobs":
            self._send_json(load_json(DATA_DIR / "fake_jobs.json", []))

        elif path == "/api/export":
            # One-click export. Streams a zip of config + data/ tree so the
            # user can back up their SENTINEL state or feed it to another
            # LLM. Runs entirely on local files - no network calls.
            try:
                blob = _build_export_zip(DATA_DIR, CONFIG_FILE)
                stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
                filename = f"sentinel-export-{stamp}.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(blob)))
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(blob)
                except self._CLIENT_GONE:
                    pass
            except Exception as e:
                logger.exception("export failed: %s", e)
                self._send_json({"ok": False, "error": str(e)}, status=500)

        elif path == "/api/resumes":
            resume_dir = DATA_DIR / "resumes"
            resumes = []
            if resume_dir.exists():
                for f in sorted(resume_dir.glob("*.html"), reverse=True)[:10]:
                    resumes.append({"file": f.name, "name": f.stem.replace("_", " ")})
                for f in sorted(resume_dir.glob("*.pdf"), reverse=True)[:10]:
                    resumes.append({"file": f.name, "name": f.stem.replace("_", " ")})
            self._send_json(resumes)

        elif path == "/api/resumes/download":
            # Stream one resume file back to the browser. Accepts
            # ?file=<basename>. We resolve against data/resumes/ and
            # reject anything that escapes that directory -- users
            # send these URLs to themselves mostly, but a path like
            # "../../etc/passwd" should still 400 rather than leak.
            qs = parse_qs(parsed.query)
            fname = (qs.get("file", [""])[0] or "").strip()
            resume_dir = (DATA_DIR / "resumes").resolve()
            if not fname:
                self._send_json({"error": "missing file param"}, 400)
                return
            try:
                target = (resume_dir / fname).resolve()
            except Exception:
                self._send_json({"error": "bad filename"}, 400)
                return
            # Path traversal guard: target must live inside resume_dir.
            try:
                target.relative_to(resume_dir)
            except ValueError:
                self._send_json({"error": "file outside resumes dir"}, 400)
                return
            if not target.is_file():
                self._send_json({"error": "not found"}, 404)
                return

            # Content-type by extension. Keep the mapping small and
            # obvious so a weak future debugger can extend it.
            ext = target.suffix.lower()
            ctype = {
                ".pdf": "application/pdf",
                ".html": "text/html; charset=utf-8",
            }.get(ext, "application/octet-stream")
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            # inline for PDFs (opens in browser tab); attachment for HTML
            # so it downloads rather than trying to render in-page.
            disp = "inline" if ext == ".pdf" else "attachment"
            self.send_header(
                "Content-Disposition",
                f'{disp}; filename="{target.name}"',
            )
            self.end_headers()
            self.wfile.write(data)

        elif path == "/api/status":
            from core import llm as llm_module
            model_check = llm_module.check_models()
            usage = llm_module.get_usage_stats()
            # Ollama 404 fallback state: when a configured model returned
            # 404 at runtime, the LLM client remembers the substitution
            # so the UI can flag "pipeline is running on qwen3:8b instead
            # of qwen3:14b" rather than let the user assume everything is
            # optimal. Empty dict when all configured models resolved fine.
            try:
                model_fallback = llm_module.get_effective_models()
            except Exception:
                model_fallback = {"missing": [], "substitutes": {}}

            # Match agent diagnostics (embedding vs LLM path, latency).
            # We prefer the live agent snapshot when a pipeline is loaded,
            # and fall back to the last-persisted stats file so the UI
            # keeps showing a median latency across backend restarts (the
            # in-memory ring buffer is lost on every cold start).
            match_status = {}
            orch = _pipeline_state.get("orchestrator")
            if orch is not None and getattr(orch, "match", None) is not None:
                try:
                    match_status = orch.match.get_status()
                except Exception:
                    match_status = {}
            if not match_status or match_status.get("median_latency_ms") is None:
                try:
                    persisted = load_json(DATA_DIR / "match_stats.json", {}) or {}
                    if persisted:
                        # Don't clobber a live "mode" reading if we have it;
                        # only backfill the keys the live snapshot missed.
                        merged = dict(persisted)
                        merged.update({k: v for k, v in match_status.items() if v not in (None, "")})
                        match_status = merged
                except Exception:
                    pass

            # Average cycle duration (last 10 runs) + median match latency.
            # Also split into scrape vs pipeline so the Brief tab can
            # answer "is ingest slow or the LLM slow?" without the user
            # having to open the log. Falls back gracefully on older
            # cycle_times entries that pre-date the split.
            cycle_times = load_json(DATA_DIR / "cycle_times.json", [])
            recent_secs = [c.get("seconds", 0) for c in cycle_times[-10:] if c.get("seconds")]
            avg_cycle_seconds = round(sum(recent_secs) / len(recent_secs), 1) if recent_secs else None
            recent_scrape = [c.get("ingest_seconds") for c in cycle_times[-10:] if c.get("ingest_seconds") is not None]
            avg_scrape_seconds = round(sum(recent_scrape) / len(recent_scrape), 1) if recent_scrape else None
            recent_pipe = [c.get("pipeline_seconds") for c in cycle_times[-10:] if c.get("pipeline_seconds") is not None]
            avg_pipeline_seconds = round(sum(recent_pipe) / len(recent_pipe), 1) if recent_pipe else None

            # Live progress snapshot - updated by orchestrator at each phase
            # boundary. The UI polls this so the Brief tab can paint the
            # current stage and rolling counts while a cycle is in flight.
            progress = None
            if orch is not None and getattr(orch, "progress", None) is not None:
                try:
                    progress = dict(orch.progress)
                    # Shallow-copy the counts so a concurrent phase update
                    # doesn't mutate the dict we just serialised.
                    progress["counts"] = dict(progress.get("counts", {}))
                except Exception:
                    progress = None

            # "Last cycle finished Xs ago" indicator for the UI.
            last_cycle_ts = None
            if cycle_times:
                last_cycle_ts = cycle_times[-1].get("ts")

            # First-run gate: surfaced on /api/status so the UI can show
            # the wizard + hide Run Pipeline without an extra round trip.
            from core import user_store
            setup_completed = user_store.is_setup_complete(DATA_DIR)

            # Cross-cycle URL dedupe stats. Last-cycle counts live on the
            # orchestrator; the registry size is read from disk so we
            # don't have to keep the orchestrator around to know it.
            url_dedupe = {"input": 0, "skipped": 0, "new": 0, "registry_size": 0}
            if orch is not None and getattr(orch, "last_url_dedupe_stats", None) is not None:
                try:
                    url_dedupe.update(orch.last_url_dedupe_stats)
                except Exception:
                    pass
            try:
                seen_urls_path = DATA_DIR / "seen_urls.json"
                if seen_urls_path.exists():
                    url_dedupe["registry_size"] = len(load_json(seen_urls_path, []) or [])
            except Exception:
                pass

            # Dead ATS slugs recorded by the last ingest phase. Keeps
            # the UI able to show a "N slugs are 404'd, remove them"
            # banner without scraping the log files.
            dead_slugs = load_json(DATA_DIR / "dead_slugs.json", []) or []

            # Per-source ingest tally (#89). Snapshot written by the
            # orchestrator at the end of every ingest phase so the Brief
            # tab can show where matches are actually coming from.
            ingest_sources = load_json(DATA_DIR / "ingest_sources.json", {}) or {}

            # Derive cycle_in_progress from BOTH the manual-claim slot
            # AND the orchestrator's live `progress.stage`. The auto-
            # loop runs cycles via `orchestrator.run()` which advances
            # progress.stage but never touches `_pipeline_state["cycle_in_progress"]`
            # — that flag is only set by the manual /api/run-cycle path.
            # Without this OR, the UI thought no cycle was running
            # whenever the auto-loop fired, so the Run Pipeline button
            # looked ready but clicks fought the in-flight auto cycle.
            stage = (progress or {}).get("stage")
            cycle_active = bool(_pipeline_state["cycle_in_progress"]) or (
                stage is not None and stage != "idle"
            )
            self._send_json({
                "status": "running",
                "pipeline_running": _pipeline_state["running"] or cycle_active,
                "cycle_in_progress": cycle_active,
                # current_tiers is set while a cycle is in flight so the
                # UI can distinguish "Run Pipeline" (fast) from "Run
                # Scraper" (slow) without guessing from the stage label.
                "current_tiers": _pipeline_state.get("current_tiers"),
                "last_cycle": _pipeline_state["last_cycle"],
                "last_cycle_ts": last_cycle_ts,
                "has_data": DASHBOARD_FILE.exists(),
                "matches_count": sum(1 for _ in (DATA_DIR / "matches").glob("*.json")) if (DATA_DIR / "matches").exists() else 0,
                "cycles_logged": len(load_json(MARKET_FILE, [])),
                "models": model_check,
                "model_fallback": model_fallback,
                "usage": usage,
                "match": match_status,
                "avg_cycle_seconds": avg_cycle_seconds,
                "avg_scrape_seconds": avg_scrape_seconds,
                "avg_pipeline_seconds": avg_pipeline_seconds,
                "cycles_recorded": len(cycle_times),
                "progress": progress,
                "setup_completed": setup_completed,
                "url_dedupe": url_dedupe,
                "dead_slugs": dead_slugs,
                "ingest_sources": ingest_sources,
            })

        elif path == "/api/config":
            self._send_json(load_json(CONFIG_FILE, {}))

        elif path == "/api/resume":
            from core import resume_store
            # Don't include the full parsed text in the default GET; it can
            # be large and the UI only needs the metadata for most views.
            state = resume_store.read_current(DATA_DIR)
            include_text = parse_qs(parsed.query).get("full", ["0"])[0] == "1"
            if not include_text:
                state = {**state, "parsed_text": "", "additional_notes_len": len(state["additional_notes"])}
                state.pop("additional_notes", None)
            self._send_json(state)

        elif path == "/api/setup-state":
            # Snapshot of user.json + a few derived readiness flags the
            # wizard uses to decide which step it's on (resume present?
            # preferences set? role keywords populated?).
            from core import user_store, resume_store
            user = user_store.load(DATA_DIR)
            cfg = load_json(CONFIG_FILE, {}) or {}
            resume = resume_store.read_current(DATA_DIR)
            prefs = cfg.get("preferences", {}) or {}
            role_keywords = (cfg.get("ingest", {}) or {}).get("role_keywords", []) or []
            self._send_json({
                "user": user,
                "setup_completed": bool(user.get("setup_completed")),
                "has_resume": bool(resume.get("has_resume")),
                "has_preferences": bool(
                    prefs.get("allowed_locations") or prefs.get("blocked_locations")
                    or prefs.get("salary_floor_usd") or prefs.get("remote_only")
                    or prefs.get("allow_remote") is False
                    or prefs.get("current_level") or prefs.get("years_experience")
                ),
                "has_role_keywords": len(role_keywords) > 0,
                "has_identity": bool(user.get("name") or user.get("current_role") or user.get("target_level")),
            })

        elif path == "/api/prewarm":
            from core import prewarm
            self._send_json(prewarm.get_status())

        elif path == "/api/preflight":
            # Parallel dependency check used by the first-run wizard to
            # give users an "easy startup" readout before any pipeline
            # work. Caps around 3s total on a healthy box.
            from core import preflight
            cfg = load_json(CONFIG_FILE, {}) or {}
            models = list({
                (cfg.get("parse", {}) or {}).get("model") or "gemma4:e4b",
                (cfg.get("match", {}) or {}).get("model") or "gemma4:26b",
                (cfg.get("chat",  {}) or {}).get("model") or "qwen3:8b",
            })
            models = [m for m in models if m]
            self._send_json(preflight.run_all(models))

        elif path == "/api/resources":
            # Aggregate resource panel: cycle wall-clock, match-mode,
            # optional GPU/RAM probes. Degrades gracefully when files
            # or tooling are missing so the Brief tab always gets a
            # renderable payload.
            try:
                from core import resource_snapshot
                self._send_json(resource_snapshot.collect(DATA_DIR))
            except Exception as e:
                logger.exception("resource snapshot failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/helper":
            # Current helper view: chosen sprite + full state->asset map
            # so the frontend can switch between idle / celebrate / sleep
            # GIFs locally without another round-trip.
            try:
                from core import helper as _helper
                self._send_json(_helper.read_helper(DATA_DIR).to_dict())
            except Exception as e:
                logger.exception("read helper failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/helper/options":
            # Full catalogue for the settings picker: sprite roster with
            # per-state asset URLs, eye styles, accessories, name limits,
            # advertised animation states. UI renders preview GIFs from
            # this payload directly.
            try:
                from core import helper as _helper
                self._send_json(_helper.list_options())
            except Exception as e:
                logger.exception("helper options failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/helper/sayings":
            # Mood-grouped speech-bubble copy. The UI shuffles within
            # the requested mood so lines don't repeat. Query param:
            # ?mood=idle (default) | cycle_start | match_found |
            # empty_cycle | encourage.
            try:
                from core import helper as _helper
                mood = (parse_qs(parsed.query).get("mood") or ["idle"])[0]
                self._send_json({"mood": mood,
                                  "sayings": _helper.sayings(mood)})
            except Exception as e:
                logger.exception("helper sayings failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/engagement":
            # Current engagement metrics + nudge decision preview. Never
            # posts to Discord - that's a separate POST endpoint. Lets the
            # dashboard show "tier: dormant, 3d idle" chips without firing
            # anything, and makes the dry-run payload inspectable.
            try:
                from core import engagement, user_store
                user_data = user_store.load(DATA_DIR)
                metrics = engagement.compute_metrics(user_data)
                nudge_state = engagement.load_nudge_state(DATA_DIR)
                fire, reason = engagement.should_reengage(
                    metrics, nudge_state.get("last_nudge_at"),
                )
                self._send_json({
                    "metrics": metrics.to_dict(),
                    "nudge_state": nudge_state,
                    "would_fire": fire,
                    "reason": reason,
                })
            except Exception as e:
                logger.exception("engagement snapshot failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/resume/profile":
            from core import resume_profile, resume_store
            # Return the cached structured profile if it exists. Never run the
            # LLM synchronously here: a qwen3:8b parse can take 20-60s and
            # would hang the UI. If no cache exists, return a status the UI
            # can act on by POSTing /api/resume/reparse (which keeps the
            # busy state + "Parsing..." button feedback).
            try:
                state = resume_store.read_current(DATA_DIR)
                if not state["has_resume"]:
                    self._send_json({"profile": None, "status": "no_resume"})
                    return
                cached = resume_profile.get_cached_profile(DATA_DIR)
                if cached:
                    self._send_json({"profile": cached, "cached": True})
                    return
                self._send_json({"profile": None, "status": "needs_parse"})
            except Exception as e:
                logger.exception("Profile fetch failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path.startswith("/api/"):
            self._send_json({
                "error": "Unknown endpoint",
                "endpoints": [
                    "GET  /api/dashboard", "GET  /api/market", "GET  /api/market-tier1",
                    "GET  /api/market-tier2",
                    "GET  /api/tracker", "GET  /api/decisions", "GET  /api/reactions",
                    "GET  /api/matches", "GET  /api/match-registry-stats",
                    "POST /api/matches/state",
                    "GET  /api/fit-gaps", "GET  /api/digests",
                    "GET  /api/fake-jobs", "GET  /api/resumes", "GET  /api/status",
                    "GET  /api/config", "GET  /api/cycle-times",
                    "GET  /api/resume", "GET  /api/resume?full=1",
                    "GET  /api/resume/profile",
                    "GET  /api/setup-state", "GET  /api/prewarm",
                    "GET  /api/cycle-history?n=50",
                    "GET  /api/logs?n=200&level=INFO",
                    "GET  /api/triage/learned",
                    "POST /api/run-cycle", "POST /api/run-scraper",
                    "POST /api/run-all", "POST /api/reset-history",
                    "POST /api/config",
                    "POST /api/setup", "POST /api/prewarm",
                    "POST /api/resume", "POST /api/resume/notes", "POST /api/resume/clear",
                    "POST /api/resume/reparse",
                    "POST /api/reactions", "POST /api/chat",
                    "POST /api/match/rationale",
                ],
            }, status=404)

        else:
            # Non-API path: serve the built UI out of sentinel-ui/dist so
            # the packaged exe is a single self-contained thing. In dev
            # the Vite dev server still handles this on :3000.
            self._serve_static(path)

    def _serve_static(self, path: str):
        root = app_paths.static_dir()
        if not root.is_dir():
            self._send_json({
                "error": "UI not built",
                "hint": "Run: cd sentinel-ui ; npm install ; npm run build",
                "looked_in": str(root),
            }, status=503)
            return

        # Strip leading slash and prevent directory traversal.
        rel = (path or "/").lstrip("/")
        if not rel:
            rel = "index.html"
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            self._send_json({"error": "forbidden"}, status=403)
            return

        # SPA fallback: if the file doesn't exist, serve index.html so React
        # Router (if the user ever adds one) still works on deep links.
        if not target.is_file():
            target = root / "index.html"
            if not target.is_file():
                self._send_json({"error": "not found"}, status=404)
                return

        try:
            data = target.read_bytes()
        except OSError as e:
            self._send_json({"error": f"could not read: {e}"}, status=500)
            return

        ctype, _ = mimetypes.guess_type(str(target))
        if ctype is None:
            ctype = "application/octet-stream"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Hash-named Vite assets are safe to cache hard; index.html never is.
        if target.name == "index.html":
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""

        if path in ("/api/run-cycle", "/api/run-scraper", "/api/run-all"):
            # Three flavours, one code path:
            #   /api/run-cycle   → tiers=("fast",)          fast ATS + HTTP
            #   /api/run-scraper → tiers=("slow",)          Playwright SPAs
            #   /api/run-all     → tiers=("fast","slow")    both, sequential
            #
            # All three share the same cycle slot (mutually exclusive)
            # since they write to the same match_registry downstream.
            # The orchestrator's run_cycle() walks the tiers tuple in
            # order, so "run-all" = fast first, then slow, in ONE cycle
            # -- no need to orchestrate two separate thread launches
            # here. That keeps the "is a cycle running?" check correct.
            if path == "/api/run-scraper":
                tiers = ("slow",)
                label = "Scraper"
            elif path == "/api/run-all":
                tiers = ("fast", "slow")
                label = "Full run"
            else:
                tiers = ("fast",)
                label = "Pipeline"

            # Reject manual cycles until first-run setup is complete -
            # otherwise the pipeline scrapes against an empty profile
            # and the dashboard shows zero matches for reasons that
            # aren't obvious to the user.
            from core import user_store
            if not user_store.is_setup_complete(DATA_DIR):
                self._send_json({
                    "ok": False,
                    "error": f"Setup not complete. Finish the first-run wizard to enable the {label.lower()}.",
                    "needs_setup": True,
                }, 409)
                return
            # Claim the cycle slot atomically so two rapid POSTs can't
            # both pass the check and spawn two concurrent cycles. We
            # also check the orchestrator's progress.stage because the
            # auto-loop runs cycles without touching this flag — without
            # this we'd let the user fire a manual cycle on top of an
            # in-flight auto cycle, which corrupts shared orch state.
            orch = _pipeline_state.get("orchestrator")
            orch_stage = None
            if orch is not None:
                try:
                    orch_stage = (orch.progress or {}).get("stage")
                except Exception:
                    orch_stage = None
            orch_busy = orch_stage is not None and orch_stage != "idle"
            with _state_lock:
                if _pipeline_state["cycle_in_progress"] or orch_busy:
                    self._send_json({"ok": False,
                                     "error": f"Cycle already in progress (cannot start {label.lower()})"}, 409)
                    return
                _pipeline_state["cycle_in_progress"] = True
                t = threading.Thread(
                    target=_run_single_cycle,
                    args=(tiers,),
                    daemon=True,
                )
                _pipeline_state["thread"] = t
                t.start()
            self._send_json({"ok": True, "message": f"{label} cycle started",
                             "tiers": list(tiers)})

        elif path == "/api/setup":
            # Commit-all endpoint for the wizard. Body shape:
            #   {
            #     "identity": {"name": "", "current_role": "", "target_level": ""},
            #     "role_keywords": ["product manager", ...],
            #     "preferences": {...},       # merged into config.preferences
            #     "finish": true              # flip setup_completed when true
            #   }
            # Every field is optional - the wizard can POST incrementally
            # as the user clicks through steps, then POST with finish:true
            # on the final step to mark setup done.
            try:
                data = json.loads(body or b"{}")
            except Exception as e:
                self._send_json({"ok": False, "error": f"bad json: {e}"}, 400)
                return
            try:
                from core import user_store
                identity = data.get("identity") or {}
                patch = {k: identity[k] for k in ("name", "current_role", "target_level") if k in identity}
                if patch:
                    user_store.update(DATA_DIR, patch)

                # Merge preferences + role_keywords into config.json so the
                # orchestrator's next _refresh_profile picks them up without
                # a duplicate store.
                cfg_patch_present = ("preferences" in data) or ("role_keywords" in data)
                if cfg_patch_present:
                    with _state_lock:
                        existing = load_json(CONFIG_FILE, {}) or {}
                        if isinstance(data.get("role_keywords"), list):
                            existing.setdefault("ingest", {})["role_keywords"] = data["role_keywords"]
                        if isinstance(data.get("preferences"), dict):
                            prefs = existing.setdefault("preferences", {})
                            for k, v in data["preferences"].items():
                                prefs[k] = v
                        _atomic_write_text(CONFIG_FILE, json.dumps(existing, indent=2))
                    # Hot-apply preferences if the match agent is live.
                    orch = _pipeline_state.get("orchestrator")
                    if orch is not None and isinstance(data.get("preferences"), dict):
                        try:
                            orch.match.set_preferences(existing.get("preferences", {}))
                        except Exception:
                            logger.exception("Could not hot-apply preferences")

                completed = False
                if data.get("finish"):
                    user_store.mark_setup_complete(DATA_DIR)
                    completed = True

                self._send_json({"ok": True, "setup_completed": completed})
            except Exception as e:
                logger.exception("Setup save failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/helper":
            # Merge a partial helper update into the user store. Body
            # is a JSON dict with any subset of {name, sprite, color,
            # eyes, accessory}; unknown keys or out-of-range values
            # are dropped silently so a bad patch is a no-op rather
            # than a reset.
            try:
                patch = json.loads(body or b"{}")
            except Exception as e:
                self._send_json({"ok": False, "error": f"bad json: {e}"}, 400)
                return
            try:
                from core import helper as _helper
                view = _helper.update_helper(DATA_DIR, patch)
                self._send_json({"ok": True, "helper": view.to_dict()})
            except Exception as e:
                logger.exception("helper update failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/prewarm":
            # Trigger pre-warm as a background thread. Wizard calls this
            # when it opens so cold-start cost is paid while the user is
            # still filling in the form. Idempotent - a second POST while
            # one is running is a no-op.
            try:
                from core import prewarm
                cfg = load_json(CONFIG_FILE, {}) or {}
                models = list({
                    (cfg.get("parse", {}) or {}).get("model") or "gemma4:e4b",
                    (cfg.get("match", {}) or {}).get("model") or "gemma4:26b",
                    (cfg.get("chat",  {}) or {}).get("model") or "qwen3:8b",
                })
                models = [m for m in models if m]
                prewarm.run_background(models)
                self._send_json({"ok": True, "status": prewarm.get_status()})
            except Exception as e:
                logger.exception("Prewarm trigger failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/config":
            try:
                new_config = json.loads(body)
                # Serialise read-modify-write so two concurrent saves
                # from two browser tabs don't lose updates.
                with _state_lock:
                    existing = load_json(CONFIG_FILE, {})

                    if "role_keywords" in new_config:
                        existing.setdefault("ingest", {})["role_keywords"] = new_config["role_keywords"]
                    if "threshold" in new_config:
                        existing.setdefault("match", {})["threshold"] = float(new_config["threshold"])
                    if "models" in new_config:
                        models = new_config["models"]
                        # Per-task model overrides. Each task is optional
                        # so the wizard can patch a single choice without
                        # wiping the others. Unknown keys are ignored
                        # so the UI can add more tasks later without a
                        # server bump.
                        if "parse" in models:
                            existing.setdefault("parse", {})["model"] = models["parse"]
                        if "match" in models:
                            existing.setdefault("match", {})["model"] = models["match"]
                        if "analyze" in models:
                            existing.setdefault("analyze", {})["model"] = models["analyze"]
                        if "chat" in models:
                            existing.setdefault("chat", {})["model"] = models["chat"]
                        if "digest" in models:
                            existing.setdefault("digest", {})["model"] = models["digest"]
                    if "analyze_top_n" in new_config:
                        try:
                            existing.setdefault("analyze", {})["top_n"] = int(new_config["analyze_top_n"])
                        except Exception:
                            pass
                    if "profile_text" in new_config:
                        existing.setdefault("match", {})["profile_text"] = new_config["profile_text"]
                    if "max_cycles" in new_config:
                        existing["max_cycles"] = int(new_config["max_cycles"])
                    if "cycle_interval_minutes" in new_config:
                        # Clamp to a sane range. Below 5 min hammers ATS
                        # APIs for no gain (they don't post that often).
                        # Above 240 is effectively "off" for a daily scanner.
                        try:
                            mins = int(new_config["cycle_interval_minutes"])
                        except (TypeError, ValueError):
                            mins = 30
                        existing["cycle_interval_minutes"] = max(5, min(240, mins))
                    if "preferences" in new_config and isinstance(new_config["preferences"], dict):
                        # Shallow-merge so one field can be updated in isolation.
                        prefs = existing.setdefault("preferences", {})
                        for k, v in new_config["preferences"].items():
                            prefs[k] = v
                    if "ingest" in new_config and isinstance(new_config["ingest"], dict):
                        # Company tenant lists + big-tech toggles. Each field
                        # is independently optional so the UI can patch a
                        # single list without wiping neighbours.
                        ing = existing.setdefault("ingest", {})
                        src = new_config["ingest"]
                        if "greenhouse_companies" in src and isinstance(src["greenhouse_companies"], list):
                            # Normalise: lowercase, strip, dedupe, drop empty.
                            ing["greenhouse_companies"] = sorted({
                                str(s).strip().lower() for s in src["greenhouse_companies"]
                                if str(s).strip()
                            })
                        if "lever_companies" in src and isinstance(src["lever_companies"], list):
                            ing["lever_companies"] = sorted({
                                str(s).strip().lower() for s in src["lever_companies"]
                                if str(s).strip()
                            })
                        if "ashby_companies" in src and isinstance(src["ashby_companies"], list):
                            # Ashby is [[display, slug], ...]. Preserve display
                            # casing but lowercase the slug.
                            cleaned = []
                            for row in src["ashby_companies"]:
                                if isinstance(row, list) and len(row) >= 2:
                                    display = str(row[0] or "").strip()
                                    slug = str(row[1] or "").strip().lower()
                                    if slug:
                                        cleaned.append([display or slug, slug])
                            # Dedupe by slug, last write wins.
                            by_slug = {}
                            for row in cleaned:
                                by_slug[row[1]] = row
                            ing["ashby_companies"] = sorted(by_slug.values(), key=lambda r: r[1])
                        for tog in ("enable_apple", "enable_amazon", "enable_google",
                                    "enable_meta", "enable_microsoft"):
                            if tog in src:
                                ing[tog] = bool(src[tog])
                    if "fake_detection" in new_config and isinstance(new_config["fake_detection"], dict):
                        fd = existing.setdefault("fake_detection", {})
                        for k, v in new_config["fake_detection"].items():
                            fd[k] = v

                    _atomic_write_text(CONFIG_FILE, json.dumps(existing, indent=2))

                # Hot-apply preferences to the live match agent so the user
                # doesn't have to wait for the next cycle to see changes.
                orch = _pipeline_state.get("orchestrator")
                if orch is not None and "preferences" in new_config:
                    try:
                        orch.match.set_preferences(existing.get("preferences", {}))
                    except Exception:
                        logger.exception("Could not hot-apply preferences")
                if orch is not None and "fake_detection" in new_config:
                    try:
                        fd = existing.get("fake_detection") or {}
                        orch.match.set_fake_threshold(
                            fd.get("aggressiveness", fd.get("threshold"))
                        )
                        # Ghost-score fold controls. These live in the same
                        # fake_detection block so one Settings panel owns all
                        # ghost-behaviour knobs, but they're semantically
                        # different from the flag threshold above: weight
                        # scales the penalty, flag/warn decide the badge band.
                        if "ghost_weight" in fd:
                            orch.match.set_ghost_weight(fd.get("ghost_weight"))
                        if "flag_threshold" in fd or "warn_threshold" in fd:
                            orch.match.set_ghost_thresholds(
                                flag=fd.get("flag_threshold"),
                                warn=fd.get("warn_threshold"),
                            )
                    except Exception:
                        logger.exception("Could not hot-apply fake_detection threshold")

                self._send_json({"ok": True, "message": "Config updated"})

            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/cover-letter":
            # Generate a cover letter from the user's parsed resume profile
            # against a specific job. Local Ollama call, no network. Result
            # is returned to the caller AND persisted under
            # data/cover_letters/ so the user can browse past drafts.
            #
            # Body: {"job": {title, company, location, description, ...},
            #        "tone": "professional"|"warm"|"punchy",
            #        "custom_note": str, "model": str?}
            try:
                payload = json.loads(body or b"{}")
            except Exception as e:
                self._send_json({"ok": False, "error": f"bad json: {e}"}, 400)
                return
            job = payload.get("job") or {}
            title = (job.get("title") or "").strip()
            company = (job.get("company") or "").strip()
            if not title or not company:
                self._send_json({"ok": False, "error": "job.title and job.company are required"}, 400)
                return
            tone = (payload.get("tone") or "professional").strip().lower()
            if tone not in {"professional", "warm", "punchy"}:
                tone = "professional"
            custom_note = (payload.get("custom_note") or "").strip()
            model_override = (payload.get("model") or "").strip() or None

            # Pull the parsed resume profile (optional - falls back to raw
            # resume text if the structured parse hasn't run). Without
            # either, we refuse: generating against zero candidate signal
            # produces generic slop that's worse than no letter at all.
            from core import resume_profile as rp_module
            from core import resume_store, user_store, llm as llm_module

            profile = rp_module.get_cached_profile(DATA_DIR)
            profile_text = rp_module.profile_to_text(profile) if profile else ""
            if not profile_text:
                # Fall back to the raw resume text if present.
                try:
                    state = resume_store.read_current(DATA_DIR)
                    profile_text = (state.get("text") or "").strip()
                except Exception:
                    profile_text = ""
            if not profile_text:
                self._send_json({
                    "ok": False,
                    "error": "No resume on file. Upload a resume in Settings first.",
                }, 400)
                return

            # Trim the profile text so we don't hand the LLM a 10k-token
            # block. First ~4k chars is plenty for a cover-letter prompt.
            profile_text = profile_text[:4000]

            user = user_store.load(DATA_DIR) or {}
            candidate_name = (user.get("name") or (profile or {}).get("name") or "").strip()

            # Compact the JD pieces - no point feeding a 5000-word dump.
            description = (job.get("description") or "").strip()[:2500]
            tech_list = job.get("technologies") or []
            location = (job.get("location") or "").strip()
            seniority = (job.get("seniority") or "").strip()

            tone_guidance = {
                "professional": "Calm, clear, and specific. No jargon, no hype.",
                "warm": "Conversational and genuine without being informal. Show curiosity about the team.",
                "punchy": "Tight, direct, high-signal. Short sentences. Lead with impact, not background.",
            }[tone]

            prompt = f"""You are writing a cover letter for a real job application. Ground every
claim in the candidate profile below. Do not invent experience, companies,
dates, or numbers that aren't present. If something's missing, skip it.

CANDIDATE PROFILE
{profile_text}
{('Candidate name: ' + candidate_name) if candidate_name else ''}

JOB POSTING
Title: {title}
Company: {company}
Location: {location or 'not specified'}
Seniority: {seniority or 'not specified'}
Technologies: {', '.join(tech_list[:15]) if tech_list else 'not specified'}
Description:
{description or '(no description provided)'}

{('EXTRA INSTRUCTIONS FROM CANDIDATE: ' + custom_note) if custom_note else ''}

TONE: {tone_guidance}

RULES
- 3 to 4 short paragraphs. ~250 to 350 words total.
- First paragraph: why this role at this company specifically, grounded
  in one concrete thing from the JD.
- Middle paragraph(s): two or three specific matches between the
  candidate's actual experience and the JD. Name the technology,
  domain, or outcome each time.
- Closing: one clear sentence of interest plus availability / next step.
- Do NOT invent metrics, former employers, or credentials not in the
  profile. If the profile has numbers, you may reuse them verbatim.
- Do NOT use the phrase "I am writing to apply" or any similar filler.
- No bullet points. No headers. Plain prose only.
- Write in British English (e.g. 'organise', 'optimise', no Oxford commas).
- Do not use em dashes.

Output ONLY the letter body. No preamble, no signature block, no
subject line. Start with the salutation ("Dear Hiring Team," is fine
when the posting doesn't name one)."""

            try:
                text = llm_module.query(
                    prompt,
                    task="default",
                    model=model_override,
                    temperature=0.55,
                    timeout=180,
                )
            except Exception as e:
                logger.exception("Cover letter LLM call failed")
                self._send_json({"ok": False, "error": f"LLM error: {e}"}, 502)
                return

            text = (text or "").strip()
            if not text:
                self._send_json({"ok": False, "error": "Model returned an empty letter."}, 502)
                return

            # Persist for later review. Same idea as digests/fit_gaps dirs.
            saved_to = None
            try:
                import re as _re
                out_dir = DATA_DIR / "cover_letters"
                out_dir.mkdir(parents=True, exist_ok=True)
                slug_source = f"{company}_{title}"
                slug = _re.sub(r"[^A-Za-z0-9]+", "_", slug_source).strip("_")[:80] or "untitled"
                stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                out_path = out_dir / f"{slug}_{stamp}.md"
                header = (
                    f"# Cover letter: {title} at {company}\n\n"
                    f"_Generated {stamp} UTC_\n\n"
                    f"**Tone:** {tone}  \n"
                    f"**Match score:** "
                    f"{int((job.get('_match_score') or 0) * 100)}%\n\n"
                    f"---\n\n"
                )
                _atomic_write_text(out_path, header + text + "\n")
                saved_to = str(out_path)
            except Exception as e:
                logger.warning("cover letter persist failed: %s", e)

            self._send_json({
                "ok": True,
                "text": text,
                "saved_to": saved_to,
                "tone": tone,
                "model": model_override or llm_module.get_model("default"),
            })

        elif path == "/api/resume":
            from core import resume_store, resume_profile
            try:
                payload = json.loads(body or b"{}")
                filename = payload.get("filename") or ""
                content_b64 = payload.get("content_base64") or ""
                if not filename or not content_b64:
                    raise ValueError("filename and content_base64 are required")
                meta = resume_store.save_upload(DATA_DIR, filename, content_b64)
                # New resume means the old structured profile is stale.
                resume_profile.invalidate(DATA_DIR)
                self._send_json({"ok": True, "metadata": meta})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                logger.exception("Resume upload failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/resume/notes":
            from core import resume_store, resume_profile
            try:
                payload = json.loads(body or b"{}")
                notes = payload.get("notes", "")
                result = resume_store.save_notes(DATA_DIR, notes)
                # Notes influence the parsed profile (targets, domains),
                # so invalidate the cache so the next fetch re-runs the LLM.
                resume_profile.invalidate(DATA_DIR)
                self._send_json({"ok": True, **result})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/resume/clear":
            from core import resume_store, resume_profile
            resume_store.clear(DATA_DIR)
            resume_profile.invalidate(DATA_DIR)
            self._send_json({"ok": True})

        elif path == "/api/reset-history":
            # Nuke per-cycle run history (matches, digests, parsed,
            # stats, caches) while preserving user-owned data (resume,
            # preferences, tracker, decisions, story bank).
            #
            # The allow-list + path-traversal guard live in
            # core.reset_history. This route is a thin wrapper so
            # the delete semantics stay in ONE obvious file.
            from core import reset_history as _reset
            try:
                result = _reset.reset_history(DATA_DIR)
                self._send_json(result, 200 if result.get("ok") else 500)
            except Exception as e:
                logger.exception("reset-history failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/tailor-resume":
            # Generate ONE tailored resume (HTML + PDF) for the single
            # match passed in the body. Delegates to core.resume_tailor
            # which:
            #   1. Pulls the parsed profile (core.resume_profile) and
            #      raw resume text (core.resume_store) from disk, so
            #      the caller only has to send the JOB payload.
            #   2. Runs ONE LLM pass that returns structured JSON
            #      (summary / bullets / skills). Never HTML.
            #   3. Renders HTML deterministically in Python, then
            #      writes a PDF via weasyprint (fast) or Playwright
            #      (fallback if weasyprint's GTK DLLs are missing on
            #      Windows, which is the common case).
            #
            # Body shape (everything except title/company is optional):
            #   { "title": str, "company": str, "url": str,
            #     "description": str, "technologies": [str, ...] }
            try:
                payload = json.loads(body or b"{}")
            except Exception as e:
                self._send_json({"ok": False, "error": f"bad json: {e}"}, 400)
                return

            from core import resume_profile, resume_store, resume_tailor

            profile = resume_profile.get_cached_profile(DATA_DIR) or {}
            resume_text = resume_store.get_profile_text(DATA_DIR) or ""

            if not profile:
                self._send_json(
                    {"ok": False, "error": "No parsed resume profile. "
                                          "Upload a resume in Settings first."},
                    400,
                )
                return
            if not resume_text.strip():
                self._send_json(
                    {"ok": False, "error": "Resume text is empty. Re-upload."},
                    400,
                )
                return

            try:
                result = resume_tailor.tailor_resume(
                    data_dir=DATA_DIR,
                    profile=profile,
                    resume_text=resume_text,
                    job=payload,
                )
            except Exception as e:
                logger.exception("tailor-resume failed")
                self._send_json({"ok": False, "error": str(e)}, 500)
                return

            if not result.get("ok"):
                self._send_json(result, 500)
                return

            # Convert absolute paths to filenames the UI can pass
            # back to /api/resumes/download for a clean download link.
            html_file = Path(result["html_path"]).name if result.get("html_path") else None
            pdf_file = Path(result["pdf_path"]).name if result.get("pdf_path") else None
            self._send_json({
                "ok": True,
                "html_file": html_file,
                "pdf_file": pdf_file,
                "pdf_method": result.get("pdf_method"),
                "summary": result.get("summary", ""),
            })

        elif path == "/api/resume/reparse":
            from core import resume_profile
            try:
                payload = json.loads(body or b"{}") if body else {}
                model = payload.get("model") or None
                profile = resume_profile.parse_to_profile(DATA_DIR, force=True, model=model)
                if profile.get("error"):
                    self._send_json({"ok": False, "error": profile["error"]}, 400)
                    return
                self._send_json({"ok": True, "profile": profile})
            except Exception as e:
                logger.exception("Resume reparse failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/reactions":
            from core import decision_store
            try:
                payload = json.loads(body or b"{}")
                result = decision_store.record_reaction(
                    DATA_DIR,
                    title=payload.get("title") or "",
                    company=payload.get("company") or "",
                    action=payload.get("action") or "",
                    url=payload.get("url") or "",
                    score=payload.get("score") or 0,
                    notes=payload.get("notes") or "",
                )
                self._send_json({"ok": True, **result})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                logger.exception("Reaction save failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/matches/state":
            # Flip one of the per-user booleans on a stored match.
            # Body: {"key": "<registry key>", "field": "seen"|"dismissed"|"starred", "value": true|false}
            # Or, for convenience when the caller doesn't have the
            # registry key yet, it can send {"title": ..., "company": ..., "location": ...}
            # and we'll recompute the key. Key is preferred - cheaper
            # and more exact.
            from core.match_registry import get_registry
            from core import dedupe as dedupe_mod
            try:
                data = json.loads(body or b"{}")
                field = (data.get("field") or "").strip().lower()
                if field not in ("seen", "dismissed", "starred"):
                    raise ValueError("field must be one of: seen, dismissed, starred")
                value = bool(data.get("value"))
                key = data.get("key")
                if not key:
                    # Recompute from title/company/location the same way
                    # dedupe does so the UI can get away without the key.
                    tmp_payload = {
                        "title": data.get("title") or "",
                        "company": data.get("company") or "",
                        "location": data.get("location") or "",
                    }
                    key = dedupe_mod._dedupe_key(tmp_payload)
                if not key or key == "||":
                    raise ValueError("key or (title+company) is required")
                reg = get_registry(DATA_DIR)
                reg.reload()
                entry = reg.set_state(key, field, value)
                if entry is None:
                    self._send_json({"ok": False, "error": "unknown match key"}, 404)
                else:
                    self._send_json({
                        "ok": True,
                        "key": key,
                        "field": field,
                        "value": value,
                        "seen": entry.get("seen"),
                        "dismissed": entry.get("dismissed"),
                        "starred": entry.get("starred"),
                    })
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                logger.exception("match state update failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/match/rationale":
            # On-demand "Why this match?" rationale. Body:
            #   {"payload": {...full match packet payload...},
            #    "force":   false}
            # The client already has the payload from /api/matches, so we
            # don't need to look it up again. Profile text comes from the
            # live orchestrator's match agent if running, else from
            # config.match.profile_text.
            from core import rationale
            try:
                data = json.loads(body or b"{}")
                payload = data.get("payload") or {}
                if not isinstance(payload, dict) or not payload.get("title"):
                    raise ValueError("payload.title is required")

                cfg = load_json(CONFIG_FILE, {}) or {}
                match_cfg = cfg.get("match", {}) or {}

                # Prefer the live agent's profile (includes hot-applied
                # resume text) then fall back to the config snapshot.
                profile_text = ""
                orch = _pipeline_state.get("orchestrator")
                if orch is not None and getattr(orch, "match", None) is not None:
                    profile_text = getattr(orch.match, "profile_text", "") or ""
                if not profile_text:
                    profile_text = match_cfg.get("profile_text") or ""

                threshold = float(match_cfg.get("threshold") or 0.55)
                # analyze.model is the qwen3:8b reasoning slot by default,
                # which is the right size/latency trade-off here.
                model = (cfg.get("analyze") or {}).get("model")

                result = rationale.generate(
                    profile_text=profile_text,
                    payload=payload,
                    threshold=threshold,
                    model=model,
                    force=bool(data.get("force")),
                )
                status = 200 if result.get("ok") else 400
                self._send_json(result, status)
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                logger.exception("Rationale generation failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/ingest/test":
            # Sanity-check a single tenant slug against its ATS endpoint so
            # users can add a company and verify the URL works before
            # committing to config. Body: {"kind": "greenhouse"|"lever"|
            # "ashby", "slug": "stripe", "display": "Stripe" (ashby only)}.
            # Returns {ok, status_code, jobs_found, sample_title}.
            try:
                payload = json.loads(body or b"{}")
                kind = (payload.get("kind") or "").strip().lower()
                slug = (payload.get("slug") or "").strip()
                display = (payload.get("display") or slug or "").strip()
                if kind not in ("greenhouse", "lever", "ashby"):
                    self._send_json({"ok": False, "error": "kind must be greenhouse|lever|ashby"}, 400)
                    return
                if not slug:
                    self._send_json({"ok": False, "error": "slug required"}, 400)
                    return
                import requests as _req
                url = None
                sample = None
                jobs_found = 0
                if kind == "greenhouse":
                    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
                    r = _req.get(url, timeout=10)
                    if r.status_code == 200:
                        data = r.json()
                        jobs_found = len(data.get("jobs", []) or [])
                        if jobs_found:
                            sample = (data["jobs"][0] or {}).get("title")
                elif kind == "lever":
                    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
                    r = _req.get(url, timeout=10)
                    if r.status_code == 200:
                        data = r.json() or []
                        jobs_found = len(data) if isinstance(data, list) else 0
                        if jobs_found:
                            sample = (data[0] or {}).get("text")
                elif kind == "ashby":
                    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
                    r = _req.get(url, timeout=10)
                    if r.status_code == 200:
                        data = r.json() or {}
                        postings = data.get("jobs", []) or []
                        jobs_found = len(postings)
                        if postings:
                            sample = (postings[0] or {}).get("title")
                self._send_json({
                    "ok": r.status_code == 200,
                    "kind": kind,
                    "slug": slug,
                    "display": display,
                    "url": url,
                    "status_code": r.status_code,
                    "jobs_found": jobs_found,
                    "sample_title": sample,
                })
            except Exception as e:
                logger.exception("Tenant test failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/chat":
            from core import chat as chat_module
            try:
                payload = json.loads(body or b"{}")
                messages = payload.get("messages") or []
                if not isinstance(messages, list):
                    raise ValueError("messages must be a list of {role, content}")

                # Let the client override the chat model via optional field;
                # default comes from config.chat.model then falls back to qwen3:14b.
                cfg = load_json(CONFIG_FILE, {})
                default_model = (cfg.get("chat") or {}).get("model") or "qwen3:14b"
                model = payload.get("model") or default_model

                # Screen-state context from the UI (view, selectedJob, filters).
                # Optional - chat_once tolerates None / partial payloads.
                context = payload.get("context") if isinstance(payload.get("context"), dict) else None

                result = chat_module.chat_once(DATA_DIR, messages, model=model, context=context)
                self._send_json({"ok": True, **result})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                logger.exception("Chat failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        else:
            self._send_json({"error": "Unknown POST endpoint"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        logger.debug("API: %s", format % args)


def set_orchestrator(orch):
    """Called by main.py to give the server access to the orchestrator."""
    _pipeline_state["orchestrator"] = orch


def start_server(port=8099, host="127.0.0.1"):
    # Bind to 127.0.0.1 by default so the API is local-only (not exposed
    # on the LAN) and so the Vite proxy at http://127.0.0.1:8099 always
    # finds it. Override with host="0.0.0.0" if you want LAN access.
    #
    # Bind errors used to get swallowed inside the daemon thread, leaving
    # the UI sitting at "DISCONNECTED" with no clue why. Now we log the
    # bind loudly and - critically - print to stdout so users launching
    # via start.ps1 see the real reason (port in use from a zombie, EADDR
    # blocked by a VPN, etc.) without having to tail the log file.
    import socket
    try:
        server = ThreadingHTTPServer((host, port), SentinelHandler)
    except OSError as e:
        msg = f"SENTINEL API could not bind to {host}:{port} ({e})."
        hint = (
            "Another process is probably holding the port. Close any old "
            "SENTINEL window and try again, or set api_port in config.json "
            "to something else (e.g. 8100)."
        )
        logger.error(msg)
        print(f"[sentinel.server] ERROR: {msg}\n[sentinel.server] HINT: {hint}", flush=True)
        raise
    logger.info("SENTINEL API on http://%s:%d", host, port)
    print(f"[sentinel.server] listening on http://{host}:{port}", flush=True)
    server.serve_forever()


def start_server_thread(port=8099, host="127.0.0.1"):
    """Bind + serve in a background thread. We pre-probe the port on the
    main thread so a bind failure surfaces synchronously instead of
    getting swallowed inside the daemon thread."""
    import socket
    # Pre-bind probe - the actual server thread still binds the real
    # socket, but this catches the common "zombie SENTINEL holds port
    # 8099" case before we hand off to the daemon thread (whose
    # exceptions would vanish silently).
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
        probe.close()
    except OSError as e:
        msg = f"Port {host}:{port} is unavailable ({e})."
        hint = (
            "Close the other SENTINEL window (Task Manager > sentinel.exe / "
            "python.exe), or change api_port in config.json."
        )
        logger.error(msg)
        print(f"[sentinel.server] ERROR: {msg}\n[sentinel.server] HINT: {hint}", flush=True)
        raise
    t = threading.Thread(target=start_server, args=(port, host), daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    start_server()
