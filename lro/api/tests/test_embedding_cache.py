"""
Unit tests for core.embedding_cache.

text_key() stability/determinism + a get/set/flush/reload round-trip
against a tmp dir + model-name-mismatch invalidation.

These use torch tensors (torch.tensor / torch.save / torch.load) which
are pure-CPU local operations — no GPU, no network, no model load.
If torch isn't importable the cache treats itself as empty by design,
so those round-trip tests skip rather than fake a result.
"""
import pytest

from core.embedding_cache import EmbeddingCache, text_key, _CACHE_FILENAME

torch = pytest.importorskip(
    "torch",
    reason="torch not installed — EmbeddingCache load/flush are no-ops without it",
)


# ─────────────────────────────────────────────────────────────────
# text_key — stability + determinism
# ─────────────────────────────────────────────────────────────────
def test_text_key_is_deterministic():
    assert text_key("title: PM\ncompany: Acme") == text_key("title: PM\ncompany: Acme")


def test_text_key_differs_on_any_change():
    base = text_key("title: PM")
    assert text_key("title: PM ") != base       # trailing space
    assert text_key("title: pm") != base        # case
    assert text_key("title: PgM") != base       # content


def test_text_key_length_and_hex():
    k = text_key("anything")
    assert len(k) == 16
    int(k, 16)  # must be valid hex — raises ValueError otherwise


def test_text_key_handles_empty_string():
    k = text_key("")
    assert len(k) == 16


def test_text_key_handles_unicode():
    # errors="replace" in the impl means this never raises.
    k = text_key("München — €140,000 \U0001F600")
    assert len(k) == 16


# ─────────────────────────────────────────────────────────────────
# get / set / flush / reload round-trip
# ─────────────────────────────────────────────────────────────────
def test_get_miss_then_set_hit(tmp_path):
    cache = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    cache.load()
    key = text_key("some job text")

    assert cache.get(key) is None          # miss
    cache.set(key, torch.tensor([1.0, 2.0, 3.0]))
    got = cache.get(key)                   # hit
    assert got is not None
    assert torch.equal(got, torch.tensor([1.0, 2.0, 3.0]))


def test_hit_miss_counters(tmp_path):
    cache = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    cache.load()
    key = text_key("job")
    cache.get(key)                         # miss
    cache.set(key, torch.tensor([0.1]))
    cache.get(key)                         # hit
    cache.get(text_key("other"))           # miss
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["size"] == 1


def test_flush_then_reload_round_trip(tmp_path):
    key = text_key("persisted job text")
    vec = torch.tensor([0.5, 0.6, 0.7, 0.8])

    writer = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    writer.load()
    writer.set(key, vec)
    writer.flush()
    assert (tmp_path / _CACHE_FILENAME).exists()

    # Fresh instance, same dir + same model name → entry survives.
    reader = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    reader.load()
    got = reader.get(key)
    assert got is not None
    assert torch.equal(got, vec)


def test_flush_is_noop_when_clean(tmp_path):
    cache = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    cache.load()
    cache.flush()  # nothing dirty
    # No file written because there was nothing to persist.
    assert not (tmp_path / _CACHE_FILENAME).exists()


def test_set_moves_tensor_to_cpu(tmp_path):
    cache = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    cache.load()
    key = text_key("job")
    cache.set(key, torch.tensor([1.0, 2.0]))
    stored = cache.get(key)
    assert stored.device.type == "cpu"


def test_reset_counters_keeps_entries(tmp_path):
    cache = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    cache.load()
    key = text_key("job")
    cache.set(key, torch.tensor([1.0]))
    cache.get(key)
    cache.reset_counters()
    stats = cache.stats()
    assert stats["hits"] == 0 and stats["misses"] == 0
    assert stats["size"] == 1            # on-disk/in-memory entries untouched
    assert cache.get(key) is not None


# ─────────────────────────────────────────────────────────────────
# model-name-mismatch invalidation
# ─────────────────────────────────────────────────────────────────
def test_model_mismatch_invalidates_cache(tmp_path):
    key = text_key("job text")
    vec = torch.tensor([1.0, 2.0, 3.0])

    # Build a cache with model A.
    writer = EmbeddingCache(tmp_path, "model-A")
    writer.load()
    writer.set(key, vec)
    writer.flush()

    # Load the SAME file as model B → stale cache is ignored entirely.
    reader_b = EmbeddingCache(tmp_path, "model-B")
    reader_b.load()
    assert reader_b.get(key) is None
    assert reader_b.stats()["size"] == 0

    # Sanity: loading as model A again still finds it.
    reader_a = EmbeddingCache(tmp_path, "model-A")
    reader_a.load()
    assert reader_a.get(key) is not None


def test_load_missing_file_is_empty(tmp_path):
    cache = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    cache.load()  # no file on disk
    assert cache.stats()["size"] == 0


def test_load_is_idempotent(tmp_path):
    key = text_key("job")
    writer = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    writer.load()
    writer.set(key, torch.tensor([1.0]))
    writer.flush()

    reader = EmbeddingCache(tmp_path, "BAAI/bge-m3")
    reader.load()
    reader.load()  # second call is a no-op, must not wipe state
    assert reader.get(key) is not None
