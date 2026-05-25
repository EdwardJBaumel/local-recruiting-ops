"""
Reset History
=============

What this file is
-----------------
The one-click "wipe my run history but don't lose my resume or
preferences" button. Called by the Danger Zone action in Settings
and by the POST /api/reset-history endpoint.

Why this lives in its own file
------------------------------
Two reasons:

1. **Readability for weak LLMs.** When a future model (or a future
   you, at 2am) needs to know "what exactly does the reset button
   do", the answer is ONE file with ONE function and an obvious
   allow/deny list. No spelunking through server.py's 1500 lines
   of route handlers.

2. **Safety.** Deleting user data is the kind of thing where a
   one-line refactor accidentally deletes `resume/` instead of
   `resumes/` and now the user's uploaded CV is gone. Centralising
   the allow-list + running every deletion through a single
   path-traversal guard makes that class of bug impossible.

What it clears
--------------
Anything in RESET_TARGETS below. Everything else (resume, user
prefs, story bank, tracker, decisions, cover letters) stays.

Safety guards
-------------
- Every target must live inside `data_dir`. If somehow a relative
  path escapes via symlink or `..`, we refuse to delete it.
- We NEVER touch `resume/`, `user.json`, `tracker.json`, the story
  bank file, or cover letters. Those are explicitly in KEEP_TARGETS
  as a paper-trail for future code review.
- On any exception we keep going and report ALL failures at the
  end -- one corrupt file shouldn't block the rest of the wipe.

Returns
-------
    {
      "ok": True,
      "cleared":  ["matches/", "digests/", "seen_jobs.json", ...],
      "skipped":  ["fit_gaps/ (did not exist)", ...],
      "errors":   [{"target": "...", "error": "..."}, ...]
    }
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger("lantern.reset_history")


# -----------------------------------------------------------------------------
# WHAT GETS WIPED
# -----------------------------------------------------------------------------
# Directories are wiped recursively. Files are just unlinked. Add new
# transient-state paths here as the pipeline grows -- this is the ONE
# place that knows what "reset" means.
# -----------------------------------------------------------------------------
RESET_DIRS: tuple[str, ...] = (
    "matches",          # per-cycle match snapshots
    "fit_gaps",         # per-cycle fit-gap analyses
    "parsed",           # per-cycle parsed job dumps
    "digests",          # saved weekly digests
)

RESET_FILES: tuple[str, ...] = (
    "seen_jobs.json",       # URL-dedup registry
    "seen_urls.json",       # same idea, older key scheme
    "match_registry.json",  # cross-cycle match history
    "match_stats.json",     # aggregate stats
    "cycle_times.json",     # cycle timing history
    "dashboard.json",       # current snapshot (regenerates next cycle)
    "ingest_sources.json",  # per-source stats
    "market_intel.json",    # market intel cache
    "dead_slugs.json",      # dead-job slug list
    "embedding_cache.pt",   # JD-embedding cache (rebuilds on next match)
)

# -----------------------------------------------------------------------------
# WHAT NEVER GETS WIPED -- documented here as a paper-trail. The reset
# function doesn't actually read this list; it only deletes the allow-list
# above. But keeping this here makes the invariant explicit so a future
# editor doesn't accidentally add "resume" to RESET_DIRS.
# -----------------------------------------------------------------------------
KEEP_TARGETS: tuple[str, ...] = (
    "resume/",                 # uploaded CV + parsed profile
    "resumes/",                # rendered tailored resumes
    "user.json",               # preferences / settings
    "tracker.json",            # application tracker (user-curated)
    "decision_log.json",       # like/pass reactions (user input)
    "feedback_embeddings.json",# learned feedback vectors (user input)
    "stories.md",              # STAR+R story bank
    "cover_letters/",          # generated cover letters
)


# -----------------------------------------------------------------------------
# PUBLIC ENTRY
# -----------------------------------------------------------------------------
def reset_history(data_dir: Path | str) -> dict[str, Any]:
    """Wipe per-cycle run history. Keep user-owned data.

    Parameters
    ----------
    data_dir :
        Sentinel's data directory (usually `./data`). Must exist;
        otherwise we return an error without creating anything.

    Returns
    -------
    dict with keys `ok`, `cleared`, `skipped`, `errors`. See
    module docstring for shape. Never raises -- all errors land
    in the `errors` list so the UI can surface them.
    """
    data_dir = Path(data_dir).resolve()

    if not data_dir.is_dir():
        return {
            "ok": False,
            "cleared": [],
            "skipped": [],
            "errors": [{"target": str(data_dir), "error": "data_dir not found"}],
        }

    cleared: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []

    # Directories first, then files. Order is cosmetic -- cleared
    # list reads top-to-bottom the same way RESET_DIRS + RESET_FILES
    # are declared above.
    for name in RESET_DIRS:
        target = (data_dir / name).resolve()
        if not _is_inside(target, data_dir):
            # Defensive: refuse to touch anything outside data_dir
            # (would only matter if a symlink pointed outside).
            errors.append({"target": name, "error": "path escapes data_dir"})
            continue
        if not target.exists():
            skipped.append(f"{name}/ (did not exist)")
            continue
        try:
            shutil.rmtree(target)
            cleared.append(f"{name}/")
            logger.info("[reset_history] cleared dir %s", target)
        except Exception as e:
            errors.append({"target": name, "error": str(e)})
            logger.warning("[reset_history] failed to clear %s: %s", target, e)

    for name in RESET_FILES:
        target = (data_dir / name).resolve()
        if not _is_inside(target, data_dir):
            errors.append({"target": name, "error": "path escapes data_dir"})
            continue
        if not target.exists():
            skipped.append(f"{name} (did not exist)")
            continue
        try:
            target.unlink()
            cleared.append(name)
            logger.info("[reset_history] unlinked %s", target)
        except Exception as e:
            errors.append({"target": name, "error": str(e)})
            logger.warning("[reset_history] failed to unlink %s: %s", target, e)

    return {
        "ok": not errors,
        "cleared": cleared,
        "skipped": skipped,
        "errors": errors,
    }


# -----------------------------------------------------------------------------
# INTERNALS
# -----------------------------------------------------------------------------
def _is_inside(target: Path, root: Path) -> bool:
    """True iff `target` is at or below `root`. Uses resolved paths so
    symlink shenanigans can't sneak a delete outside data_dir."""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False
