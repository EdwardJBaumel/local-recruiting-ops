"""
CRASH-SAFE FILE WRITES

Single source of truth for writing state / config JSON files.

Why: SENTINEL writes JSON state (config.json, cycle_times.json,
dashboard.json, tracker.json, decision_log.json, etc) from multiple
threads and from the orchestrator's main loop. A plain `Path.write_text`
truncates the destination to zero, writes bytes, and closes. A crash or
concurrent rename mid-write leaves the file truncated - which is
exactly what we saw in the wild on 2026-04-21 (config.json and
cycle_times.json both corrupted mid-dump, wedging the pipeline).

Strategy: write to a *per-writer unique* `<path>.<pid>.<rand>.tmp`,
fsync, then `os.replace`. On POSIX and Windows `os.replace` is atomic
when source and destination live on the same filesystem (they always
do here). fsync guards against a crash between the write and the
rename losing data that was still in the page cache. On filesystems
that don't support fsync we fall through - the rename-based atomicity
is still a huge improvement over a bare write.

Why per-writer tmp names: earlier versions used `<path>.tmp` for every
writer. Two concurrent writers hitting the same destination would
collide on the tmp path — writer A renames its tmp to the target, and
writer B's subsequent `os.replace(<path>.tmp, <path>)` then fails with
`FileNotFoundError` because writer A's rename took writer B's tmp
file with it. Giving each writer its own tmp name eliminates the race.
"""
from __future__ import annotations

import os
import time
import threading
import uuid
from pathlib import Path

# Windows-specific: os.replace raises PermissionError (WinError 5) when the
# destination path is held for even a few ms by Defender realtime scan, the
# Search indexer, OneDrive, or a concurrent reader. Retry with backoff.
# See: https://bugs.python.org/issue46003 and WinError ERROR_ACCESS_DENIED.
_REPLACE_RETRIES = 8
_REPLACE_BACKOFF_MS = (5, 10, 20, 40, 80, 160, 320, 640)  # ~1.3s worst case

# Per-destination lock so two threads writing to the same path serialize
# their rename step. Different paths stay concurrent. Bounded by the
# number of distinct JSON files the app writes (~20), so no cleanup
# needed.
_path_locks: dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()


def _as_path(p) -> Path:
    """Coerce str / os.PathLike to Path. Existing callers across the
    codebase pass both shapes (``write_text_atomic(tracker_file, ...)``
    where tracker_file is a Path, but also ``write_text_atomic(str(p),
    ...)`` in a few places). Normalising here keeps the public surface
    forgiving."""
    return p if isinstance(p, Path) else Path(p)


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _path_locks_guard:
        lock = _path_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _path_locks[key] = lock
    return lock


def _unique_tmp_for(path: Path) -> Path:
    """Build a collision-proof tmp sibling. pid + uuid suffix keeps
    concurrent writers from stomping each other."""
    tag = f"{os.getpid()}.{uuid.uuid4().hex[:8]}"
    return path.with_suffix(path.suffix + f".{tag}.tmp")


def _replace_with_retry(src: str | os.PathLike, dst: str | os.PathLike) -> None:
    """Atomic rename with retry on transient Windows errors.

    Retries on PermissionError (Defender/indexer holds) and
    FileNotFoundError (rare — usually means a sibling writer beat us
    to the punch; safe to bail since the destination is already being
    updated by someone else).
    """
    last_err: Exception | None = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last_err = e
            if attempt == _REPLACE_RETRIES - 1:
                break
            time.sleep(_REPLACE_BACKOFF_MS[attempt] / 1000.0)
        except FileNotFoundError as e:
            # Source tmp vanished mid-rename. This happens when another
            # thread/process picked up a previous tmp and beat us, or
            # when antivirus quarantined our temp file. We log once and
            # give up — the destination is being handled elsewhere.
            last_err = e
            break
    assert last_err is not None
    raise last_err


def write_text_atomic(path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` via a unique temp file + os.replace. See
    module docstring for the why."""
    path = _as_path(path)
    tmp = _unique_tmp_for(path)
    with _lock_for(path):
        try:
            with open(tmp, "w", encoding=encoding) as f:
                f.write(text)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            _replace_with_retry(tmp, path)
        finally:
            # If replace failed mid-flight, drop the orphan tmp so we
            # don't leave `.12345.abcd1234.tmp` crumbs behind.
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


def write_bytes_atomic(path, data: bytes) -> None:
    path = _as_path(path)
    tmp = _unique_tmp_for(path)
    with _lock_for(path):
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            _replace_with_retry(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


def write_json_atomic(path, obj, indent: int | None = 2) -> None:
    """Convenience wrapper: json.dumps + write_text_atomic."""
    import json
    write_text_atomic(_as_path(path), json.dumps(obj, indent=indent))
