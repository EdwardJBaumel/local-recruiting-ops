"""
RESOURCE SNAPSHOT

Aggregates "what is SENTINEL costing my machine right now" into a single
JSON payload for the Brief-tab panel. Keeps all IO in pure helpers so
the aggregator can be unit-tested without touching disk or a GPU.

Inputs (all optional - missing pieces degrade gracefully):
  a. data/cycle_times.json       per-cycle wall-clock + counts
  b. data/match_stats.json       matcher latency + mode
  c. nvidia-smi (if installed)   GPU VRAM snapshot
  d. psutil (if installed)       RAM / CPU snapshot

Output shape (see tests for the full contract):
  {
    "cycles": {"count": N, "median_seconds": M, "last": {...}},
    "match":  {"mode": "embeddings"|"llm", "median_latency_ms": ...},
    "gpu":    {"name": str, "total_mib": int, "used_mib": int} | None,
    "memory": {"rss_mib": int, "total_mib": int} | None,
    "as_of":  "<iso8601>"
  }

Why an aggregator (and not wire each piece into the dashboard ad hoc):
the Brief tab wants one atomic "is everything healthy" read. A single
call also means one place to gate behind try/except when optional deps
are missing.
"""
from __future__ import annotations

import json
import logging
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("sentinel.resource_snapshot")

CYCLE_TIMES_FILE = "cycle_times.json"
MATCH_STATS_FILE = "match_stats.json"


def _safe_load_json(path: Path) -> Any:
    """Read + parse JSON, swallowing every failure path into None so the
    aggregator stays non-fatal. We log the specific reason so a wedged
    file is still visible at DEBUG without nuking the whole endpoint."""
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("resource_snapshot: %s unreadable (%s)", path.name, e)
        return None


def summarise_cycles(cycle_times: list | None, *, keep: int = 20) -> dict:
    """Reduce cycle_times.json to the fields the Brief panel wants.
    `keep` caps how many recent cycles contribute to median/last so an
    unbounded log doesn't drag tail latency."""
    if not isinstance(cycle_times, list) or not cycle_times:
        return {"count": 0, "median_seconds": None, "last": None}

    # Trim to the most recent N so the aggregate reflects "what is
    # happening right now", not lifetime averages.
    tail = cycle_times[-keep:]
    seconds = [
        float(c.get("seconds"))
        for c in tail
        if isinstance(c, dict) and isinstance(c.get("seconds"), (int, float))
    ]
    median = round(statistics.median(seconds), 2) if seconds else None
    last = tail[-1] if isinstance(tail[-1], dict) else None
    # Strip heavy / noisy fields so the UI payload stays compact.
    last_summary = None
    if last is not None:
        last_summary = {
            k: last.get(k)
            for k in ("cycle", "seconds", "ts", "matches", "new_jobs",
                      "ingested", "fit_gaps", "resumes")
            if k in last
        }
    return {
        "count": len(cycle_times),  # lifetime count, even when we median on tail
        "median_seconds": median,
        "last": last_summary,
    }


def summarise_match_stats(match_stats: dict | None) -> dict:
    """Extract the match-mode + latency line for the panel. Returns a
    dict of Nones rather than None so the UI doesn't need a null-check
    on the outer shape."""
    if not isinstance(match_stats, dict):
        return {"mode": None, "median_latency_ms": None, "threshold": None,
                "embeddings_active": None, "sample_count": None}
    return {
        "mode": match_stats.get("mode"),
        "median_latency_ms": match_stats.get("median_latency_ms"),
        "threshold": match_stats.get("threshold"),
        "embeddings_active": match_stats.get("embeddings_active"),
        "sample_count": match_stats.get("sample_count"),
    }


def probe_gpu_vram(runner=None, timeout: float = 1.5) -> dict | None:
    """Best-effort nvidia-smi probe. Returns None on any failure so the
    caller can render a "GPU: unknown" pill without special-casing.
    `runner` is injected for tests; defaults to subprocess.run.
    """
    if runner is None:
        runner = subprocess.run
    cmd = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.used",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception as e:
        logger.debug("nvidia-smi probe raised: %s", e)
        return None

    stdout = getattr(proc, "stdout", "") or ""
    if getattr(proc, "returncode", 1) != 0 or not stdout.strip():
        return None

    # First card only - SENTINEL only runs one Ollama instance.
    first = stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 3:
        return None
    name = parts[0]
    try:
        total = int(parts[1])
        used = int(parts[2])
    except ValueError:
        return None
    return {
        "name": name,
        "total_mib": total,
        "used_mib": used,
        "used_pct": round(100.0 * used / total, 1) if total else None,
    }


def probe_ollama_loaded(timeout: float = 1.0) -> list | None:
    """Hit Ollama's /api/ps to learn what's currently loaded and how
    much VRAM each model occupies. Returns None on any failure so the
    caller can render an "unknown" state.

    Ollama reports `size` (total weight bytes) and `size_vram` (bytes
    actually resident on the GPU). When they differ, the model is
    spilling to CPU — slow inference. Surfacing this lets the user
    notice when their tier choice exceeds available VRAM (e.g. a
    16 GB card running gemma3:27b at Q4 will silently spill ~1 GB to
    RAM and inference drops to 1/3 the speed).
    """
    try:
        import requests  # type: ignore
    except Exception:
        return None
    try:
        resp = requests.get("http://127.0.0.1:11434/api/ps", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug("ollama /api/ps probe failed: %s", e)
        return None
    out = []
    for m in (data.get("models") or []):
        size = m.get("size") or 0
        size_vram = m.get("size_vram") or 0
        out.append({
            "name": m.get("name"),
            "size_mib": round(size / (1024 * 1024), 1),
            "vram_mib": round(size_vram / (1024 * 1024), 1),
            "cpu_mib": round(max(0, size - size_vram) / (1024 * 1024), 1),
            "spilling": size > 0 and size_vram < size,
        })
    return out


def probe_memory() -> dict | None:
    """psutil RAM snapshot if psutil is installed. Optional dep."""
    try:
        import psutil  # type: ignore
    except Exception:
        return None
    try:
        vm = psutil.virtual_memory()
        proc = psutil.Process()
        rss = proc.memory_info().rss
    except Exception as e:
        logger.debug("psutil probe failed: %s", e)
        return None
    return {
        "rss_mib": round(rss / (1024 * 1024), 1),
        "total_mib": round(vm.total / (1024 * 1024), 1),
        "system_used_pct": vm.percent,
    }


def collect(
    data_dir: Path | str,
    *,
    include_gpu: bool = True,
    include_memory: bool = True,
    gpu_runner=None,
) -> dict:
    """Build the full snapshot. `gpu_runner` is injected for tests."""
    ddir = Path(data_dir)
    cycles = _safe_load_json(ddir / CYCLE_TIMES_FILE)
    match_stats = _safe_load_json(ddir / MATCH_STATS_FILE)
    snapshot: dict = {
        "cycles": summarise_cycles(cycles),
        "match": summarise_match_stats(match_stats),
        "gpu": probe_gpu_vram(runner=gpu_runner) if include_gpu else None,
        "memory": probe_memory() if include_memory else None,
        "ollama_loaded": probe_ollama_loaded(),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    return snapshot
