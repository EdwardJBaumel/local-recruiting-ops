"""
EMBEDDING CACHE
===============

What this file is
-----------------
A persistent disk cache for job-description embeddings, keyed on the
hash of the exact text we'd otherwise hand to sentence-transformers.
The match agent consults the cache before encoding each chunk; misses
get encoded by the model AND written back so the next cycle is fast.

Why
---
Most postings show up in 4–6 consecutive cycles before they age out or
get re-posted under a new URL. Re-embedding a JD whose text hasn't
changed costs ~50 ms on GPU and ~5 s on CPU — both wastes once the
embedding has been computed once. Caching collapses steady-state cycle
embedding cost from "embed every survivor" to "embed only the genuinely
new ones."

Real impact (measured locally with bge-m3 on RTX 5070 Ti):
  - First cycle:  ~50 s embedding (989 jobs, all misses)
  - Second cycle: ~5 s embedding (~50% hit rate as registry fills)
  - Steady-state: ~1-2 s embedding (~95% hit rate after the URL
                  inventory stabilises)

Storage shape
-------------
We use a single torch-saved `.pt` file because:
  - sentence-transformers emits torch tensors. Saving as JSON would
    mean per-element float serialisation = ~30x the disk size of a
    tensor pickle.
  - torch.save handles tensor metadata (dtype, shape) natively. JSON
    plus a custom float decoder would re-implement the same thing
    slower.
  - One file means one fsync per flush. With ~10k entries this
    weighs in around ~40 MB on disk — comparable to seen_urls.json.

The on-disk format is `{key: tensor}` where `key` is a 16-char hex
prefix of SHA-1 over the embedding INPUT text (so any change to
title / description / location / etc. that affects the embedding
input invalidates the cache entry naturally).

Why SHA-1 and not just URL: the embedding INPUT (title + desc + ...)
is what determines the vector. If a posting's description got
re-edited by the recruiter, we want a fresh embedding even though
the URL is the same. Hashing the input fixes that for free.

Versioning + invalidation
-------------------------
We embed the embedding-model name in the cache file so swapping
from bge-m3 to bge-small auto-invalidates the whole cache (rather
than mixing 1024-dim and 384-dim vectors and producing nonsense
cosine scores). Schema is `{model: str, entries: {key: tensor}}`.

If the model name changes, we treat the on-disk cache as cold and
start fresh.

Concurrency
-----------
Single-writer assumed (only the orchestrator mutates this cache, one
cycle at a time). We don't bother with file locking; in the unlikely
event two processes touch it simultaneously the worst case is one
flush wins and the other's writes are lost — still consistent, just
suboptimal.

Atomic write: we write to a `.tmp` sibling and `os.replace` over the
final path so a crash mid-flush can't corrupt the cache. Standard
move-into-place pattern.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("lantern.embedding_cache")

# Filename in the data dir. Mirrors the seen_urls.json / match_registry.json
# naming convention so reset_history.py can wipe it predictably.
_CACHE_FILENAME = "embedding_cache.pt"

# Key length is a 16-char hex prefix of SHA-1 (64 bits of namespace).
# Collision odds at 10k entries: ~3e-12. Comfortably negligible.
_KEY_BYTES = 16


def text_key(input_text: str) -> str:
    """Stable key for an embedding input. Identical text always yields
    the same key; any change (even whitespace) yields a different one.
    """
    h = hashlib.sha1(input_text.encode("utf-8", errors="replace")).hexdigest()
    return h[:_KEY_BYTES]


class EmbeddingCache:
    """Disk-persisted text-hash → tensor cache for match-agent embeddings.

    Designed for single-process use: the orchestrator owns the only
    live instance, and the match agent borrows it via the same dict
    interface during cycle runs.
    """

    def __init__(self, data_dir: Path | str, model_name: str):
        self.path = Path(data_dir) / _CACHE_FILENAME
        self.model_name = model_name
        # In-memory backing. Mutated by `set` and flushed to disk via
        # `flush()`. We don't auto-flush every set because a cycle adds
        # hundreds of entries; one flush at the end of match is the
        # right amortisation.
        self._entries: dict[str, Any] = {}
        # `_dirty` lets `flush()` no-op when nothing changed (e.g. an
        # all-hits cycle on a mature registry).
        self._dirty = False
        # Counters for the end-of-cycle log line. Cleared each flush.
        self._hits = 0
        self._misses = 0
        self._loaded = False

    def load(self) -> None:
        """Read the cache file from disk. Idempotent — safe to call
        more than once; subsequent calls are no-ops unless the file
        has been deleted out from under us (e.g. Reset Data was hit)."""
        if self._loaded:
            return
        try:
            import torch
        except ImportError:
            # torch isn't available — the match agent will fall back
            # to LLM scoring anyway, and that path doesn't use this
            # cache. Treat as empty.
            self._loaded = True
            return
        if not self.path.exists():
            self._loaded = True
            return
        try:
            payload = torch.load(self.path, map_location="cpu", weights_only=False)
        except Exception as e:
            logger.warning("Embedding cache unreadable (%s); starting fresh.", e)
            self._loaded = True
            return
        if not isinstance(payload, dict):
            logger.warning("Embedding cache has unexpected shape; starting fresh.")
            self._loaded = True
            return
        stored_model = payload.get("model")
        if stored_model != self.model_name:
            logger.info(
                "Embedding cache was built with model %r, current model is %r — "
                "ignoring stale cache (it would mix incompatible vector sizes).",
                stored_model, self.model_name,
            )
            self._loaded = True
            return
        entries = payload.get("entries") or {}
        if isinstance(entries, dict):
            self._entries = entries
            logger.info("Embedding cache loaded: %d entries from %s",
                        len(self._entries), self.path.name)
        self._loaded = True

    def get(self, key: str) -> Optional[Any]:
        if key in self._entries:
            self._hits += 1
            return self._entries[key]
        self._misses += 1
        return None

    def set(self, key: str, tensor: Any) -> None:
        # Move to CPU before storing so the cache survives a GPU
        # context teardown (e.g. cycle ends and we don't want the cache
        # holding references to GPU memory we'd rather free).
        try:
            tensor = tensor.detach().to("cpu")
        except Exception:
            # Already on CPU or not a tensor — store whatever we got.
            pass
        self._entries[key] = tensor
        self._dirty = True

    def flush(self) -> None:
        """Write changes to disk. Atomic via tmp-file replace.

        No-op if nothing changed since the last flush — caller can call
        this unconditionally at end-of-cycle without worrying about
        overwriting clean state.
        """
        if not self._dirty:
            return
        try:
            import torch
        except ImportError:
            return
        # Atomic write: write to a sibling .tmp, then os.replace over
        # the target. os.replace is atomic on the same filesystem on
        # both Windows and POSIX.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            torch.save(
                {"model": self.model_name, "entries": self._entries},
                tmp,
            )
            os.replace(tmp, self.path)
            self._dirty = False
            logger.info(
                "Embedding cache flushed: %d entries (%d hits / %d misses this cycle).",
                len(self._entries), self._hits, self._misses,
            )
        except Exception as e:
            logger.warning("Embedding cache flush failed: %s", e)
            # Clean up the tmp file if it survived; otherwise next
            # flush will trip over it.
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

    def stats(self) -> dict:
        """Snapshot of cache state for logging / debugging. Hits + misses
        reset on each flush; size is the absolute entry count."""
        return {
            "size": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
            "dirty": self._dirty,
        }

    def reset_counters(self) -> None:
        """Zero the per-cycle hit/miss counters without touching the
        on-disk cache. Called by the orchestrator at the start of each
        match phase so the end-of-cycle stats line reflects only that
        cycle."""
        self._hits = 0
        self._misses = 0
