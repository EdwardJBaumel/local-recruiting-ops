"""
PRE-WARM

Runs expensive cold-start work ahead of the first cycle so the user's
first Run Pipeline click feels instant. Two steps:

  a. Load sentence-transformers model weights into memory. First call
     to SentenceTransformer(...) loads ~90 MB from disk (1-3s SSD,
     5-10s older disk). Subsequent encodes are near-instant.
  b. Ping Ollama with a tiny prompt on each required model so Ollama
     loads the GB-sized weights into VRAM/RAM before the first real
     parse request. This can take 2-30s depending on model size and
     whether Ollama unloaded them for idle.

Both steps are best-effort. On lower-end machines without a GPU, or
when Ollama isn't running, or when sentence-transformers is missing
(packaged EXE path), we log a debug line and return. The first cycle
still works; it just pays the same cold-start it would've paid anyway.

Status is exposed via a single module-level dict so the API can show
progress in the wizard ('Warming models... Ollama ready').
"""

import logging
import threading
import time

logger = logging.getLogger("sentinel.prewarm")

_status = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "embeddings": {"state": "pending", "detail": ""},
    "ollama":     {"state": "pending", "detail": ""},
}
_lock = threading.Lock()


def get_status() -> dict:
    """Snapshot for GET /api/prewarm. Returns a shallow copy so the
    handler can serialise without holding the lock."""
    with _lock:
        return {
            "running": _status["running"],
            "started_at": _status["started_at"],
            "finished_at": _status["finished_at"],
            "embeddings": dict(_status["embeddings"]),
            "ollama": dict(_status["ollama"]),
        }


def _set(component: str, state: str, detail: str = "") -> None:
    with _lock:
        _status[component] = {"state": state, "detail": detail}


def _warm_embeddings() -> None:
    """Import + construct the sentence-transformers model so its weights
    are in memory. Safe to call multiple times - the model is cached by
    sentence_transformers once loaded."""
    _set("embeddings", "running")
    try:
        # Deferred import: sentence_transformers is optional in the
        # packaged-EXE path, and importing it here keeps server startup
        # fast when users haven't installed it.
        from sentence_transformers import SentenceTransformer  # type: ignore
        # MiniLM is the model core/embeddings defaults to. If a different
        # one is configured later, this is still harmless - it pre-loads
        # the default, the match agent will use whatever it's configured
        # to use on its first call.
        model = SentenceTransformer("BAAI/bge-m3")
        # Touch encode() once with a dummy sentence so the torch graph
        # and tokenizer are also warmed.
        model.encode(["warm"], show_progress_bar=False)
        _set("embeddings", "ready", "BAAI/bge-m3 loaded")
        logger.info("Pre-warm: sentence-transformers ready.")
    except ImportError:
        _set("embeddings", "skipped", "sentence-transformers not installed")
        logger.info("Pre-warm: sentence-transformers not installed - skipping.")
    except Exception as e:
        _set("embeddings", "failed", f"{type(e).__name__}: {e}")
        logger.warning("Pre-warm: embeddings failed: %s", e)


def _warm_ollama(models: list[str], timeout_per_model: int = 20) -> None:
    """Hit Ollama with a 1-token generation for each required model so
    the weights are loaded before the first real parse/match request.
    Cheap per model; capped with a per-model timeout so a wedged Ollama
    doesn't pin the wizard on a slow machine."""
    _set("ollama", "running")
    try:
        # Use requests via core.llm if available; otherwise bail quietly.
        import urllib.request
        import json as _json
        ok = []
        failed = []
        for m in models:
            try:
                payload = _json.dumps({
                    "model": m,
                    "prompt": "hi",
                    "stream": False,
                    "options": {"num_predict": 1},
                }).encode()
                req = urllib.request.Request(
                    "http://127.0.0.1:11434/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=timeout_per_model) as r:
                    r.read()
                ok.append(m)
                logger.info("Pre-warm: Ollama '%s' loaded.", m)
            except Exception as e:
                failed.append((m, str(e)))
                logger.info("Pre-warm: Ollama '%s' skipped (%s).", m, e)
        if ok and not failed:
            _set("ollama", "ready", f"loaded: {', '.join(ok)}")
        elif ok and failed:
            _set("ollama", "partial",
                 f"loaded: {', '.join(ok)}; failed: {', '.join(m for m, _ in failed)}")
        elif not ok and not models:
            _set("ollama", "skipped", "no models configured")
        else:
            _set("ollama", "failed",
                 f"all models failed: {', '.join(m for m, _ in failed)}")
    except Exception as e:
        _set("ollama", "failed", f"{type(e).__name__}: {e}")
        logger.warning("Pre-warm: Ollama step failed: %s", e)


def run_background(models: list[str] | None = None) -> None:
    """Fire-and-forget pre-warm. Safe to call many times - if one is
    already running, the second call is a no-op."""
    with _lock:
        if _status["running"]:
            logger.debug("Pre-warm already in progress; skipping duplicate call.")
            return
        _status["running"] = True
        _status["started_at"] = time.time()
        _status["finished_at"] = None
        _status["embeddings"] = {"state": "pending", "detail": ""}
        _status["ollama"] = {"state": "pending", "detail": ""}

    def _go():
        try:
            _warm_embeddings()
            _warm_ollama(models or [])
        finally:
            with _lock:
                _status["running"] = False
                _status["finished_at"] = time.time()

    t = threading.Thread(target=_go, name="sentinel-prewarm", daemon=True)
    t.start()
