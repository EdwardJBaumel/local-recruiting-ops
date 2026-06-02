"""
HARDWARE DETECTION

Detects the user's GPU vendor, name, and usable VRAM so the wizard can
pick sane model-tier defaults instead of assuming a specific card. Runs
three probes in priority order and stops at the first that answers:

  a. nvidia-smi           (NVIDIA cards on Windows, Linux)
  b. rocm-smi             (AMD cards on Linux, partial Windows)
  c. system_profiler      (Apple Silicon / macOS unified memory)

If nothing answers (CPU-only machine, drivers missing, sandbox), the
result is {"detected": False, ...} and the wizard falls back to a
user-driven VRAM picker.

Design notes:
- Each subprocess probe has a 1.0s timeout, except the Apple
  system_profiler call which gets 2.0s because it's genuinely slow on
  a cold metadata cache. Probing should never block the wizard UI for
  more than ~4s in the absolute worst case (all probes time out
  sequentially) - in practice the first probe returns or fails-fast
  in <200ms.
- Results are cached for _CACHE_TTL_S so the wizard's preflight polling
  doesn't re-shell out every few seconds. Hardware doesn't change mid-
  session.
- We return the lowest common denominator: vendor, name, vram_gb. The
  wizard treats vram_gb as the single knob for tier defaults.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time

logger = logging.getLogger("lro.hardware")

_PROBE_TIMEOUT = 1.0
_CACHE_TTL_S = 30.0

_cache_lock = threading.Lock()
_cache = {"ts": 0.0, "result": None}


def _run(cmd: list[str], timeout: float = _PROBE_TIMEOUT) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except Exception as e:
        return 1, "", str(e)


def _probe_nvidia() -> dict | None:
    """nvidia-smi on Linux/Windows. Returns the highest-VRAM GPU found."""
    if shutil.which("nvidia-smi") is None:
        return None
    rc, out, _ = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ])
    if rc != 0 or not out.strip():
        return None
    best = None
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            vram_mb = int(parts[1])
        except ValueError:
            continue
        vram_gb = round(vram_mb / 1024, 1)
        if best is None or vram_gb > best["vram_gb"]:
            best = {"vendor": "NVIDIA", "name": name, "vram_gb": vram_gb}
    return best


def _probe_amd() -> dict | None:
    """rocm-smi for AMD GPUs. Returns the highest-VRAM GPU found.
    Output formats vary across rocm versions; we try JSON first, then
    fall back to parsing the plain-text table."""
    if shutil.which("rocm-smi") is None:
        return None

    # JSON path (modern rocm-smi)
    rc, out, _ = _run(["rocm-smi", "--showmeminfo", "vram", "--json"])
    if rc == 0 and out.strip():
        try:
            data = json.loads(out)
            best = None
            for card_key, card in (data or {}).items():
                if not isinstance(card, dict):
                    continue
                # Key name varies: "VRAM Total Memory (B)" / "vram total (B)"
                total_b = None
                for k, v in card.items():
                    if "vram" in k.lower() and "total" in k.lower():
                        try:
                            total_b = int(str(v).strip())
                        except (ValueError, TypeError):
                            continue
                        break
                if total_b is None:
                    continue
                vram_gb = round(total_b / (1024 ** 3), 1)
                name = card.get("Card series") or card.get("GPU") or card_key
                if best is None or vram_gb > best["vram_gb"]:
                    best = {"vendor": "AMD", "name": str(name), "vram_gb": vram_gb}
            if best is not None:
                return best
        except Exception:
            pass

    # Plain-text fallback
    rc, out, _ = _run(["rocm-smi", "--showmeminfo", "vram"])
    if rc == 0 and out.strip():
        m = re.search(r"VRAM Total Memory.*?:\s*(\d+)", out, re.IGNORECASE)
        if m:
            try:
                total_b = int(m.group(1))
                vram_gb = round(total_b / (1024 ** 3), 1)
                return {"vendor": "AMD", "name": "AMD GPU", "vram_gb": vram_gb}
            except ValueError:
                pass
    return None


def _probe_apple() -> dict | None:
    """Apple Silicon: unified memory acts as VRAM. system_profiler
    gives us the chip name, and we read total RAM for the VRAM figure
    because the GPU can use the whole pool (minus OS overhead)."""
    if sys.platform != "darwin":
        return None
    # system_profiler is genuinely slow on a cold call (OS metadata cache),
    # so we give it a 2s budget instead of the default 1s. The earlier
    # probes exit in milliseconds on macOS (nvidia/rocm both missing), so
    # worst-case total is bounded by this one value.
    rc, out, _ = _run(["system_profiler", "SPHardwareDataType", "-json"], timeout=2.0)
    if rc != 0 or not out.strip():
        return None
    try:
        data = json.loads(out)
        items = data.get("SPHardwareDataType") or []
        if not items:
            return None
        h = items[0]
        chip = h.get("chip_type") or h.get("cpu_type") or "Apple Silicon"
        # e.g. "64 GB"
        mem = h.get("physical_memory") or ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*GB", mem)
        if not m:
            return None
        total_gb = float(m.group(1))
        # Allow roughly 75% of unified RAM to be treated as VRAM for
        # model-tier decisions; the OS needs the rest. This is a
        # conservative figure; users with "High Power Mode" can bump it.
        usable_gb = round(total_gb * 0.75, 1)
        return {"vendor": "Apple", "name": chip, "vram_gb": usable_gb}
    except Exception as e:
        logger.debug("Apple probe parse failed: %s", e)
        return None


def _vram_band(vram_gb: float | None) -> str:
    """Group a VRAM figure into a band the wizard uses for defaults.
    Bands: 'cpu' (no detected GPU), 'low' (<=4), 'mid' (4-8),
    'high' (8-16), 'top' (16+). Tunable if new tiers show up."""
    if vram_gb is None:
        return "cpu"
    if vram_gb <= 4:
        return "low"
    if vram_gb <= 8:
        return "mid"
    if vram_gb < 16:
        return "high"
    return "top"


def detect(force: bool = False) -> dict:
    """Return the current hardware snapshot.

    Shape:
      {
        "detected": True,
        "vendor":   "NVIDIA" | "AMD" | "Apple",
        "name":     "GeForce RTX <model>",
        "vram_gb":  16.0,
        "band":     "top" | "high" | "mid" | "low" | "cpu",
        "source":   "nvidia-smi" | "rocm-smi" | "system_profiler",
      }
    On failure: {"detected": False, "reason": "...", "band": "cpu"}.
    """
    now = time.time()
    if not force:
        with _cache_lock:
            if _cache["result"] is not None and (now - _cache["ts"]) < _CACHE_TTL_S:
                return _cache["result"]

    result = None
    for probe, label in (
        (_probe_nvidia, "nvidia-smi"),
        (_probe_amd, "rocm-smi"),
        (_probe_apple, "system_profiler"),
    ):
        try:
            hit = probe()
        except Exception as e:
            logger.debug("%s probe raised: %s", label, e)
            hit = None
        if hit:
            hit["detected"] = True
            hit["source"] = label
            hit["band"] = _vram_band(hit.get("vram_gb"))
            result = hit
            break

    if result is None:
        result = {
            "detected": False,
            "reason": "No NVIDIA, AMD, or Apple GPU detected. You'll run on CPU.",
            "band": "cpu",
            "vram_gb": None,
            "vendor": None,
            "name": None,
            "source": None,
        }

    with _cache_lock:
        _cache["ts"] = now
        _cache["result"] = result
    return result


def preflight_entry(snapshot: dict | None = None) -> dict:
    """Adapter so preflight can splice hardware in alongside the other
    checks. Returns the {state, detail, fix} shape the UI expects."""
    snap = snapshot or detect()
    if snap.get("detected"):
        vram = snap.get("vram_gb")
        vram_str = f"{vram:g} GB" if isinstance(vram, (int, float)) else "unknown"
        return {
            "state": "ok",
            "detail": f"{snap.get('vendor')} {snap.get('name')} / {vram_str} VRAM",
            "fix": "",
        }
    return {
        "state": "warn",
        "detail": "No discrete GPU detected; models will run on CPU and cycles will be slow.",
        "fix": "If you have a GPU, install vendor drivers (NVIDIA / AMD / Apple Silicon) and rerun the wizard.",
    }
