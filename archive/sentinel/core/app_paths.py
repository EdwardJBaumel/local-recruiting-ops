"""
Runtime path resolution for both dev mode and the PyInstaller onefile bundle.

PyInstaller extracts bundled files to a temp dir at `sys._MEIPASS`. Anything
the user should be able to edit on disk (config, data, logs) lives beside the
exe, NOT inside _MEIPASS, because _MEIPASS is wiped when the process exits.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def bundle_dir() -> Path:
    """Read-only resources shipped inside the exe (static frontend, etc.).
    In dev, this is the repo root (one level up from this file's package).
    """
    if is_frozen():
        # onefile: _MEIPASS; onedir: sys._MEIPASS is set too, just points at
        # the exe folder instead of a temp dir.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # core/app_paths.py -> core -> sentinel/
    return Path(__file__).resolve().parent.parent


def runtime_dir() -> Path:
    """Writable state (config.json, data/, logs/). Beside the exe when frozen,
    cwd otherwise (matches how `python main.py` already behaves)."""
    if is_frozen():
        override = os.environ.get("SENTINEL_HOME")
        if override:
            p = Path(override).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def static_dir() -> Path:
    """Location of the built React UI (sentinel-ui/dist)."""
    # When frozen, the build script adds 'ui' under bundle_dir().
    b = bundle_dir()
    candidates = [b / "ui", b / "sentinel-ui" / "dist", b.parent / "sentinel-ui" / "dist"]
    for c in candidates:
        if c.is_dir():
            return c
    # Return the first candidate even if missing so callers can log it sensibly.
    return candidates[0]
