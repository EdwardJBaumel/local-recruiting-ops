"""
Multi-model Ollama client.
Routes tasks to right-sized models for quality first, speed second.

Per-stage picks are documented with reasoning in
docs/MODEL_RESEARCH_2026-04.md. Short version:

  parse    - qwen2.5:14b      structured JSON extraction
  match    - qwen3:14b        LLM fallback when embeddings unavailable
  analyze  - deepseek-r1:14b  fit-gap with explicit chain-of-thought
  digest   - gemma3:12b       narrative prose summary
  chat     - qwen3:14b        conversational Q&A (shares with match)
  cover    - qwen3:14b        cover-letter drafting

All fit a 16 GB GPU because Ollama loads them one at a time. On smaller
cards the 14B models spill to CPU - slower but functional, which matches
the user's stated preference of quality-over-speed.
"""

import requests
import json
import logging
import time

logger = logging.getLogger("sentinel.llm")

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

# Task-to-model mapping (overridable via config). Every tag below is a
# real model present in the public Ollama library as of April 2026.
DEFAULT_MODELS = {
    "parse":   "qwen3:14b",         # HTML / resume -> JSON extraction
    "match":   "qwen3:14b",         # embedding-fallback LLM scoring
    "analyze": "phi4-reasoning:14b", # fit-gap chain-of-thought rationale
    "digest":  "gemma3:12b",        # cycle summary prose
    "chat":    "qwen3:8b",          # user Q&A on matches (snappy)
    "cover":   "gemma3:12b",        # cover-letter drafting (prose)
    "default": "qwen3:8b",          # generic fallback (always-resident)
}

# Fallback chain when the configured model returns 404 ("model not
# found"). Ordered by capability descending — biggest-still-fits first
# so a cycle that asked for qwen3:14b only steps down if 14b is gone,
# not all the way to 3B. Qwen2.5 is intentionally not in the chain:
# Qwen3 supersedes it on every relevant axis (STEM, JSON adherence,
# tool-calling) per the Qwen team's own report — keeping it would just
# burn disk for a strictly worse model.
FALLBACK_CHAIN = ["qwen3:14b", "qwen3:8b", "qwen3:4b", "gemma3:12b", "gemma3:4b", "llama3.2:3b"]

# Models we've proven 404 at runtime. Populated lazily on the first
# failure and consulted before every subsequent call so we don't keep
# hammering Ollama with the same dead tag. Never cleared — if you pull
# the model, restart the process.
_missing_models: set[str] = set()

# Remembered substitute for a missing model ("qwen3:14b" -> "qwen3:8b").
# Two reasons to cache: (1) speed — skip the fallback probe — and
# (2) stability — every stage that asked for qwen3:14b gets the same
# substitute instead of drifting to different models.
_substitute_models: dict[str, str] = {}

# Cache of models that /api/tags says are loaded. Refreshed once per
# process the first time we need it.
_available_models_cache: list[str] | None = None

# Track model usage for dashboard metrics
_usage_stats = {"calls": {}, "tokens_approx": {}, "latency_ms": {}}


def _list_available_models() -> list[str]:
    """Ask Ollama which models are pulled. Cached across calls."""
    global _available_models_cache
    if _available_models_cache is not None:
        return _available_models_cache
    try:
        resp = requests.get(OLLAMA_TAGS_URL, timeout=5)
        resp.raise_for_status()
        _available_models_cache = [m["name"] for m in resp.json().get("models", [])]
    except Exception as e:
        logger.warning("Could not list Ollama models: %s", e)
        _available_models_cache = []
    return _available_models_cache


def _resolve_model(model: str) -> str:
    """Return `model` if Ollama has it, else the first fallback that's
    actually pulled. Memoised per-missing-tag so we substitute once and
    stick with the substitute for the rest of the process."""
    if model in _substitute_models:
        return _substitute_models[model]
    if model not in _missing_models:
        return model
    # We already know it's missing — pick a substitute.
    available = _list_available_models()
    for candidate in FALLBACK_CHAIN:
        if candidate == model:
            continue
        if any(candidate in a for a in available):
            _substitute_models[model] = candidate
            logger.warning(
                "Model '%s' unavailable; falling back to '%s' for the rest of this process.",
                model, candidate,
            )
            return candidate
    # Nothing in the chain is pulled — return the original so the caller
    # gets a clean error instead of silent weirdness.
    logger.error(
        "Model '%s' is missing and no fallback in %s is pulled either. Giving up.",
        model, FALLBACK_CHAIN,
    )
    return model


def get_effective_models() -> dict:
    """Return the current missing-model / substitute map for the
    dashboard banner. Keys: missing (list), substitutes (dict)."""
    return {
        "missing": sorted(_missing_models),
        "substitutes": dict(_substitute_models),
    }


def get_model(task: str, config_models: dict = None) -> str:
    """Get the right model for a task, with config override."""
    models = {**DEFAULT_MODELS, **(config_models or {})}
    return models.get(task, models["default"])


def get_usage_stats() -> dict:
    """Return model usage statistics for dashboard display."""
    return _usage_stats


def _track(task: str, model: str, latency_ms: float, approx_tokens: int = 0):
    """Track usage for efficiency reporting."""
    key = f"{task}:{model}"
    if key not in _usage_stats["calls"]:
        _usage_stats["calls"][key] = 0
        _usage_stats["tokens_approx"][key] = 0
        _usage_stats["latency_ms"][key] = []
    _usage_stats["calls"][key] += 1
    _usage_stats["tokens_approx"][key] += approx_tokens
    _usage_stats["latency_ms"][key].append(latency_ms)
    # Keep last 100 latencies
    _usage_stats["latency_ms"][key] = _usage_stats["latency_ms"][key][-100:]


def query(prompt: str, task: str = "default", model: str = None,
          config_models: dict = None, temperature: float = 0.3,
          timeout: int = 120) -> str:
    """Send a prompt to Ollama. Routes to the right model based on task.

    Transparent fallback: when the configured model returns 404 ("model
    not found") we mark it missing, pick the first pulled model from
    FALLBACK_CHAIN, and retry once. The substitution sticks for the
    rest of the process so every caller gets consistent routing.
    """
    if model is None:
        model = get_model(task, config_models)

    # Apply known substitution up front so we skip the dead model entirely.
    effective = _resolve_model(model)

    attempted = []
    for attempt in range(2):
        attempted.append(effective)
        start = time.time()
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model": effective,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": temperature},
                },
                timeout=timeout,
            )
            if resp.status_code == 404:
                # Model not pulled. Mark it, pick a fallback, retry once.
                if effective not in _missing_models:
                    logger.warning(
                        "Ollama 404 for model '%s' (task=%s). Switching to fallback.",
                        effective, task,
                    )
                _missing_models.add(effective)
                new_effective = _resolve_model(effective)
                if new_effective == effective or new_effective in attempted:
                    # No usable fallback — fail loud rather than loop.
                    resp.raise_for_status()
                effective = new_effective
                continue
            resp.raise_for_status()
            result = resp.json().get("response", "").strip()

            latency = (time.time() - start) * 1000
            _track(task, effective, latency, len(result) // 4)
            logger.debug("[%s] %s responded in %.0fms", task, effective, latency)

            return result

        except requests.ConnectionError:
            logger.error("Cannot connect to Ollama. Is it running? (ollama serve)")
            raise
        except requests.Timeout:
            logger.error("Ollama timed out (%ds) on model %s for task %s", timeout, effective, task)
            raise
        except requests.HTTPError as e:
            # If the caller wanted to see the 404, it'll land here on
            # the second iteration after fallback was exhausted.
            logger.error("Ollama error [%s/%s]: %s", task, effective, e)
            raise
        except Exception as e:
            logger.error("Ollama error [%s/%s]: %s", task, effective, e)
            raise

    # Shouldn't reach — the loop either returns or raises.
    raise RuntimeError(f"Ollama query exhausted after attempts: {attempted}")


def query_json(prompt: str, task: str = "default", model: str = None,
               config_models: dict = None) -> dict:
    """Send a prompt and parse JSON response."""
    raw = query(prompt, task=task, model=model, config_models=config_models,
                temperature=0.1)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                pass
        logger.warning("JSON parse failed [%s]: %s", task, raw[:200])
        return {"_raw": raw, "_parse_error": True}


def check_models(required_tasks: list = None) -> dict:
    """Check which models are available in Ollama. Returns status dict."""
    tasks = required_tasks or list(DEFAULT_MODELS.keys())
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        resp.raise_for_status()
        available = [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return {"available": [], "missing": list(DEFAULT_MODELS.values()), "ok": False}

    needed = set(DEFAULT_MODELS[t] for t in tasks if t in DEFAULT_MODELS)
    missing = [m for m in needed if not any(m in a for a in available)]

    return {
        "available": available,
        "needed": list(needed),
        "missing": missing,
        "ok": len(missing) == 0,
        "model_map": {t: DEFAULT_MODELS.get(t, "?") for t in tasks},
    }
