"""
Decision log + user reactions (thumbs up/down on matches).

The pipeline already writes "pass" decisions with reasons into
data/decision_log.json via the tracker. This module adds a tagged reaction
layer: a separate key per (title, company) holding {action: 'up'|'down',
ts, notes}. We keep them in the same file so the dashboard has one source
of truth for "everything I've weighed in on".

Schema on disk:
  {
    "reactions": {
      "<title>||<company>": {
        "action": "up" | "down",
        "title": ...,
        "company": ...,
        "url": ...,
        "score": 0.62,
        "ts": "2026-04-20T...",
        "source": "dashboard"
      },
      ...
    },
    "decisions": [ ...existing pass-reasons from the tracker... ]
  }

The tracker's existing list-of-decisions format is preserved untouched.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger("lro.decision_store")

_lock = threading.Lock()
_FILE_NAME = "decision_log.json"


def _key(title: str, company: str) -> str:
    return f"{(title or '').strip().lower()}||{(company or '').strip().lower()}"


def _path(data_dir: Path) -> Path:
    return data_dir / _FILE_NAME


def _load(data_dir: Path) -> dict:
    p = _path(data_dir)
    if not p.exists():
        return {"reactions": {}, "decisions": []}
    try:
        raw = json.loads(p.read_text())
    except Exception:
        logger.warning("decision_log.json unreadable; starting fresh.")
        return {"reactions": {}, "decisions": []}

    # Tolerate the older tracker-only list format.
    if isinstance(raw, list):
        return {"reactions": {}, "decisions": raw}
    if "reactions" not in raw:
        raw["reactions"] = {}
    if "decisions" not in raw:
        raw["decisions"] = []
    return raw


def _save(data_dir: Path, data: dict) -> None:
    from core.io_safe import write_text_atomic
    write_text_atomic(_path(data_dir), json.dumps(data, indent=2))


def record_reaction(data_dir: Path, title: str, company: str, action: str,
                    url: str = "", score: float = 0.0, notes: str = "") -> dict:
    """Record or update a thumbs-up/down reaction. action in {'up','down','clear'}."""
    if action not in ("up", "down", "clear"):
        raise ValueError(f"action must be 'up', 'down' or 'clear' (got {action!r})")
    if not title or not company:
        raise ValueError("title and company are required")

    k = _key(title, company)
    with _lock:
        data = _load(data_dir)
        reactions = data["reactions"]
        if action == "clear":
            reactions.pop(k, None)
        else:
            reactions[k] = {
                "action": action,
                "title": title,
                "company": company,
                "url": url,
                "score": float(score or 0.0),
                "notes": notes or "",
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                "source": "dashboard",
            }
        _save(data_dir, data)
        return {"key": k, "action": action, "count": len(reactions)}


def list_all(data_dir: Path) -> dict:
    """Return the whole decision log (reactions + pass-decisions)."""
    with _lock:
        return _load(data_dir)


def list_reactions(data_dir: Path, filter_action: str | None = None) -> list[dict]:
    """Return reactions as a list, newest first. Optional action filter."""
    data = list_all(data_dir)
    items = list(data["reactions"].values())
    if filter_action:
        items = [i for i in items if i.get("action") == filter_action]
    items.sort(key=lambda i: i.get("ts", ""), reverse=True)
    return items
