"""
LOG TAIL HELPER

Reads the tail of logs/sentinel.log and parses each line against the
format basicConfig() installs in main.py:

    %(asctime)s [%(name)s] %(levelname)s: %(message)s

Example line:
    2026-04-20 14:32:15,123 [sentinel.match] INFO: Matching 24 jobs...

The in-app "Logs" panel uses this so the user doesn't need a terminal
open to see what the pipeline is doing. We tail with a rolling byte
cap so a huge log file doesn't swamp the server - a few hundred KB is
plenty for "last 500 lines at WARNING+".

Filter semantics:
  - `min_level` keeps lines at or above a minimum severity. When a
    line's level can't be parsed (multi-line tracebacks appear as
    continuation lines) we inherit the last known level so a full
    traceback under an ERROR line stays attached to that ERROR.
  - `n` is the MAX number of lines returned *after* filtering.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("sentinel.log_tail")

_LOG_PATH = Path("logs/sentinel.log")
# Read at most this many bytes from the end of the file before parsing.
# 2 MB keeps even verbose DEBUG traces affordable while still giving
# "last 500 lines" at INFO+ the headroom it needs.
_TAIL_BYTES = 2 * 1024 * 1024

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

# Match: "2026-04-20 14:32:15,123 [name] LEVEL: msg"
_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?)\s+"
    r"\[(?P<logger>[^\]]+)\]\s+"
    r"(?P<level>[A-Z]+):\s?"
    r"(?P<msg>.*)$"
)


def _read_tail(path: Path, max_bytes: int) -> str:
    """Read the final `max_bytes` from `path` as utf-8 text.

    Opens the file in binary mode, seeks to (size - max_bytes), drops
    the first partial line so we don't leak a mid-line fragment into
    the parser. Returns '' for missing/unreadable files rather than
    raising - callers treat an empty string the same as "no logs yet".
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return ""
    except OSError as e:
        logger.warning("Cannot stat log file %s: %s", path, e)
        return ""

    try:
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes, os.SEEK_SET)
                # Skip the partial line at the seek boundary.
                fh.readline()
            data = fh.read()
    except OSError as e:
        logger.warning("Could not read log file %s: %s", path, e)
        return ""

    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def tail(n: int = 200, min_level: str = "INFO") -> dict:
    """Return the last `n` log lines at or above `min_level`.

    Output shape:
        {
          "available": bool,   # False when the log file doesn't exist yet
          "path": str,
          "min_level": str,
          "lines": [
            {ts, level, logger, message, raw},
            ...
          ]
        }
    """
    threshold = _LEVEL_ORDER.get((min_level or "INFO").upper(), 20)
    path = _LOG_PATH
    if not path.exists():
        return {"available": False, "path": str(path), "min_level": min_level, "lines": []}

    raw = _read_tail(path, _TAIL_BYTES)
    if not raw:
        return {"available": True, "path": str(path), "min_level": min_level, "lines": []}

    parsed: list[dict] = []
    last_level = "INFO"  # Continuation lines inherit the previous level.
    for line in raw.splitlines():
        m = _LINE_RE.match(line)
        if m:
            level = m.group("level").upper()
            last_level = level
            if _LEVEL_ORDER.get(level, 0) < threshold:
                continue
            parsed.append({
                "ts": m.group("ts"),
                "level": level,
                "logger": m.group("logger"),
                "message": m.group("msg"),
                "raw": line,
            })
        else:
            # Continuation line (traceback or wrapped message). Attach to
            # the previous parsed entry when possible so the UI shows
            # multi-line errors as one unit.
            if _LEVEL_ORDER.get(last_level, 0) < threshold:
                continue
            if parsed:
                parsed[-1]["message"] += "\n" + line
                parsed[-1]["raw"] += "\n" + line
            else:
                parsed.append({
                    "ts": "",
                    "level": last_level,
                    "logger": "",
                    "message": line,
                    "raw": line,
                })

    # Cap AFTER filtering so `n=200 at level=ERROR` returns 200 errors,
    # not "the 200 most recent lines of which maybe 3 are errors".
    capped = parsed[-max(1, min(n, 1000)):]
    return {
        "available": True,
        "path": str(path),
        "min_level": min_level,
        "lines": capped,
    }
