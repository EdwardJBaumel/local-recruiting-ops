"""
PREFLIGHT DEPENDENCY CHECK

Runs on wizard open. Probes everything SENTINEL needs to deliver a
good first experience and returns a structured report the UI can render
as ticks / crosses with per-check guidance.

Checks:
  a. ollama_running: can we reach http://127.0.0.1:11434 ?
  b. ollama_models: are the configured parse/match/analyze/digest models pulled?
  c. embeddings: is sentence-transformers importable? (packaged EXE
     path skips it deliberately - we surface that so the user knows the
     slower LLM match path is in effect, not just failing silently.)
  d. network: can we reach a well-known ATS endpoint (Greenhouse's
     public boards-api) ? Cheap signal that outbound HTTPS works.
  e. python: interpreter version (requirements.txt expects 3.11+).

Each check returns {state: ok/warn/fail/skipped, detail: str, fix: str}
so the UI can render actionable text without branching on codes.

All checks have hard timeouts so a wedged dependency can't make the
wizard hang. Designed to run in <3s end to end on a healthy box.
"""

import sys
import time
import json
import socket
import logging
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("lantern.preflight")

# 1.5s budget for any single HTTP probe. 11434 is local, so even slow
# machines should answer well under that. ATS probe might be slower on
# cold DNS but still fits.
_HTTP_TIMEOUT = 1.5

# Module-level cache of the last run_all() result. The UI polls
# /api/preflight after startup so we don't want to re-probe on every
# poll - cache for _CACHE_TTL_S so repeated polls are cheap, but not so
# long that stale results block a user who just started Ollama.
_CACHE_TTL_S = 5.0
_cache_lock = threading.Lock()
_cache = {"ts": 0.0, "models_key": None, "result": None}


def _ok(detail: str = "", fix: str = "") -> dict:
    return {"state": "ok", "detail": detail, "fix": fix}

def _warn(detail: str, fix: str = "") -> dict:
    return {"state": "warn", "detail": detail, "fix": fix}

def _fail(detail: str, fix: str = "") -> dict:
    return {"state": "fail", "detail": detail, "fix": fix}

def _skipped(detail: str, fix: str = "") -> dict:
    return {"state": "skipped", "detail": detail, "fix": fix}


def check_python() -> dict:
    """Interpreter version. <3.10 fails outright; 3.10 warns; 3.11+ ok."""
    v = sys.version_info
    label = f"Python {v.major}.{v.minor}.{v.micro}"
    if v < (3, 10):
        return _fail(f"{label} is below minimum (3.11+).",
                     "Install Python 3.11+ from https://python.org and recreate the venv.")
    if v < (3, 11):
        return _warn(f"{label} works but the project targets 3.11+.",
                     "Upgrade to Python 3.11 when convenient.")
    return _ok(label)


def check_ollama_running() -> dict:
    """Probe localhost:11434/api/tags. That endpoint is cheap (lists
    installed models) and failing to respond is the canonical 'Ollama
    isn't running' signal."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            r.read()
        return _ok("Ollama reachable on 127.0.0.1:11434")
    except urllib.error.URLError as e:
        return _fail(f"Ollama not reachable ({e.reason}).",
                     "Install from https://ollama.com and run: ollama serve")
    except socket.timeout:
        return _fail("Ollama connection timed out.",
                     "Check that `ollama serve` is running and no firewall blocks 11434.")
    except Exception as e:
        return _fail(f"Unexpected error: {e}",
                     "Try restarting Ollama.")


def check_ollama_models(required: list[str]) -> dict:
    """Cross-reference configured models against Ollama's /api/tags
    output. Returns ok when all present, warn when one or more missing
    (pipeline still runs with fallbacks), fail when Ollama is up but
    returned an empty model list (odd; something's wrong)."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
    except Exception as e:
        return _fail(f"Could not list Ollama models: {e}",
                     "Ensure Ollama is running and run: ollama list")
    installed = set()
    for m in data.get("models") or []:
        # Ollama returns names like "gemma4:26b"; match exactly.
        name = (m.get("name") or m.get("model") or "").strip()
        if name:
            installed.add(name)
            # Also allow matching by the bare model part before ':'
            installed.add(name.split(":")[0])
    required_clean = [m for m in (required or []) if m]
    missing = [m for m in required_clean if m not in installed and m.split(":")[0] not in installed]
    if not required_clean:
        return _skipped("No models configured to check.")
    if not missing:
        return _ok(f"All configured models present: {', '.join(required_clean)}")
    return _warn(
        f"Missing: {', '.join(missing)}. Pipeline will fall back to available models.",
        "To install: " + " ; ".join(f"ollama pull {m}" for m in missing),
    )


def check_embeddings() -> dict:
    """sentence-transformers is optional - the packaged EXE skips it on
    purpose to keep bundle size sane. Surface the state so users know
    whether they're on the fast (cosine) or slow (LLM fallback) match
    path rather than having the match agent log a warning."""
    try:
        import sentence_transformers  # noqa: F401
        return _ok("sentence-transformers installed (fast cosine match path)")
    except ImportError:
        return _warn(
            "sentence-transformers not installed; LLM fallback match path in use.",
            "Run: pip install sentence-transformers (~400 MB; optional).",
        )
    except Exception as e:
        return _warn(f"sentence-transformers import failed: {e}",
                     "Reinstall: pip install --force-reinstall sentence-transformers")


def check_network() -> dict:
    """Cheap signal that outbound HTTPS works. Greenhouse's boards-api
    is the most-scraped source and is rate-friendly on HEAD requests."""
    try:
        req = urllib.request.Request(
            "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=false",
            method="HEAD",
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            r.read()
        return _ok("Outbound HTTPS reachable (Greenhouse API)")
    except urllib.error.HTTPError as e:
        # A 4xx/5xx still proves the network works - we got a reply.
        if 400 <= e.code < 600:
            return _ok(f"Outbound HTTPS reachable (HTTP {e.code} from test endpoint)")
        return _warn(f"Unexpected HTTP status: {e.code}")
    except urllib.error.URLError as e:
        return _fail(f"Outbound network failed ({e.reason}).",
                     "Check your internet connection or proxy/firewall settings.")
    except socket.timeout:
        return _warn("Outbound HTTPS probe timed out; scraping may be slow.",
                     "Try again on a faster connection.")
    except Exception as e:
        return _warn(f"Network probe error: {e}")


def run_all(model_list: list[str], force: bool = False) -> dict:
    """Run checks in parallel. Returns {'checks': {...}, 'summary':
    {'ok'|'warn'|'fail': count}, 'ready_for_first_run': bool}.

    ready_for_first_run is true when nothing is failing - a user with
    warnings can still click 'Start' and the pipeline will degrade
    gracefully (LLM fallback, partial models, etc.).

    Results are cached per (model_list, TTL) so the UI can poll the
    /api/preflight endpoint without re-probing Ollama every few
    seconds. Pass force=True to skip the cache (used by the wizard's
    'Re-check' button)."""
    now = time.time()
    key = tuple(model_list or [])
    if not force:
        with _cache_lock:
            if (_cache["result"] is not None
                    and _cache["models_key"] == key
                    and (now - _cache["ts"]) < _CACHE_TTL_S):
                return _cache["result"]

    # Local imports keep the preflight module light: hardware probe
    # shells out to nvidia-smi / rocm-smi / system_profiler, so we only
    # pull it in when this function actually runs.
    from core import hardware as _hardware

    checks = {}
    jobs = {
        "python":         check_python,
        "ollama_running": check_ollama_running,
        "ollama_models":  lambda: check_ollama_models(model_list),
        "embeddings":     check_embeddings,
        "network":        check_network,
        "hardware":       _hardware.preflight_entry,
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        fut_map = {ex.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(fut_map):
            name = fut_map[fut]
            try:
                checks[name] = fut.result()
            except Exception as e:
                checks[name] = _fail(f"check threw: {e}")
    summary = {"ok": 0, "warn": 0, "fail": 0, "skipped": 0}
    for c in checks.values():
        summary[c["state"]] = summary.get(c["state"], 0) + 1
    # Hardware snapshot, separate from the checks dict so the UI can
    # consume it as structured data (vendor/name/vram_gb/band) without
    # re-parsing a human-readable detail string.
    try:
        hardware_snapshot = _hardware.detect()
    except Exception as e:
        logger.debug("Hardware detect raised: %s", e)
        hardware_snapshot = {"detected": False, "reason": str(e), "band": "cpu"}

    result = {
        "checks": checks,
        "summary": summary,
        "ready_for_first_run": summary["fail"] == 0,
        "checked_at": now,
        "hardware": hardware_snapshot,
    }
    with _cache_lock:
        _cache["ts"] = now
        _cache["models_key"] = key
        _cache["result"] = result
    return result


def get_last_result() -> dict | None:
    """Return the last run_all() result if one exists, else None.
    Used by /api/preflight to return the cached snapshot without
    re-running; the background prewarm/preflight thread primes this on
    startup."""
    with _cache_lock:
        return _cache["result"]
