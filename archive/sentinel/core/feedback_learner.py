"""
FEEDBACK LEARNER

Closes the loop between user saves/dismisses (tracked in the match
registry) and future match scoring. When the user hearts a job, its
embedding is cached here and used to boost similar jobs on future
cycles. Same for dismisses, with an inverted sign.

Design calls:

  a. Embeddings cached in a separate JSON file (not the registry) so
     the registry stays small and diff-friendly. Cache keys are the
     same dedupe keys the registry uses, so cross-referencing is
     trivial.
  b. Lazy compute: the cache is only populated when refresh() is
     called. Stale keys (no longer in registry) are dropped in that
     pass, so a user who unstars a job also removes its influence.
  c. Gates: boost/penalty only applied when the respective set has at
     least MIN_SET_SIZE entries. Below that the signal is too noisy
     to be useful.
  d. Weights blend via:
         base              if no saves
         0.75*base + 0.25*boost   if saves present
     Then dismiss_penalty is subtracted at weight 0.15 (capped so
     the final value can't go negative).
  e. Dismiss signal is used gently because dismissals are cheaper to
     emit than saves and therefore noisier. Don't want a single
     accidental click to tank future matches.

Storage path: <data_dir>/feedback_embeddings.json

    {
      "version": 1,
      "updated_at": "2026-04-21T...",
      "embeddings": {
        "<dedupe_key>": {
          "state": "starred" | "dismissed",
          "vec": [0.1, ...]    # float32, length = model dim
        }
      }
    }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from core.io_safe import write_json_atomic

logger = logging.getLogger("sentinel.feedback_learner")

FILENAME = "feedback_embeddings.json"
MIN_SET_SIZE = 3  # Below this, the signal is too noisy to apply.
SAVE_BLEND = 0.25  # Weight on save-boost in the blended score.
DISMISS_WEIGHT = 0.15  # Weight on dismiss-penalty subtraction.

# Cold-start seed config. Seeds are synthetic "proxy-starred" vectors
# derived from the user's resume text + role keywords. They are only
# consulted when the real starred set is below MIN_SET_SIZE so the
# actual user signal always dominates once it exists. Seeds are keyed
# with a reserved prefix so they never collide with dedupe keys and
# always survive refresh() (which only touches registry-backed keys).
SEED_STATE = "seed"
SEED_KEY_PREFIX = "__seed__:"
SEED_BLEND = 0.12  # Deliberately below SAVE_BLEND: synthetic signal.
SEED_MIN_SET_SIZE = 2  # One good anchor is noisy; two is floor.


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_text(payload: dict) -> str:
    """Mirror MatchAgent._job_to_text so embeddings are comparable
    across the scorer and the learner. Kept here as a tiny duplicate
    to avoid a circular import."""
    parts = []
    for key in ("title", "company", "location", "description", "seniority", "remote"):
        val = payload.get(key)
        if val:
            parts.append(f"{key}: {val}")
    techs = payload.get("technologies", [])
    if techs:
        parts.append(f"technologies: {', '.join(techs)}")
    return "\n".join(parts)


class FeedbackLearner:
    """Caches embeddings for starred and dismissed jobs, computes
    boost/penalty against a target job embedding. Stateless between
    refreshes — call refresh() once per cycle."""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / FILENAME
        # In-memory lookup: key -> {state, vec (tensor or list)}
        self._entries: dict[str, dict] = {}
        # Tracks what profile text we last seeded from so re-seeding only
        # happens when the resume / role keywords actually change. Kept
        # in the cache file so it survives restarts.
        self._profile_seed_hash: str | None = None
        self._load_from_disk()

    # ───────────────────────────────────────────────────────────── IO
    def _load_from_disk(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            embeddings = raw.get("embeddings") or {}
            self._profile_seed_hash = raw.get("profile_seed_hash")
            for key, entry in embeddings.items():
                if not isinstance(entry, dict):
                    continue
                vec = entry.get("vec")
                state = entry.get("state")
                if not isinstance(vec, list) or state not in ("starred", "dismissed", SEED_STATE):
                    continue
                self._entries[key] = {"state": state, "vec": vec}
            logger.info("FeedbackLearner: loaded %d cached embeddings", len(self._entries))
        except Exception as e:
            logger.warning("FeedbackLearner: cache parse failed (%s); starting empty", e)
            self._entries = {}
            self._profile_seed_hash = None

    def _flush(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        serialisable = {
            k: {"state": v["state"], "vec": v["vec"] if isinstance(v["vec"], list) else v["vec"].tolist()}
            for k, v in self._entries.items()
        }
        payload = {
            "version": 1,
            "updated_at": _now_iso(),
            "embeddings": serialisable,
        }
        if self._profile_seed_hash is not None:
            payload["profile_seed_hash"] = self._profile_seed_hash
        write_json_atomic(
            self.path,
            payload,
            indent=None,  # keep compact - these files get large
        )

    # ─────────────────────────────────────────────────────────── REFRESH
    def refresh(self, registry_entries: dict, embed_model) -> dict:
        """Ensure every currently-starred-or-dismissed registry entry has
        an embedding cached. Drops embeddings whose keys have been
        un-starred / un-dismissed. Returns small stats dict for logging.

        registry_entries: dict keyed by dedupe_key with the registry
            entry schema (fields: starred, dismissed, payload).
        embed_model: sentence-transformers SentenceTransformer instance
            from MatchAgent. None is tolerated - we skip the work.
        """
        if embed_model is None:
            return {"added": 0, "dropped": 0, "kept": len(self._entries)}

        desired: dict[str, str] = {}  # key -> state
        for key, entry in (registry_entries or {}).items():
            if not isinstance(entry, dict):
                continue
            if entry.get("starred"):
                desired[key] = "starred"
            elif entry.get("dismissed"):
                desired[key] = "dismissed"

        # Drop anything no longer in desired or whose state changed.
        # Seed entries (state=SEED_STATE, key=SEED_KEY_PREFIX+...) are
        # not registry-backed and must survive refresh(); only
        # seed_from_profile() manages them.
        dropped = 0
        for key in list(self._entries.keys()):
            current_state = self._entries[key].get("state")
            if current_state == SEED_STATE or key.startswith(SEED_KEY_PREFIX):
                continue
            if key not in desired or desired[key] != current_state:
                del self._entries[key]
                dropped += 1

        # Add any new entries.
        added = 0
        for key, state in desired.items():
            if key in self._entries:
                continue
            entry = registry_entries.get(key) or {}
            payload = entry.get("payload") or {}
            text = _job_text(payload)
            if not text:
                continue
            try:
                vec = embed_model.encode(text, convert_to_tensor=False)
                vec_list = vec.tolist() if hasattr(vec, "tolist") else list(vec)
            except Exception as e:
                logger.warning("FeedbackLearner: encode failed for %s (%s)", key[:40], e)
                continue
            self._entries[key] = {"state": state, "vec": vec_list}
            added += 1

        if added or dropped:
            try:
                self._flush()
            except Exception as e:
                logger.warning("FeedbackLearner: cache flush failed: %s", e)

        return {"added": added, "dropped": dropped, "kept": len(self._entries)}

    # ───────────────────────────────────────────────── SEED (COLD START)
    def seed_from_profile(
        self,
        profile_text: str,
        embed_model,
        role_keywords: Iterable[str] | None = None,
    ) -> dict:
        """Populate synthetic 'seed' vectors from the user's resume text
        and role keywords. Seeds are used by `adjust()` as a cold-start
        fallback when the real starred set is below threshold; they never
        supersede real user signal once it exists.

        Idempotent: skips encoding when the same (profile, keywords)
        combination was already seeded. Replaces all prior seeds when
        the input changes.

        Returns a small stats dict: {added, dropped, skipped, source_hash}.
        """
        import hashlib

        profile_text = (profile_text or "").strip()
        kws = [k.strip() for k in (role_keywords or []) if isinstance(k, str) and k.strip()]

        # Stable hash of the inputs so we can no-op when nothing changed.
        hasher = hashlib.sha256()
        hasher.update(profile_text.encode("utf-8", errors="replace"))
        for k in sorted(kws):
            hasher.update(b"\x1f")
            hasher.update(k.encode("utf-8", errors="replace"))
        source_hash = hasher.hexdigest()

        if not profile_text or embed_model is None:
            return {"added": 0, "dropped": 0, "skipped": 0, "source_hash": None}

        existing_seed_count = sum(
            1 for v in self._entries.values() if v.get("state") == SEED_STATE
        )
        if self._profile_seed_hash == source_hash and existing_seed_count > 0:
            return {"added": 0, "dropped": 0, "skipped": existing_seed_count,
                    "source_hash": source_hash}

        # Drop all prior seeds, encode fresh.
        dropped = 0
        for key in list(self._entries.keys()):
            if self._entries[key].get("state") == SEED_STATE:
                del self._entries[key]
                dropped += 1

        chunks: list[tuple[str, str]] = [("profile", profile_text)]
        for kw in kws:
            chunks.append((f"keyword:{kw[:60]}", kw))

        added = 0
        for sub_key, text in chunks:
            try:
                vec = embed_model.encode(text, convert_to_tensor=False)
                vec_list = vec.tolist() if hasattr(vec, "tolist") else list(vec)
            except Exception as e:
                logger.warning("FeedbackLearner: seed encode failed (%s): %s",
                               sub_key[:40], e)
                continue
            self._entries[f"{SEED_KEY_PREFIX}{sub_key}"] = {
                "state": SEED_STATE, "vec": vec_list
            }
            added += 1

        self._profile_seed_hash = source_hash if added else None
        if added or dropped:
            try:
                self._flush()
            except Exception as e:
                logger.warning("FeedbackLearner: seed flush failed: %s", e)

        return {"added": added, "dropped": dropped, "skipped": 0,
                "source_hash": source_hash}

    # ───────────────────────────────────────────────────── QUERY HELPERS
    def _max_cosine(self, job_vec, state: str, min_count: int = MIN_SET_SIZE) -> float | None:
        """Max cosine similarity between job_vec (list or tensor) and
        every cached vec of the given state. None if fewer than
        min_count entries of that state are cached."""
        # Lazy import so this module has no hard dependency on torch
        # when embeddings are not installed.
        candidates = [v["vec"] for v in self._entries.values() if v["state"] == state]
        if len(candidates) < min_count:
            return None
        try:
            import torch
            t_cands = torch.tensor(candidates, dtype=torch.float32)
            if hasattr(job_vec, "cpu"):
                t_job = job_vec.detach().to(dtype=torch.float32).flatten()
            else:
                t_job = torch.tensor(list(job_vec), dtype=torch.float32)
            # Normalise then dot product = cosine.
            t_cands_n = t_cands / (t_cands.norm(dim=1, keepdim=True) + 1e-8)
            t_job_n = t_job / (t_job.norm() + 1e-8)
            sims = (t_cands_n @ t_job_n).tolist()
        except Exception as e:
            logger.warning("FeedbackLearner: cosine computation failed: %s", e)
            return None
        return float(max(sims)) if sims else None

    def adjust(self, base_score: float, job_embedding) -> tuple[float, dict]:
        """Blend save-boost and dismiss-penalty into the base cosine.

        Returns (adjusted_score, telemetry_dict). The telemetry is
        attached to the match payload so the UI can explain why a
        score changed.
        """
        tele: dict = {}
        final = float(base_score)

        save_boost = self._max_cosine(job_embedding, "starred")
        if save_boost is not None:
            final = (1.0 - SAVE_BLEND) * final + SAVE_BLEND * save_boost
            tele["save_boost"] = round(save_boost, 4)
            tele["save_set_size"] = sum(1 for v in self._entries.values() if v["state"] == "starred")
        else:
            # Cold-start fallback. Seeds (resume text + role keywords)
            # stand in for the user's starred signal until it actually
            # exists. Weaker blend than a real star so seeds can't fight
            # a genuine user preference once it arrives. Suppressed
            # entirely once save_boost crosses MIN_SET_SIZE.
            seed_boost = self._max_cosine(
                job_embedding, SEED_STATE, min_count=SEED_MIN_SET_SIZE
            )
            if seed_boost is not None:
                final = (1.0 - SEED_BLEND) * final + SEED_BLEND * seed_boost
                tele["seed_boost"] = round(seed_boost, 4)
                tele["seed_set_size"] = sum(
                    1 for v in self._entries.values() if v["state"] == SEED_STATE
                )

        dismiss_pen = self._max_cosine(job_embedding, "dismissed")
        if dismiss_pen is not None and dismiss_pen > 0:
            final -= DISMISS_WEIGHT * dismiss_pen
            tele["dismiss_penalty"] = round(dismiss_pen, 4)
            tele["dismiss_set_size"] = sum(1 for v in self._entries.values() if v["state"] == "dismissed")

        # Keep in the cosine-space [0, 1] band so downstream tier logic
        # still makes sense.
        final = max(0.0, min(1.0, final))
        return final, tele

    # ───────────────────────────────────────────────── INTROSPECTION
    def stats(self) -> dict:
        starred = sum(1 for v in self._entries.values() if v["state"] == "starred")
        dismissed = sum(1 for v in self._entries.values() if v["state"] == "dismissed")
        seeded = sum(1 for v in self._entries.values() if v["state"] == SEED_STATE)
        return {
            "starred_cached": starred,
            "dismissed_cached": dismissed,
            "seeded_cached": seeded,
            "min_set_size": MIN_SET_SIZE,
            "seed_min_set_size": SEED_MIN_SET_SIZE,
            "save_blend": SAVE_BLEND,
            "seed_blend": SEED_BLEND,
            "dismiss_weight": DISMISS_WEIGHT,
            "profile_seed_hash": self._profile_seed_hash,
        }
