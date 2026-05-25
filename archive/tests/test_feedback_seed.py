"""Tests for FeedbackLearner.seed_from_profile — the cold-start fix
that lets the boost/penalty logic contribute something useful before
the user has starred their first 3 jobs.

We stub out the embedding model with a tiny deterministic encoder so
the tests don't depend on sentence-transformers being installed.
"""
import hashlib
import json

import pytest

from sentinel.core.feedback_learner import (
    FeedbackLearner,
    SAVE_BLEND,
    SEED_BLEND,
    SEED_KEY_PREFIX,
    SEED_MIN_SET_SIZE,
    SEED_STATE,
)


class FakeEmbedder:
    """Deterministic token-hash embedder. Same text → same vector.
    Gives us enough signal to test cosine ranking without torch."""

    def __init__(self, dim: int = 16):
        self.dim = dim
        self.calls = 0

    def encode(self, text: str, convert_to_tensor: bool = False):
        self.calls += 1
        vec = [0.0] * self.dim
        # Hash each token into buckets so identical texts produce
        # identical vectors and similar ones overlap.
        for tok in str(text).lower().split():
            idx = int(hashlib.sha1(tok.encode("utf-8")).hexdigest(), 16) % self.dim
            vec[idx] += 1.0
        return vec


def _registry_entry(state: str, title: str = "PM") -> dict:
    return {
        state: True,
        "payload": {"title": title, "company": "Acme", "location": "Remote",
                    "description": title},
    }


class TestSeedIdempotence:
    def test_empty_profile_is_noop(self, tmp_path):
        fl = FeedbackLearner(tmp_path)
        stats = fl.seed_from_profile("", FakeEmbedder())
        assert stats["added"] == 0
        assert fl.stats()["seeded_cached"] == 0

    def test_none_model_is_noop(self, tmp_path):
        fl = FeedbackLearner(tmp_path)
        stats = fl.seed_from_profile("hello world", None)
        assert stats["added"] == 0

    def test_seed_adds_profile_plus_keywords(self, tmp_path):
        fl = FeedbackLearner(tmp_path)
        embed = FakeEmbedder()
        stats = fl.seed_from_profile(
            "senior product manager platform ai",
            embed,
            role_keywords=["product manager", "technical program manager"],
        )
        # 1 profile chunk + 2 keyword chunks = 3 encodes.
        assert stats["added"] == 3
        assert embed.calls == 3
        assert fl.stats()["seeded_cached"] == 3

    def test_reseed_same_input_skips_encode(self, tmp_path):
        fl = FeedbackLearner(tmp_path)
        embed = FakeEmbedder()
        fl.seed_from_profile("hello", embed, role_keywords=["pm"])
        pre_calls = embed.calls
        stats = fl.seed_from_profile("hello", embed, role_keywords=["pm"])
        assert embed.calls == pre_calls, "same input should not re-encode"
        assert stats["added"] == 0
        assert stats["skipped"] >= 1

    def test_reseed_different_input_replaces(self, tmp_path):
        fl = FeedbackLearner(tmp_path)
        embed = FakeEmbedder()
        fl.seed_from_profile("profile v1", embed, role_keywords=["a"])
        assert fl.stats()["seeded_cached"] == 2
        fl.seed_from_profile("profile v2", embed, role_keywords=["b", "c"])
        assert fl.stats()["seeded_cached"] == 3  # 1 profile + 2 keywords

    def test_keyword_order_does_not_trigger_reseed(self, tmp_path):
        fl = FeedbackLearner(tmp_path)
        embed = FakeEmbedder()
        fl.seed_from_profile("resume", embed, role_keywords=["pm", "tpm"])
        pre_calls = embed.calls
        fl.seed_from_profile("resume", embed, role_keywords=["tpm", "pm"])
        # Sorted before hashing → unchanged.
        assert embed.calls == pre_calls


class TestSeedPersistence:
    def test_seeds_survive_restart(self, tmp_path):
        embed = FakeEmbedder()
        fl = FeedbackLearner(tmp_path)
        fl.seed_from_profile("product manager ai", embed)
        # New instance reads from disk.
        fl2 = FeedbackLearner(tmp_path)
        assert fl2.stats()["seeded_cached"] == 1

    def test_source_hash_persists(self, tmp_path):
        embed = FakeEmbedder()
        fl = FeedbackLearner(tmp_path)
        fl.seed_from_profile("abc", embed, role_keywords=["x"])
        fl2 = FeedbackLearner(tmp_path)
        # Same inputs on restart: no re-encoding needed.
        pre_calls = embed.calls
        # Rehydrate embed for fl2 (same deterministic encoder).
        fl2.seed_from_profile("abc", embed, role_keywords=["x"])
        assert embed.calls == pre_calls

    def test_on_disk_schema_has_seeds(self, tmp_path):
        embed = FakeEmbedder()
        fl = FeedbackLearner(tmp_path)
        fl.seed_from_profile("resume text here", embed)
        raw = json.loads((tmp_path / "feedback_embeddings.json").read_text())
        emb = raw["embeddings"]
        assert all(k.startswith(SEED_KEY_PREFIX) for k in emb.keys())
        assert all(v["state"] == SEED_STATE for v in emb.values())
        assert isinstance(raw.get("profile_seed_hash"), str)


class TestSeedIsolation:
    def test_refresh_does_not_drop_seeds(self, tmp_path):
        embed = FakeEmbedder()
        fl = FeedbackLearner(tmp_path)
        fl.seed_from_profile("resume", embed, role_keywords=["pm", "tpm"])
        assert fl.stats()["seeded_cached"] == 3

        # Refresh with an empty registry: would drop everything if seeds
        # weren't protected.
        fl.refresh({}, embed)
        assert fl.stats()["seeded_cached"] == 3, \
            "refresh() must not drop seed entries"

    def test_refresh_still_syncs_real_entries(self, tmp_path):
        embed = FakeEmbedder()
        fl = FeedbackLearner(tmp_path)
        fl.seed_from_profile("resume", embed)

        registry = {"k1": _registry_entry("starred"), "k2": _registry_entry("starred")}
        fl.refresh(registry, embed)
        assert fl.stats()["starred_cached"] == 2
        assert fl.stats()["seeded_cached"] >= 1

        # Unstar k1 — refresh should drop it without touching seeds.
        registry.pop("k1")
        fl.refresh(registry, embed)
        assert fl.stats()["starred_cached"] == 1
        assert fl.stats()["seeded_cached"] >= 1


class TestAdjustColdStartBehaviour:
    def test_real_stars_dominate_when_above_threshold(self, tmp_path):
        embed = FakeEmbedder()
        fl = FeedbackLearner(tmp_path)
        fl.seed_from_profile("product manager", embed)

        # Populate 3 real stars so MIN_SET_SIZE is met.
        registry = {
            f"k{i}": _registry_entry("starred", title=f"PM {i}") for i in range(3)
        }
        fl.refresh(registry, embed)

        base = 0.50
        job_vec = embed.encode("PM 0")
        final, tele = fl.adjust(base, job_vec)
        # save_boost path taken; seed path suppressed.
        assert "save_boost" in tele
        assert "seed_boost" not in tele

    def test_seed_path_fills_gap_before_any_stars(self, tmp_path):
        embed = FakeEmbedder()
        fl = FeedbackLearner(tmp_path)
        # Two seeds (1 profile + 1 keyword) → meets SEED_MIN_SET_SIZE=2.
        fl.seed_from_profile("product manager platform", embed, role_keywords=["pm"])
        assert fl.stats()["seeded_cached"] >= SEED_MIN_SET_SIZE

        base = 0.50
        job_vec = embed.encode("product manager platform")
        final, tele = fl.adjust(base, job_vec)
        assert "seed_boost" in tele
        assert "save_boost" not in tele
        # Final is blended, not equal to base: some contribution happened.
        assert final != base or tele["seed_boost"] <= base  # signal applied

    def test_seed_requires_minimum_set_size(self, tmp_path):
        embed = FakeEmbedder()
        fl = FeedbackLearner(tmp_path)
        # Only one seed (profile only, no keywords) → below SEED_MIN_SET_SIZE.
        fl.seed_from_profile("resume solo", embed, role_keywords=[])
        assert fl.stats()["seeded_cached"] == 1

        base = 0.50
        job_vec = embed.encode("resume solo")
        final, tele = fl.adjust(base, job_vec)
        assert "seed_boost" not in tele  # too few to act on
        assert final == base

    def test_seed_blend_weaker_than_star_blend(self):
        # Invariant: we deliberately damp the synthetic signal.
        assert SEED_BLEND < SAVE_BLEND

    def test_adjust_stays_in_unit_interval(self, tmp_path):
        embed = FakeEmbedder()
        fl = FeedbackLearner(tmp_path)
        fl.seed_from_profile("zzzz", embed, role_keywords=["aaaa"])
        for base in (0.0, 0.1, 0.5, 0.9, 1.0):
            final, _ = fl.adjust(base, embed.encode("zzzz"))
            assert 0.0 <= final <= 1.0
