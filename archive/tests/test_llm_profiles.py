"""Tests for the LLM profile resolver.

Verifies the consolidation-path promise: when the user picks a named
profile, every missing per-stage model is filled in; anything they
set themselves is untouched.
"""
import copy

import pytest

from sentinel.core import llm_profiles as lp


# ─── list_profiles / describe ─────────────────────────────────────
class TestRegistryShape:
    def test_all_profiles_listed(self):
        names = {p["name"] for p in lp.list_profiles()}
        assert {"lightweight", "compact", "full"} <= names

    def test_default_profile_exists(self):
        assert lp.DEFAULT_PROFILE in lp.PROFILES

    def test_describe_unknown_marks_not_known(self):
        out = lp.describe("not-a-profile")
        assert out["known"] is False

    def test_describe_custom_is_known(self):
        assert lp.describe("custom")["known"] is True

    def test_describe_none_returns_stub(self):
        out = lp.describe(None)
        assert out["known"] is False
        assert out["name"] is None

    def test_describe_profile_includes_stages(self):
        out = lp.describe("compact")
        # Every stage path should appear in the stages dict.
        assert set(out["stages"].keys()) == {
            "parse", "match", "analyze", "chat", "digest", "cover_letter"
        }


# ─── apply_profile ────────────────────────────────────────────────
class TestApplyProfile:
    def test_no_profile_field_returns_unchanged(self):
        cfg = {"parse": {"model": "mine"}}
        assert lp.apply_profile(cfg) == cfg

    def test_custom_profile_returns_unchanged(self):
        cfg = {"llm_profile": "custom", "parse": {"model": "mine"}}
        out = lp.apply_profile(cfg)
        assert out == cfg

    def test_unknown_profile_returns_unchanged_with_field(self):
        cfg = {"llm_profile": "quantum", "parse": {"model": "mine"}}
        out = lp.apply_profile(cfg)
        # Unknown profiles are ignored; don't crash, don't mutate.
        assert out.get("parse", {}).get("model") == "mine"

    def test_lightweight_fills_all_stages(self):
        cfg = {"llm_profile": "lightweight"}
        out = lp.apply_profile(cfg)
        assert out["parse"]["model"] == "qwen3:8b"
        assert out["match"]["model"] == "qwen3:8b"
        assert out["analyze_model"] == "qwen3:8b"
        assert out["chat_model"] == "qwen3:8b"
        assert out["digest_model"] == "qwen3:8b"
        assert out["cover_letter_model"] == "qwen3:8b"

    def test_compact_fills_two_model_set(self):
        cfg = {"llm_profile": "compact"}
        out = lp.apply_profile(cfg)
        # Profile promise: parse uses the smaller model, everything else
        # uses the 14B reasoning model.
        assert out["parse"]["model"] == "qwen2.5:7b"
        assert out["match"]["model"] == "qwen3:14b"
        assert out["analyze_model"] == "qwen3:14b"
        # Two unique models across the stage set.
        unique = {out["parse"]["model"], out["match"]["model"],
                  out["analyze_model"], out["chat_model"],
                  out["digest_model"], out["cover_letter_model"]}
        assert len(unique) == 2

    def test_full_preserves_four_model_specialisation(self):
        out = lp.apply_profile({"llm_profile": "full"})
        assert out["parse"]["model"] == "qwen2.5:14b"
        assert out["analyze_model"] == "deepseek-r1:14b"
        assert out["digest_model"] == "gemma3:12b"

    def test_case_insensitive_profile_name(self):
        out = lp.apply_profile({"llm_profile": "COMPACT"})
        assert out["parse"]["model"] == "qwen2.5:7b"

    def test_whitespace_trimmed(self):
        out = lp.apply_profile({"llm_profile": "  compact  "})
        assert out["parse"]["model"] == "qwen2.5:7b"

    def test_user_override_wins(self):
        # User pinned parse.model; profile fills only the unset stages.
        cfg = {"llm_profile": "lightweight", "parse": {"model": "my-pinned"}}
        out = lp.apply_profile(cfg)
        assert out["parse"]["model"] == "my-pinned"  # untouched
        assert out["analyze_model"] == "qwen3:8b"    # filled

    def test_empty_string_stage_is_treated_as_unset(self):
        cfg = {"llm_profile": "lightweight", "parse": {"model": ""},
               "chat_model": "   "}
        out = lp.apply_profile(cfg)
        assert out["parse"]["model"] == "qwen3:8b"
        assert out["chat_model"] == "qwen3:8b"

    def test_existing_section_without_model_key(self):
        cfg = {"llm_profile": "lightweight", "parse": {"threshold": 0.5}}
        out = lp.apply_profile(cfg)
        assert out["parse"]["model"] == "qwen3:8b"
        # Non-model fields preserved.
        assert out["parse"]["threshold"] == 0.5

    def test_immutability_of_input(self):
        cfg = {"llm_profile": "lightweight", "parse": {"model": None}}
        snapshot = copy.deepcopy(cfg)
        _ = lp.apply_profile(cfg)
        # Input must not be mutated.
        assert cfg == snapshot

    def test_non_dict_input_is_returned_as_is(self):
        assert lp.apply_profile(None) is None
        assert lp.apply_profile("not a dict") == "not a dict"

    def test_cover_letter_model_filled(self):
        out = lp.apply_profile({"llm_profile": "compact"})
        assert out["cover_letter_model"] == "qwen3:14b"


class TestProfileInvariants:
    def test_lightweight_is_truly_single_model(self):
        p = lp.PROFILES["lightweight"]
        stages = {p.parse, p.match, p.analyze, p.chat, p.digest, p.cover_letter}
        assert len(stages) == 1

    def test_compact_is_two_models(self):
        p = lp.PROFILES["compact"]
        stages = {p.parse, p.match, p.analyze, p.chat, p.digest, p.cover_letter}
        assert len(stages) == 2

    def test_approx_unique_models_matches_reality(self):
        for key, p in lp.PROFILES.items():
            stages = {p.parse, p.match, p.analyze, p.chat, p.digest, p.cover_letter}
            assert len(stages) == p.approx_unique_models, f"{key} miscounted"
