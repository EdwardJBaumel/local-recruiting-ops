"""
PERSISTENT MATCH REGISTRY

Near-dupe fallback
------------------
The exact dedupe key (company || title || normalised_location) catches
identical re-posts but misses "Senior PM, Ads" vs "Senior Product
Manager, Ads Platform". On upsert, if an incoming match has no exact
match but a same-company + same-normalised-location entry already exists
whose title-token Jaccard overlap is above threshold, we treat it as a
repost of that entry instead of creating a new row. This is deliberately
a token-similarity check rather than an embedding check: the EXE build
ships without sentence-transformers and we honour the "local cost is a
finite budget" rule — Jaccard is O(tokens), zero VRAM, no model load.

Single source of truth for every role that has ever scored above the
match threshold for this user. Per-cycle snapshots in data/matches/
retain run-by-run history; the UI reads from this registry so:

  a. Matches don't silently fall off after 10 cycles (server.py's
     previous /api/matches concatenated the 10 most-recent cycle files).
  b. Per-row user state (seen, dismissed, starred) survives cycles and
     restarts.
  c. "Applied" is *not* stored here - it lives in tracker.json. We join
     at read time. One source of truth per fact.

Design calls (recorded here so they survive a refactor):

  - Registry key is the same key core.dedupe uses (company || title ||
    normalised location). Stable across ingests, cheap to compute.
  - seen / dismissed / starred are independent booleans. Combinable.
    No enum, no exclusivity surprises.
  - seen flips on user interaction (click-to-expand, action button).
    Pure scroll-past does not mark seen - that was noted as too
    aggressive when we designed this.
  - profile_version is stored per entry. If the user edits their
    profile, historical matches keep their original score rather than
    silently re-scoring (re-scoring 10k entries on a profile edit would
    be brutal on local GPU).
  - Single JSON file with atomic write. Fine up to ~5k rows; there is a
    compacting + per-month partition migration path sketched at the
    bottom of this docstring but not implemented yet.

Growth envelope (back-of-envelope):

    rows | JSON size | write  | notes
    -----|-----------|--------|--------------------
     500 |   ~250 KB |  <1 ms |
    2000 |    ~1 MB  |  ~5 ms |
    5000 |   ~2.5 MB | ~15 ms | migration trigger
   10000 |    ~5 MB  | ~30 ms | UI virtualization needed

Migration trigger: when the registry crosses 5k entries, partition into
match_registry_YYYY_MM.json and keep an index file for the server. Not
implemented - left as a TODO so we don't over-engineer before we have
the real load data.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from core.io_safe import write_json_atomic
from core import dedupe as dedupe_mod

logger = logging.getLogger("lro.match_registry")

# Tokens dropped from titles before computing near-dupe Jaccard. Stop
# words only — seniority words like "senior"/"staff"/"junior" are kept
# because they are the whole point of distinguishing two near-identical
# titles at different levels. "Platform" and "team" are kept too for the
# same reason (they often identify sub-roles).
_TITLE_STOP = {"a", "an", "the", "of", "for", "in", "on", "at", "to",
               "with", "and", "or"}
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")

# Jaccard threshold above which two titles are considered the same role
# reposted. 0.75 is empirically safe: "Senior PM, Ads" vs "Senior PM,
# Ads Platform" = 0.75, "Senior PM, Ads" vs "Senior PM, Ops" = 0.60.
_NEAR_DUPE_JACCARD = 0.75

# Fields on the user-state side. Keep this list short and explicit so it
# is obvious when we're adding something new.
_STATE_FIELDS = ("seen", "dismissed", "starred", "removed")

# Fields copied out of the payload into the top-level entry so the UI
# can render a list view without re-parsing every payload. Payload is
# still stored in full, these are just a shortcut for list rendering.
_SUMMARY_FIELDS = (
    "company", "title", "location", "url", "remote", "seniority",
    "posted_date", "salary", "salary_min", "salary_max",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_key(payload: dict) -> str:
    """Reuse the cross-ATS dedupe key so the registry rides on top of the
    same identity that already handles 'same role on 3 boards'."""
    return dedupe_mod._dedupe_key(payload)


def _summary(payload: dict) -> dict:
    return {k: payload.get(k) for k in _SUMMARY_FIELDS if payload.get(k) not in (None, "")}


def _title_tokens(title: str) -> frozenset:
    """Lowercase alnum-plus-symbol tokens, minus a small stop-list."""
    if not title:
        return frozenset()
    return frozenset(
        t for t in _TOKEN_RE.findall(title.lower())
        if len(t) >= 2 and t not in _TITLE_STOP
    )


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _bucket_key(payload: dict) -> tuple[str, str]:
    """Same-company + same-normalised-location bucket for near-dupe scan.
    Reuses dedupe's location normaliser so 'SF' vs 'San Francisco' land
    in the same bucket as the exact key does."""
    company = str(payload.get("company") or "").strip().lower()
    location = dedupe_mod._normalise_location(payload.get("location") or "")
    return (company, location)


class MatchRegistry:
    """Persistent store for matches. One file, dict keyed by dedupe key.

    File layout on disk:

        {
          "version": 1,
          "updated_at": "2026-04-21T...",
          "entries": {
              "<key>": {
                  "key": "...",
                  "payload": { ... full match payload ... },
                  "summary": { title, company, ... },
                  "score": 0.72,
                  "profile_version": "<sha or null>",
                  "first_seen_cycle": 6,
                  "first_seen_at": "2026-04-21T...",
                  "last_seen_cycle": 6,
                  "last_seen_at": "2026-04-21T...",
                  "cycle_count": 1,
                  "seen": false,
                  "dismissed": false,
                  "starred": false,
                  "state_updated_at": null
              }
          }
        }
    """

    FILENAME = "match_registry.json"
    CURRENT_VERSION = 1

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / self.FILENAME
        self._cache: dict | None = None

    # ------------------------------------------------------------------ IO

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = {
                "version": self.CURRENT_VERSION,
                "updated_at": None,
                "entries": {},
            }
            return self._cache
        try:
            raw = json.loads(self.path.read_text())
        except Exception as e:
            logger.warning("match_registry: failed to parse %s (%s); starting empty", self.path, e)
            self._cache = {
                "version": self.CURRENT_VERSION,
                "updated_at": None,
                "entries": {},
            }
            return self._cache
        if "entries" not in raw or not isinstance(raw["entries"], dict):
            raw["entries"] = {}
        if "version" not in raw:
            raw["version"] = self.CURRENT_VERSION
        self._cache = raw
        return self._cache

    def _flush(self):
        if self._cache is None:
            return
        self._cache["updated_at"] = _now_iso()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, self._cache, indent=2)

    def reload(self):
        """Drop the in-memory copy and read from disk again. Useful when
        the orchestrator and the server share a process but both have
        pointers to their own MatchRegistry - after the server writes
        state the orchestrator should reload before its next upsert."""
        self._cache = None
        return self._load()

    # ----------------------------------------------------------------- WRITE

    def upsert_matches(
        self,
        matches: Iterable,
        cycle: int,
        profile_version: str | None = None,
    ) -> dict:
        """Insert new matches, update existing ones.

        `matches` is expected to be an iterable of packet-like objects
        with a `.payload` attribute, or plain payload dicts. We tolerate
        either because orchestrator's match stage mostly works in terms
        of SentinelPacket but some code paths pass dicts.

        Returns a small stats dict the orchestrator can log:
            {"added": N, "updated": N, "total": N}
        """
        registry = self._load()
        entries = registry["entries"]
        added = 0
        updated = 0
        now = _now_iso()

        for item in matches:
            payload = getattr(item, "payload", None) or item
            if not isinstance(payload, dict):
                continue
            key = _make_key(payload)
            if not key or key == "||":
                continue
            score = float(payload.get("_match_score", 0) or 0)

            existing = entries.get(key)
            if existing is None:
                near_key = self._find_near_dupe(entries, payload)
                if near_key is not None:
                    key = near_key
                    existing = entries.get(near_key)
            if existing is None:
                entries[key] = {
                    "key": key,
                    "payload": payload,
                    "summary": _summary(payload),
                    "score": score,
                    "profile_version": profile_version,
                    "first_seen_cycle": cycle,
                    "first_seen_at": now,
                    "last_seen_cycle": cycle,
                    "last_seen_at": now,
                    "cycle_count": 1,
                    "seen": False,
                    "dismissed": False,
                    "starred": False,
                    "removed": False,
                    "state_updated_at": None,
                }
                added += 1
            else:
                # Only bump score if the new one is higher - a role that
                # drops in score across cycles is noise; keep the best
                # observation so the UI sort stays stable.
                if score > float(existing.get("score", 0) or 0):
                    existing["score"] = score
                    existing["profile_version"] = profile_version
                existing["payload"] = payload  # Newer payload wins.
                existing["summary"] = _summary(payload)
                existing["last_seen_cycle"] = cycle
                existing["last_seen_at"] = now
                existing["cycle_count"] = int(existing.get("cycle_count", 0)) + 1
                updated += 1

        self._flush()
        total = len(entries)
        logger.info("match_registry: added=%d updated=%d total=%d", added, updated, total)
        return {"added": added, "updated": updated, "total": total}

    def _find_near_dupe(self, entries: dict, payload: dict) -> Optional[str]:
        """Scan same-company + same-location entries for a title-token
        Jaccard overlap above threshold. Returns the existing key if a
        near-dupe is found, else None."""
        target_tokens = _title_tokens(payload.get("title") or "")
        if not target_tokens:
            return None
        target_bucket = _bucket_key(payload)
        if not target_bucket[0]:
            return None
        best_key = None
        best_overlap = 0.0
        for k, entry in entries.items():
            ep = entry.get("payload") or {}
            if _bucket_key(ep) != target_bucket:
                continue
            overlap = _jaccard(target_tokens, _title_tokens(ep.get("title") or ""))
            if overlap >= _NEAR_DUPE_JACCARD and overlap > best_overlap:
                best_overlap = overlap
                best_key = k
        if best_key is not None:
            logger.debug(
                "match_registry: near-dupe hit title=%r -> %s (jaccard=%.2f)",
                payload.get("title"), best_key, best_overlap,
            )
        return best_key

    def set_state(self, key: str, field: str, value: bool) -> dict | None:
        """Flip one of the user-state booleans. Returns the entry after
        update, or None if the key is unknown.

        Only `seen`, `dismissed`, `starred` are writeable - applied
        lives on the tracker and must go through its API."""
        if field not in _STATE_FIELDS:
            raise ValueError(f"field must be one of {_STATE_FIELDS}, got {field!r}")
        registry = self._load()
        entry = registry["entries"].get(key)
        if entry is None:
            return None
        entry[field] = bool(value)
        entry["state_updated_at"] = _now_iso()
        self._flush()
        return entry

    def mark_seen_by_key(self, key: str) -> dict | None:
        return self.set_state(key, "seen", True)

    def get_by_url(self, url: str) -> dict | None:
        """Find an entry whose payload's `url` matches.

        Used by the /api/summarize endpoint, which receives the job
        URL from the FE rather than the internal dedupe key. We do a
        linear scan because the registry is small (typically <1000
        entries) and URLs are not part of the primary key. If this
        ever shows up in a profile, add a secondary url→key index
        on _flush. Returns None if no entry matches.
        """
        if not url:
            return None
        registry = self._load()
        for entry in registry["entries"].values():
            payload = entry.get("payload") or {}
            if payload.get("url") == url:
                return entry
        return None

    def set_payload_field(self, url: str, field: str, value) -> bool:
        """Mutate one field on a registry entry's payload, persist,
        return True on success.

        Used to cache LLM-generated artefacts (summary, fit-gap blob,
        etc.) directly into the entry the FE already polls — saves a
        separate cache file and keeps the registry the single source
        of truth for what's known about each role.

        We deliberately allow ANY field name here (no allowlist) so
        future caches don't need a registry change. The convention
        is to prefix LLM-cache fields with `_` so they don't collide
        with parsed JD fields (title, company, ...).
        """
        if not url or not field:
            return False
        registry = self._load()
        for entry in registry["entries"].values():
            payload = entry.get("payload")
            if isinstance(payload, dict) and payload.get("url") == url:
                payload[field] = value
                entry["state_updated_at"] = _now_iso()
                self._flush()
                return True
        return False

    # ------------------------------------------------------------------ READ

    def all_entries(self) -> list[dict]:
        """Return all entries as a list, sorted by score desc then
        last_seen_at desc. Cheap - the sort is done on the already-loaded
        in-memory dict; no disk read unless the cache is empty."""
        registry = self._load()
        out = list(registry["entries"].values())
        out.sort(
            key=lambda e: (float(e.get("score", 0) or 0), e.get("last_seen_at") or ""),
            reverse=True,
        )
        return out

    def entries_by_key(self) -> dict:
        """Return the raw {dedupe_key: entry} map. Used by the feedback
        learner which needs to cross-reference its own cache against the
        user's current starred/dismissed set."""
        registry = self._load()
        # Return a shallow copy so external callers can't mutate the
        # in-memory registry accidentally.
        return dict(registry["entries"])

    def stats(self) -> dict:
        registry = self._load()
        entries = registry["entries"].values()
        n = len(registry["entries"])
        seen = sum(1 for e in entries if e.get("seen"))
        dismissed = sum(1 for e in entries if e.get("dismissed"))
        starred = sum(1 for e in entries if e.get("starred"))
        return {
            "total": n,
            "seen": seen,
            "unseen": n - seen,
            "dismissed": dismissed,
            "starred": starred,
            "updated_at": registry.get("updated_at"),
        }


# Module-level helpers so callers don't all need to instantiate.
_REGISTRY_SINGLETONS: dict[str, MatchRegistry] = {}


def get_registry(data_dir: Path | str) -> MatchRegistry:
    """Return a shared MatchRegistry for `data_dir`. Sharing one
    instance per data_dir keeps the in-memory cache coherent within a
    process - both orchestrator and server use `get_registry` so they
    read/write through the same cache."""
    key = str(Path(data_dir).resolve())
    inst = _REGISTRY_SINGLETONS.get(key)
    if inst is None:
        inst = MatchRegistry(key)
        _REGISTRY_SINGLETONS[key] = inst
    return inst
