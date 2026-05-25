"""Tests for the embedding-model preset registry.

Pure lookup logic so no sentence-transformers install is required.
Covers:
  a. resolve() for every documented input (None, empty, preset name,
     HF ID, pass-through for unknown strings, case-insensitivity)
  b. describe() metadata stability
  c. recommend_for_vram() picks the right tier at boundary budgets
  d. list_presets() serialisable shape
  e. Invariants: every preset is self-consistent (name matches key,
     hf_id is non-empty, vram is positive, dim > 0)
"""
import pytest

from sentinel.core import embed_presets as ep


# ─── resolve() ────────────────────────────────────────────────────
class TestResolve:
    def test_none_returns_default(self):
        assert ep.resolve(None) == ep.PRESETS[ep.DEFAULT_PRESET].hf_id

    def test_empty_string_returns_default(self):
        assert ep.resolve("") == ep.PRESETS[ep.DEFAULT_PRESET].hf_id

    def test_whitespace_returns_default(self):
        assert ep.resolve("   ") == ep.PRESETS[ep.DEFAULT_PRESET].hf_id

    def test_preset_name_resolves(self):
        assert ep.resolve("balanced") == "BAAI/bge-m3"
        assert ep.resolve("small") == "BAAI/bge-base-en-v1.5"
        assert ep.resolve("large") == "BAAI/bge-large-en-v1.5"
        assert ep.resolve("cpu") == "sentence-transformers/all-MiniLM-L6-v2"

    def test_preset_name_case_insensitive(self):
        assert ep.resolve("BALANCED") == "BAAI/bge-m3"
        assert ep.resolve("Small") == "BAAI/bge-base-en-v1.5"

    def test_hf_id_passthrough(self):
        # Registered HF id - canonicalised.
        assert ep.resolve("BAAI/bge-m3") == "BAAI/bge-m3"

    def test_hf_id_case_insensitive_match(self):
        assert ep.resolve("baai/bge-m3") == "BAAI/bge-m3"

    def test_unknown_string_passes_through(self):
        # Respect user choice; the loader will fail loudly if it's wrong.
        assert ep.resolve("some-org/novel-model") == "some-org/novel-model"

    def test_whitespace_trimmed(self):
        assert ep.resolve("  small  ") == "BAAI/bge-base-en-v1.5"


# ─── describe() ───────────────────────────────────────────────────
class TestDescribe:
    def test_preset_describe_marks_is_preset(self):
        out = ep.describe("balanced")
        assert out["is_preset"] is True
        assert out["preset"] == "balanced"
        assert out["resolved_hf_id"] == "BAAI/bge-m3"
        assert out["dim"] == 1024

    def test_unknown_hf_marks_not_preset(self):
        out = ep.describe("some-org/novel")
        assert out["is_preset"] is False
        assert out["preset"] is None
        assert out["resolved_hf_id"] == "some-org/novel"

    def test_none_describes_default(self):
        out = ep.describe(None)
        assert out["preset"] == ep.DEFAULT_PRESET

    def test_describe_shape_stable(self):
        required_keys = {"resolved_hf_id", "preset", "approx_vram_gb",
                         "dim", "notes", "is_preset"}
        assert set(ep.describe("balanced").keys()) == required_keys
        assert set(ep.describe("unknown/model").keys()) == required_keys


# ─── recommend_for_vram() ─────────────────────────────────────────
class TestRecommendForVram:
    def test_generous_vram_picks_balanced(self):
        # 12 GB GPU, 2 GB headroom -> 10 GB budget >= balanced's 2.3.
        assert ep.recommend_for_vram(12.0) == "balanced"

    def test_mid_range_picks_balanced_on_8gb(self):
        # 8 GB card, 2 GB headroom -> 6 GB for embedding. balanced fits.
        assert ep.recommend_for_vram(8.0) == "balanced"

    def test_small_gpu_picks_small(self):
        # 4 GB card, 2 GB headroom -> 2 GB budget. balanced (2.3) too big,
        # small (0.9) fits.
        assert ep.recommend_for_vram(4.0) == "small"

    def test_tiny_gpu_picks_cpu(self):
        # 2 GB card, 2 GB headroom -> 0 GB budget. Everything falls to cpu.
        assert ep.recommend_for_vram(2.0) == "cpu"

    def test_zero_vram_picks_cpu(self):
        assert ep.recommend_for_vram(0.0) == "cpu"

    def test_negative_vram_defensive(self):
        assert ep.recommend_for_vram(-5.0) == "cpu"


# ─── list_presets() + invariants ──────────────────────────────────
class TestListPresets:
    def test_list_is_non_empty_and_includes_default(self):
        presets = ep.list_presets()
        assert any(p["name"] == ep.DEFAULT_PRESET for p in presets)

    def test_each_preset_has_required_fields(self):
        for p in ep.list_presets():
            assert p["name"]
            assert p["hf_id"]
            assert p["approx_vram_gb"] > 0
            assert p["dim"] > 0
            assert isinstance(p["notes"], str) and p["notes"]


class TestPresetInvariants:
    def test_dict_key_matches_name(self):
        for key, preset in ep.PRESETS.items():
            assert preset.name == key

    def test_default_preset_exists(self):
        assert ep.DEFAULT_PRESET in ep.PRESETS

    def test_small_is_smaller_than_balanced(self):
        assert ep.PRESETS["small"].approx_vram_gb < ep.PRESETS["balanced"].approx_vram_gb
