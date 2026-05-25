"""
USER STORE

Persistent local store for one-time setup data. Lives at data/user.json
and holds:
  - setup completion flag (with timestamp + schema version)
  - user identity (name, current role, target level)
  - launch metadata (first / last launch ISO timestamps)

The orchestrator's cycle loop gates on is_setup_complete(), so the
pipeline idles until the wizard has been submitted. Survives restarts
so the wizard only appears once on first launch (or when the user
explicitly opts back into it from Settings).

Schema is intentionally tiny. Preferences, resume, role keywords, etc.
live in their existing stores (config.json, data/resume/, etc.) - this
module only owns identity + setup state to avoid duplication.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

USER_FILE_NAME = "user.json"
SCHEMA_VERSION = 1

# In-process write lock so concurrent POST /api/setup + GET /api/setup-state
# can't race on the file. load() is lock-free by design (JSON is atomic
# enough for a 1-2 KB file; we re-read on every call anyway).
_lock = threading.Lock()


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / USER_FILE_NAME


def _default() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "setup_completed_at": None,
        "setup_completed": False,
        "name": "",
        "current_role": "",
        "target_level": "",  # e.g. "senior", "staff", "principal", "manager"
        "first_launch_at": now,
        "last_launch_at": now,
    }


def load(data_dir: Path) -> dict:
    """Read the user store from disk. Creates a default on first call so
    the file always exists - simpler for callers than None-handling.

    Backward compat: on first creation, if the data dir already has a
    resume or a config.json with preferences set, the user has been
    running SENTINEL before the wizard gate existed - mark setup
    complete so we don't lock them out of their own pipeline."""
    p = _path(data_dir)
    if not p.exists():
        data = _default()
        try:
            pre_existing = _detect_pre_wizard_state(Path(data_dir))
        except Exception:
            pre_existing = False
        if pre_existing:
            data["setup_completed"] = True
            data["setup_completed_at"] = data["first_launch_at"]
            data["name"] = data.get("name") or ""
        _write(data_dir, data)
        return data
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # Corrupt file: back up and start fresh so the user isn't
        # permanently locked out by a bad write.
        try:
            p.rename(p.with_suffix(".json.corrupt"))
        except Exception:
            pass
        data = _default()
        _write(data_dir, data)
        return data
    # Fill in any fields added since the file was written.
    merged = _default()
    merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def _detect_pre_wizard_state(data_dir: Path) -> bool:
    """Heuristic: returns True if this data dir was in active use before
    user.json existed. Checked on first load() so legacy users aren't
    gated out. Signals (any one):
      - data/resume/resume.txt or similar present
      - config.json at project root mentions profile_text non-empty
      - data/matches/ has at least one cycle file
    """
    d = Path(data_dir)
    if (d / "resume").is_dir() and any((d / "resume").iterdir()):
        return True
    if (d / "matches").is_dir() and any((d / "matches").glob("*.json")):
        return True
    try:
        root_cfg = (d.parent / "config.json")
        if root_cfg.exists():
            cfg = json.loads(root_cfg.read_text(encoding="utf-8"))
            if (cfg.get("match", {}) or {}).get("profile_text"):
                return True
    except Exception:
        pass
    return False


def _write(data_dir: Path, data: dict) -> None:
    p = _path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)  # atomic on POSIX; best-effort on Windows


def update(data_dir: Path, patch: dict) -> dict:
    """Merge patch into the store and persist. Returns the new state.
    Only keys already in the default schema are accepted; unknown keys
    are dropped to prevent the UI from scribbling arbitrary fields in."""
    allowed = set(_default().keys())
    with _lock:
        current = load(data_dir)
        for k, v in patch.items():
            if k in allowed:
                current[k] = v
        current["last_launch_at"] = datetime.now(timezone.utc).isoformat()
        _write(data_dir, current)
        return current


def mark_setup_complete(data_dir: Path) -> dict:
    """Flip the setup flag + stamp the completion time. Callers should
    call this AFTER all the individual stores (resume, config) have
    been persisted so we only mark complete on a successful setup."""
    with _lock:
        current = load(data_dir)
        current["setup_completed"] = True
        current["setup_completed_at"] = datetime.now(timezone.utc).isoformat()
        _write(data_dir, current)
        return current


def is_setup_complete(data_dir: Path) -> bool:
    """Cheap check used by the orchestrator cycle loop every N seconds.
    Re-reads the file so a completion from a different thread (the API
    server) is visible immediately."""
    try:
        return bool(load(data_dir).get("setup_completed"))
    except Exception:
        return False


def touch_launch(data_dir: Path) -> None:
    """Update last_launch_at on every process start. Used for freshness
    chips in the dashboard ('first used 2026-04-20, last 3 days ago')."""
    with _lock:
        current = load(data_dir)
        current["last_launch_at"] = datetime.now(timezone.utc).isoformat()
        _write(data_dir, current)
