"""
Local Recruiting Ops API server.

Single-file HTTP layer over Python stdlib `http.server`. Each route
is a branch in `do_GET` / `do_POST` — no framework, no decorators,
nothing to learn. Reads from disk (data/, config.json), writes via
core.io_safe atomic writes, and proxies pipeline control to the
running orchestrator instance set by main.py via set_orchestrator().
"""

import json
import mimetypes
import os
import threading
import logging
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

logger = logging.getLogger("lro.server")

DATA_DIR = Path("data")
CONFIG_FILE = Path("config.json")
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


def _log_file_path() -> Path:
    cfg = load_json(CONFIG_FILE, {}) or {}
    rel = (cfg.get("logging") or {}).get("file", "logs/lro.log")
    p = Path(rel)
    if not p.is_absolute():
        p = app_paths.runtime_dir() / p
    return p


def _tail_text_lines(path: Path, n: int) -> list[str]:
    """Return the last *n* lines without reading the whole file."""
    if not path.is_file():
        return []
    n = max(1, min(int(n), 5000))
    block = 8192
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            read_size = min(block, size)
            size -= read_size
            fh.seek(size)
            data = fh.read(read_size) + data
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-n:]


def _refresh_match_display(row: dict) -> None:
    """Re-derive calibrated display scores from stored raw fit scores."""
    score = row.get("_match_score")
    if score is None:
        return
    try:
        raw = float(score)
    except (TypeError, ValueError):
        return
    provenance = row.get("_match_provenance") or "embed"
    if provenance == "embed":
        from agents.match import calibrate_score
        row["_match_score_display"] = round(calibrate_score(raw), 4)
    else:
        row["_match_score_display"] = round(raw, 4)


# mtime-keyed parse cache for the small set of JSON files that
# /api/status reads on every 2-second heartbeat poll. The same
# cycle_times.json gets re-parsed ~30 times per minute even when
# nothing changed; this short-circuits any read whose mtime hasn't
# moved. Trade-off: an external process that overwrites the file
# without changing mtime (rare; happens on some COW filesystems
# during snapshots) would be invisible — acceptable for a single-
# user local-only app.
_load_json_cached_state: dict = {}


def load_json_cached(path: Path, default=None):
    """Like load_json but returns a cached parse if the file's mtime
    hasn't changed since the last read. Per-path cache, no eviction —
    we only hot-poll a handful of files."""
    try:
        mtime = path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        return default
    key = str(path)
    cached = _load_json_cached_state.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    if mtime == 0.0:
        # File doesn't exist; cache the negative so we don't stat() in
        # a hot loop. Will be evicted on the next stat-success.
        _load_json_cached_state[key] = (0.0, default)
        return default
    try:
        parsed = json.loads(path.read_text())
    except Exception:
        return default
    _load_json_cached_state[key] = (mtime, parsed)
    return parsed


def _last_cycle_for_status(cycle_times):
    """Return the most recent cycle funnel stats for /api/status.

    Prefer the in-memory copy from the last finished run (richer —
    includes url_dedupe, mode, tiers). Fall back to cycle_times.json
    after a server restart so the Brief tab still has something to show.
    """
    live = _pipeline_state.get("last_cycle")
    if live:
        return live
    if not cycle_times:
        return None
    entry = cycle_times[-1]
    if not isinstance(entry, dict):
        return None
    return {
        "cycle": entry.get("cycle"),
        "ingested": entry.get("ingested", 0),
        "parsed": entry.get("parsed", 0),
        "qa_pass": entry.get("qa_pass", 0),
        "qa_fail": entry.get("qa_fail", 0),
        "fake_blocked": entry.get("fake_blocked", 0),
        "new_jobs": entry.get("new_jobs", 0),
        "matches": entry.get("matches", 0),
        "fit_gaps": entry.get("fit_gaps", 0),
        "resumes": entry.get("resumes", 0),
        "ingest_seconds": entry.get("ingest_seconds"),
        "pipeline_seconds": entry.get("pipeline_seconds"),
        "duration_seconds": entry.get("seconds"),
    }


def _run_single_cycle(tiers=("fast",)):
    """Run one pipeline cycle in a background thread.
    Caller (POST /api/run-cycle) has already set cycle_in_progress=True
    under _state_lock; we are responsible for clearing it on the way
    out. (Historical: a separate /api/run-scraper route used to call
    this with the now-removed "slow" Playwright tier. The tier
    parameter still accepts "slow" but it's a no-op.)

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

        if path == "/api/market":
            self._send_json(load_json(MARKET_FILE, []))

        elif path == "/api/logs":
            try:
                n = int(parse_qs(parsed.query).get("n", ["400"])[0])
            except Exception:
                n = 400
            log_path = _log_file_path()
            lines = _tail_text_lines(log_path, n)
            self._send_json({
                "path": str(log_path),
                "lines": lines,
                "exists": log_path.is_file(),
            })

        elif path == "/api/cycle-history":
            # Timeline for the History tab. Same source as status averages,
            # newest first and capped so the dashboard never pulls an
            # unbounded JSON blob from a long-lived install.
            all_cycles = load_json_cached(DATA_DIR / "cycle_times.json", []) or []
            try:
                n = int(parse_qs(parsed.query).get("n", ["500"])[0])
            except Exception:
                n = 500
            n = max(1, min(n, 2000))
            self._send_json(list(reversed(all_cycles))[:n])

        elif path == "/api/system-info":
            # System capability snapshot for the Settings UI's Models
            # picker. Surfaces whether CUDA is wired up, which GPU the
            # user has, and approximate VRAM — so the picker can give a
            # data-backed recommendation ("you have 16 GB, qwen3:14b
            # fits comfortably") instead of generic advice. Local only,
            # no external network. Probes are bounded (1-3s) so a hung
            # nvidia driver can't stall the endpoint.
            #
            # Returns:
            #   { "ok": True, "gpu": {...} | None, "torch": {...}, "host": str }
            # The FE renders a small badge / banner from this data. If
            # any sub-probe fails we still return ok:True with the field
            # set to None / unknown — better than 500-ing the whole call.
            import shutil as _shutil, subprocess as _sp
            # No `Any` type annotation here — `typing` isn't imported at
            # module scope and we don't want to make the route's first
            # call 500 on a NameError. The dict's value types are mixed
            # (bool / str / None) and Python doesn't need the hint to
            # behave correctly.
            torch_info = {"available": False, "version": None, "cuda": None, "device": "cpu"}
            try:
                import torch as _torch
                torch_info["version"] = _torch.__version__
                torch_info["cuda"]    = _torch.version.cuda
                torch_info["available"] = bool(_torch.cuda.is_available())
                torch_info["device"]  = "cuda" if torch_info["available"] else "cpu"
            except Exception as _e:
                torch_info["error"] = str(_e)
            gpu_info: dict | None = None
            if _shutil.which("nvidia-smi"):
                try:
                    r = _sp.run(
                        ["nvidia-smi",
                         "--query-gpu=name,memory.total,driver_version,compute_cap",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=3,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        # First line = first GPU. Multi-GPU rigs are rare
                        # on a job-search tool's audience; the first GPU
                        # is what PyTorch will use anyway by default.
                        first = r.stdout.strip().splitlines()[0]
                        parts = [p.strip() for p in first.split(",")]
                        if len(parts) >= 2:
                            try:
                                vram_mib = int(parts[1])
                                vram_gb = round(vram_mib / 1024, 1)
                            except ValueError:
                                vram_gb = None
                            gpu_info = {
                                "name": parts[0],
                                "vram_gb": vram_gb,
                                "driver_version": parts[2] if len(parts) > 2 else None,
                                "compute_capability": parts[3] if len(parts) > 3 else None,
                            }
                except Exception as _e:
                    gpu_info = {"error": str(_e)}
            self._send_json({
                "ok": True,
                "gpu": gpu_info,
                "torch": torch_info,
                "host": "127.0.0.1",
            })

        elif path == "/api/ollama-models":
            # Discovery endpoint for the Settings UI's model picker.
            # Hits the local Ollama /api/tags endpoint and returns the
            # list of installed model names so the UI can render a real
            # dropdown instead of asking the user to type model strings
            # they might mistype. Local-only, no external network.
            #
            # Returns: {"models": ["qwen3:14b", "phi4-reasoning:14b", ...],
            #           "host": "http://127.0.0.1:11434", "ok": bool,
            #           "error": str?}
            try:
                import urllib.request as _urlreq
                import json as _json
                ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
                req = _urlreq.Request(f"{ollama_host.rstrip('/')}/api/tags",
                                      headers={"Accept": "application/json"})
                with _urlreq.urlopen(req, timeout=5) as resp:
                    body = _json.loads(resp.read().decode("utf-8"))
                names = sorted({
                    str(m.get("name") or "").strip()
                    for m in (body.get("models") or [])
                    if (m or {}).get("name")
                })
                self._send_json({"ok": True, "models": names, "host": ollama_host})
            except Exception as e:
                # Ollama down / unreachable. Return ok:false so the UI can
                # show a helpful "start Ollama" hint instead of a generic 500.
                self._send_json({
                    "ok": False,
                    "models": [],
                    "host": os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
                    "error": str(e),
                })


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
                    # Matches tab = match tier only. Starred/dismissed state
                    # is preserved on entries the user already touched.
                    tier = payload.get("_match_tier")
                    if tier and tier != "match" and not e.get("starred"):
                        continue
                    if tier is None and not payload.get("_is_match") and not e.get("starred"):
                        continue
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
                    _refresh_match_display(row)
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
                    persisted = load_json_cached(DATA_DIR / "match_stats.json", {}) or {}
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
            # mtime-cached read — see load_json_cached. Heartbeat polls
            # this every 2s; the file only changes at cycle boundaries
            # (every few minutes), so re-parsing it 30 times a minute
            # is wasted work.
            cycle_times = load_json_cached(DATA_DIR / "cycle_times.json", [])
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
                    url_dedupe["registry_size"] = len(load_json_cached(seen_urls_path, []) or [])
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

            # Match-registry size. Replaces an old data/matches/*.json
            # file-glob that counted a directory the registry layer
            # never writes to (it always returned 0/1). get_registry is
            # a process-wide singleton shared with the orchestrator, so
            # stats() reads the warm in-memory dict — no disk hit on
            # this 2s-polled path.
            try:
                from core.match_registry import get_registry
                matches_count = int(get_registry(DATA_DIR).stats().get("total", 0))
            except Exception:
                matches_count = 0

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
                "last_cycle": _last_cycle_for_status(cycle_times),
                "last_cycle_ts": last_cycle_ts,
                "matches_count": matches_count,
                "cycles_logged": len(load_json_cached(MARKET_FILE, [])),
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
            # Surface the full live route table on 404 — quick triage
            # for "did I typo the endpoint or is the backend stale?"
            # If you add or remove a route, update this list.
            self._send_json({
                "error": "Unknown endpoint",
                "endpoints": [
                    "GET  /api/status",
                    "GET  /api/market",
                    "GET  /api/cycle-history",
                    "GET  /api/logs",
                    "GET  /api/matches",
                    "GET  /api/config",
                    "GET  /api/resume", "GET  /api/resume?full=1",
                    "GET  /api/resume/profile",
                    "GET  /api/ollama-models",
                    "GET  /api/system-info",
                    "POST /api/run-cycle",
                    "POST /api/reset-history",
                    "POST /api/config",
                    "POST /api/resume",
                    "POST /api/resume/profile",
                    "POST /api/resume/reparse",
                    "POST /api/reactions",
                    "POST /api/cover-letter",
                    "POST /api/tailor-resume",
                    "POST /api/summarize",
                    "POST /api/ingest/test",
                ],
            }, status=404)

        else:
            # Non-API path: serve the built UI out of lro/ui/dist so
            # the normal local app has one visible origin on :8099. Vite
            # on :3000 is opt-in via LRO_DEV_UI=1 for frontend work.
            self._serve_static(path)

    def _serve_static(self, path: str):
        root = app_paths.static_dir()
        if not root.is_dir():
            self._send_json({
                "error": "UI not built",
                "hint": "Run: cd lro/ui ; npm install ; npm run build",
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

        if path == "/api/run-cycle":
            # Only /api/run-cycle is supported now. The legacy
            # /api/run-scraper (slow tier — Playwright SPA scrapers)
            # and /api/run-all (fast + slow combined) routes were
            # kept on the matcher for v1 compatibility; both have
            # been removed since the slow tier was retired with
            # the Apple/Meta/Microsoft scrapers (see agents/ingest
            # docstring for the TOS rationale).
            tiers = ("fast",)
            label = "Pipeline"

            # Reject manual cycles until the user has uploaded a
            # resume — otherwise the pipeline runs against an empty
            # profile and produces zero matches for reasons that
            # aren't obvious. Was previously gated on a wizard
            # "setup_completed" flag; the wizard is gone, the
            # resume IS the only real precondition.
            from core import resume_store
            if not (resume_store.read_current(DATA_DIR) or {}).get("has_resume"):
                self._send_json({
                    "ok": False,
                    "error": "No resume on file. Upload one in Settings before running the pipeline.",
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
                        if "digest" in models:
                            existing.setdefault("digest", {})["model"] = models["digest"]
                    # Nested-stage shape — what the React Settings form
                    # actually sends. Each block is shallow-merged so the
                    # form can patch one stage's model without wiping
                    # adjacent fields (e.g. `match.threshold`). Unknown
                    # keys inside a stage are ignored so the UI can add
                    # more tasks later without a server bump.
                    for stage in ("parse", "match", "analyze", "digest", "cover_letter"):
                        if stage in new_config and isinstance(new_config[stage], dict):
                            block = existing.setdefault(stage, {})
                            for k, v in new_config[stage].items():
                                # Skip None — that's what react-hook-form sends
                                # for an optional field the user didn't touch.
                                if v is not None:
                                    block[k] = v
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
                        # Company tenant lists + big-tech toggles + role
                        # keywords. Each field is independently optional so
                        # the UI can patch a single list without wiping
                        # neighbours.
                        ing = existing.setdefault("ingest", {})
                        src = new_config["ingest"]
                        if "role_keywords" in src and isinstance(src["role_keywords"], list):
                            # Same shape the top-level role_keywords write
                            # produces. Backend accepts either; the React
                            # form happens to send nested under ingest.
                            ing["role_keywords"] = [
                                str(s).strip().lower() for s in src["role_keywords"]
                                if str(s).strip()
                            ]
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
                        # Per-source toggles. Keep this list in sync with
                        # the bespoke scrapers in agents/ingest.py and the
                        # CustomSourcesPanel in
                        # lro/ui/src/components/settings/CompaniesSection.tsx.
                        # Toggles NOT listed here are silently ignored on
                        # save. History of removals: enable_apple /
                        # enable_meta / enable_microsoft (TOS), enable_netflix
                        # (their endpoint died), enable_salesforce /
                        # enable_ibm / enable_cisco (Workday endpoints
                        # drifted). Don't add new toggles back without
                        # also adding the scraper + UI row.
                        for tog in (
                            "enable_amazon", "enable_google",
                            "enable_nvidia", "enable_adobe", "enable_intel",
                        ):
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

            # Persist for later review (user-facing artefact, like digests/).
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

        elif path == "/api/tailor-resume":
            # Tailor the user's resume to ONE specific job, on demand.
            # Mirrors the cover-letter endpoint: load the parsed resume
            # profile, run one local LLM call via ResumeGenerator.tailor(),
            # return the tailored summary + bullets + keywords + cover
            # note as text. Resume tailoring used to run for the top 5
            # every cycle (the slowest stage); it's now on-demand so the
            # user pays the LLM cost only when they actually want it.
            #
            # Body: {"job": {title, company, description, technologies?,
            #                _fit_gap?, _match_score?}}
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

            from core import resume_profile as rp_module
            from core import resume_store, user_store
            from agents.resume import ResumeGenerator

            profile = rp_module.get_cached_profile(DATA_DIR)
            profile_text = rp_module.profile_to_text(profile) if profile else ""
            if not profile_text:
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

            user = user_store.load(DATA_DIR) or {}
            candidate_name = (user.get("name") or (profile or {}).get("name") or "").strip()

            # Build a fit_gap-shaped dict. If the row has already been
            # through ANALYZE (top matches have a _fit_gap), reuse its
            # matched / gap skills for richer tailoring; otherwise
            # ResumeGenerator.tailor() degrades gracefully on empty lists.
            fg = job.get("_fit_gap") or {}
            fit_gap = {
                "title": title,
                "company": company,
                "description": (job.get("description") or "")[:4000],
                "matched_skills": fg.get("matched") or [],
                "gaps": [{"skill": g} for g in (fg.get("gaps") or [])],
                "talking_points": [fg["rationale"]] if fg.get("rationale") else [],
                "match_percentage": int((job.get("_match_score") or 0) * 100),
            }

            try:
                gen = ResumeGenerator({
                    "name": candidate_name or "Candidate",
                    "email": (user.get("email") or "").strip(),
                    "profile": profile_text[:4000],
                    "output_dir": str(DATA_DIR / "resumes"),
                })
                tailored = gen.tailor(fit_gap)
            except Exception as e:
                logger.exception("Resume tailoring failed")
                self._send_json({"ok": False, "error": f"LLM error: {e}"}, 502)
                return

            if not tailored:
                self._send_json({
                    "ok": False,
                    "error": "Model returned an unparseable resume draft. Try again.",
                }, 502)
                return

            self._send_json({
                "ok": True,
                "summary": tailored.get("summary", ""),
                "bullets": tailored.get("bullets", []) or [],
                "keywords": tailored.get("keywords", []) or [],
                "cover_note": tailored.get("cover_note", ""),
            })

        elif path == "/api/summarize":
            # Generate (or return a cached) 3-4 sentence summary of a
            # job description. Powers the "Summarize" button on the
            # MatchDetail panel. JDs from ATSes are routinely 4-10 KB
            # of HTML-formatted boilerplate; even after the FE-side
            # `jdTrim` cuts the obvious filler, users wanted a true
            # narrative summary they can scan in 5 seconds.
            #
            # Cache shape
            # -----------
            # The result is written back into the match_registry entry
            # at `payload._summary` (string). On repeat calls for the
            # same URL we short-circuit and return the cached blob —
            # this matters because the FE button is "click to expand"
            # and we don't want to burn an LLM call on every click.
            #
            # Body: {"url": string} — the job's URL, used to look up
            #       the registry entry. We accept arbitrary URLs and
            #       gracefully fall back to summarising the body's
            #       inline `description` if the URL isn't in the
            #       registry yet (e.g. a row that just appeared via
            #       the live registry stream and the user clicked
            #       Summarize before the registry persisted).
            try:
                payload = json.loads(body or b"{}")
            except Exception as e:
                self._send_json({"ok": False, "error": f"bad json: {e}"}, 400)
                return
            url = (payload.get("url") or "").strip()
            inline_desc = (payload.get("description") or "").strip()
            inline_title = (payload.get("title") or "").strip()
            inline_company = (payload.get("company") or "").strip()
            force = bool(payload.get("force"))

            from core.match_registry import get_registry
            from core import llm as llm_module

            reg = get_registry(DATA_DIR)
            reg.reload()
            entry = reg.get_by_url(url) if url else None
            entry_payload = (entry or {}).get("payload") or {}

            cached = entry_payload.get("_summary")
            if cached and not force:
                self._send_json({
                    "ok": True,
                    "summary": cached,
                    "cached": True,
                    "model": entry_payload.get("_summary_model") or "cached",
                })
                return

            description = (entry_payload.get("description") or inline_desc).strip()
            title = (entry_payload.get("title") or inline_title).strip()
            company = (entry_payload.get("company") or inline_company).strip()
            if not description:
                self._send_json({
                    "ok": False,
                    "error": "No description available to summarize.",
                }, 400)
                return

            # Cap the input so we don't hand the model a 10 KB blob.
            # 4 KB is plenty — the salient sections (responsibilities,
            # requirements) almost always fit. We've already stripped
            # HTML at parse time on most sources; for anything that
            # still has tags, the LLM tolerates them fine.
            description = description[:4000]

            prompt = f"""You are summarising a real job posting for a busy candidate.
Output a clean 3-4 sentence summary that covers, in order:
  1. What the role actually does day-to-day (1 sentence)
  2. The key technical / domain requirements (1-2 sentences)
  3. The seniority signal and any standout detail like comp band, scope,
     or unusual responsibility (1 sentence)

Rules:
- No preamble, no "This role is...", no headers, no bullets — just prose.
- 3-4 sentences total. Hard cap.
- If the JD is thin or generic, say so honestly in the summary rather
  than padding.
- British English (organise, optimise, no Oxford commas). No em dashes.

JOB
Title: {title or 'unspecified'}
Company: {company or 'unspecified'}

Description:
{description}

SUMMARY:"""

            try:
                text = llm_module.query(
                    prompt,
                    task="default",
                    temperature=0.2,
                    timeout=120,
                )
            except Exception as e:
                logger.exception("Summarize LLM call failed")
                self._send_json({"ok": False, "error": f"LLM error: {e}"}, 502)
                return

            text = (text or "").strip()
            if not text:
                self._send_json({"ok": False, "error": "Model returned empty summary."}, 502)
                return

            # Persist into the registry so the next click is free. If
            # the entry doesn't exist (FE clicked before registry
            # persisted) we skip persistence — better to return the
            # summary uncached than to fail the click.
            persisted = False
            if entry is not None:
                try:
                    reg.set_payload_field(url, "_summary", text)
                    reg.set_payload_field(url, "_summary_model",
                                          llm_module.get_model("default"))
                    persisted = True
                except Exception as e:
                    logger.warning("summary persist failed: %s", e)

            self._send_json({
                "ok": True,
                "summary": text,
                "cached": False,
                "persisted": persisted,
                "model": llm_module.get_model("default"),
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

        elif path == "/api/resume/profile":
            # User-edited override of the parsed profile. Body is a
            # partial dict — only the fields the user wants to change.
            # See core/resume_profile.save_user_override for the
            # editable-field allowlist.
            try:
                from core import resume_profile as _rp
                patch = json.loads(body) if body else {}
                result = _rp.save_user_override(DATA_DIR, patch)
                if "error" in result:
                    self._send_json({"ok": False, **result}, 400)
                else:
                    self._send_json({"ok": True, "profile": result})
            except json.JSONDecodeError:
                self._send_json({"ok": False, "error": "Invalid JSON body"}, 400)
            except Exception as e:
                logger.exception("save_user_override failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/reset-history":
            # Nuke per-cycle run history (matches, digests, parsed,
            # stats, caches) while preserving user-owned data (resume,
            # preferences, tracker, decisions, story bank).
            #
            # The allow-list + path-traversal guard live in
            # core.reset_history. This route is a thin wrapper so
            # the delete semantics stay in ONE obvious file.
            #
            # Cycle guard: refuse to reset while a cycle is actively
            # writing data, otherwise we wipe match_registry.json + the
            # parsed/ directory while a stage is mid-flight and the
            # downstream stages read empty files.
            #
            # We use ONLY `_pipeline_state["cycle_in_progress"]` here —
            # not the orchestrator's `progress.stage_label`. The flag
            # is reset by `_run_single_cycle`'s finally block, so it's
            # always accurate even if a cycle crashed. The stage_label,
            # by contrast, can drift: if a cycle errored partway
            # through and the orchestrator never reached its
            # `_set_stage("idle", ...)` call, stage_label sticks at
            # "MATCHING" / "ANALYZING" forever and locks the user out
            # of reset until they restart the launcher. That was the
            # "I haven't run a cycle, why is reset blocked?" report.
            #
            # Body field `force: true` skips the guard entirely. The UI
            # uses this when the user retries after seeing the 409 a
            # second time (the second 409 is almost always a stuck
            # flag, not a real cycle).
            try:
                body_data = json.loads(body) if body else {}
            except Exception:
                body_data = {}
            force = bool((body_data or {}).get("force"))
            if not force and bool(_pipeline_state["cycle_in_progress"]):
                self._send_json({
                    "ok": False,
                    "error": "Cycle in progress — reset is blocked while a stage is writing data. Wait for it to finish (header shows the live stage), then try again. If the header says idle but you keep seeing this, retry once more — the UI will pass a force flag to bust a stuck guard.",
                    "_can_force": True,
                }, 409)
                return
            from core import reset_history as _reset
            try:
                result = _reset.reset_history(DATA_DIR)
                self._send_json(result, 200 if result.get("ok") else 500)
            except Exception as e:
                logger.exception("reset-history failed")
                self._send_json({"ok": False, "error": str(e)}, 500)

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
            # Persist a Like / Pass / Star / Dismiss / Clear from the
            # match detail panel. Body shape mirrors what the React
            # useReact hook actually sends:
            #
            #   { "url": "<job url>", "reaction": "up"|"down"|"star"
            #                                     |"dismiss"|"clear" }
            #
            # We look up the registry entry by URL and flip the
            # per-user flags accordingly. Was previously two separate
            # endpoints (/api/reactions for an analytics-style log,
            # /api/matches/state for the actual flag flip) AND the UI
            # was calling a third path /decisions/react that no
            # handler answered, so every click looked successful in
            # the optimistic UI but was lost on the next refresh.
            # Consolidated into one canonical path.
            from core.match_registry import get_registry
            try:
                data = json.loads(body or b"{}")
                url = (data.get("url") or "").strip()
                reaction = (data.get("reaction") or "").strip().lower()
                if not url:
                    raise ValueError("url is required")
                if reaction not in ("up", "down", "star", "dismiss", "clear"):
                    raise ValueError(f"invalid reaction: {reaction!r}")

                reg = get_registry(DATA_DIR)
                reg.reload()
                # Linear scan by URL — registries here cap at a few k
                # entries, well under the threshold where this matters.
                target_key = None
                target_entry = None
                for entry in reg.all_entries():
                    if (entry.get("payload") or {}).get("url") == url:
                        target_key = entry.get("key")
                        target_entry = entry
                        break
                if not target_key:
                    self._send_json({"ok": False, "error": "url not in match registry"}, 404)
                    return

                # Reaction → flag mutations. Mirrors the optimistic
                # patches in lro/ui/src/hooks/useReact.applyReaction
                # so server state stays in lockstep with what the UI
                # showed the user the moment they clicked.
                if reaction == "up":
                    reg.set_state(target_key, "starred", True)
                    reg.set_state(target_key, "seen", True)
                elif reaction == "down":
                    reg.set_state(target_key, "dismissed", True)
                    reg.set_state(target_key, "seen", True)
                elif reaction == "star":
                    reg.set_state(target_key, "starred", not target_entry.get("starred"))
                    reg.set_state(target_key, "seen", True)
                elif reaction == "dismiss":
                    reg.set_state(target_key, "dismissed", not target_entry.get("dismissed"))
                    reg.set_state(target_key, "seen", True)
                elif reaction == "clear":
                    reg.set_state(target_key, "starred", False)
                    reg.set_state(target_key, "dismissed", False)

                self._send_json({"ok": True, "key": target_key})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                logger.exception("Reaction save failed")
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
        msg = f"Local Recruiting Ops API could not bind to {host}:{port} ({e})."
        hint = (
            "Another process is probably holding the port. Close any old "
            "Local Recruiting Ops window and try again, or set api_port in config.json "
            "to something else (e.g. 8100)."
        )
        logger.error(msg)
        print(f"[lro.server] ERROR: {msg}\n[lro.server] HINT: {hint}", flush=True)
        raise
    logger.info("Local Recruiting Ops API on http://%s:%d", host, port)
    print(f"[lro.server] listening on http://{host}:{port}", flush=True)
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
            "Close the other Local Recruiting Ops window (Task Manager > python.exe), "
            "or change api_port in config.json."
        )
        logger.error(msg)
        print(f"[lro.server] ERROR: {msg}\n[lro.server] HINT: {hint}", flush=True)
        raise
    t = threading.Thread(target=start_server, args=(port, host), daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    start_server()
