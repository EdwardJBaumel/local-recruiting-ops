"""Tests for core/fake_detector.py — deterministic ghost-job signals."""
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.core.fake_detector import (
    AGGRESSIVENESS_PRESETS,
    GHOST_SUSPECT_THRESHOLD,
    resolve_threshold,
    score_fake,
)


NOW = datetime(2026, 4, 21, tzinfo=timezone.utc)


def _clean_packet(**over):
    """A plausible, non-suspect packet. Uses posted_at within the fresh
    window, concrete location, apply URL, reasonable description length."""
    base = {
        "title": "Senior Product Manager",
        "company": "Acme",
        "location": "London, UK",
        "description": (
            "We are hiring a Senior Product Manager to lead our payments "
            "platform team. You will own the roadmap, work with engineering "
            "and design, and drive adoption of our APIs. 5+ years of PM "
            "experience required, fintech background a plus. You will "
            "partner with cross-functional teams to ship high-quality "
            "product outcomes and measure impact against clear KPIs."
        ),
        "technologies": ["product-management", "sql"],
        "apply_url": "https://acme.example.com/apply/123",
        "posted_at": (NOW - timedelta(days=3)).isoformat(),
    }
    base.update(over)
    return base


class TestThresholds:
    def test_default_constant(self):
        assert GHOST_SUSPECT_THRESHOLD == 0.45

    def test_resolve_preset_strings(self):
        assert resolve_threshold("balanced") == AGGRESSIVENESS_PRESETS["balanced"]
        assert resolve_threshold("strict") == AGGRESSIVENESS_PRESETS["strict"]
        assert resolve_threshold("low") == AGGRESSIVENESS_PRESETS["low"]

    def test_resolve_numeric_passthrough(self):
        assert resolve_threshold(0.33) == 0.33

    def test_resolve_out_of_range_falls_back(self):
        assert resolve_threshold(1.5) == GHOST_SUSPECT_THRESHOLD
        assert resolve_threshold("garbage") == GHOST_SUSPECT_THRESHOLD


class TestCleanPacket:
    def test_clean_below_threshold(self):
        out = score_fake(_clean_packet(), now=NOW)
        assert out["is_suspect"] is False
        assert out["score"] < GHOST_SUSPECT_THRESHOLD

    def test_empty_payload_zero(self):
        assert score_fake({})["score"] == 0.0
        assert score_fake(None)["is_suspect"] is False


class TestSignals:
    def test_stale_posting_fires(self):
        old = _clean_packet(posted_at=(NOW - timedelta(days=120)).isoformat())
        out = score_fake(old, now=NOW)
        assert "age_stale" in out["signals"]
        assert out["signals"]["age_stale"]["score"] > 0

    def test_wide_salary_band_fires(self):
        p = _clean_packet(salary_min=40_000, salary_max=400_000)
        out = score_fake(p, now=NOW)
        assert out["signals"]["salary_band_wide"]["score"] > 0

    def test_narrow_salary_band_benign(self):
        p = _clean_packet(salary_min=150_000, salary_max=180_000)
        out = score_fake(p, now=NOW)
        assert out["signals"]["salary_band_wide"]["score"] == 0.0

    def test_vague_location_fires(self):
        p = _clean_packet(location="anywhere")
        out = score_fake(p, now=NOW)
        assert out["signals"]["location_vague"]["score"] > 0

    def test_remote_flag_forgives_country_only(self):
        p = _clean_packet(location="united states", remote=True)
        out = score_fake(p, now=NOW)
        # Not "anywhere" — with remote=True, country-level is fine.
        assert out["signals"]["location_vague"]["score"] == 0.0

    def test_missing_fields_fire(self):
        p = {"title": "PM", "posted_at": NOW.isoformat()}
        out = score_fake(p, now=NOW)
        assert out["signals"]["missing_fields"]["score"] > 0

    def test_missing_apply_url_fires(self):
        p = _clean_packet(apply_url="", url="")
        out = score_fake(p, now=NOW)
        assert out["signals"]["apply_url_missing"]["score"] > 0

    def test_seniority_conflict_fires(self):
        p = _clean_packet(
            title="Junior Product Manager",
            description=(
                "We need someone with 12+ years of product leadership "
                "experience, having led cross-functional teams at scale "
                "and mentored junior and mid-level PMs across a broad "
                "portfolio of product initiatives end-to-end."
            ),
        )
        out = score_fake(p, now=NOW)
        assert out["signals"]["seniority_conflict"]["score"] > 0

    def test_duplicate_title_accumulator(self):
        packet = _clean_packet()
        index = {}
        first = score_fake(packet, title_index=index, now=NOW)
        second = score_fake(packet, title_index=index, now=NOW)
        assert first["signals"]["duplicate_title"]["score"] == 0.0
        assert second["signals"]["duplicate_title"]["score"] == 1.0

    def test_overloaded_stack_fires(self):
        p = _clean_packet(technologies=[
            "python", "java", "go", "rust", "ruby",
            "react", "vue", "angular", "svelte",
            "postgres", "mysql", "redis", "mongodb",
            "aws", "gcp", "azure", "kubernetes",
        ])
        out = score_fake(p, now=NOW)
        assert out["signals"]["overloaded_stack"]["score"] > 0


class TestThresholdBoundary:
    def test_custom_threshold_flips_suspect(self):
        # Use a mildly dirty packet so the score is > 0.
        p = _clean_packet(location="anywhere")
        out = score_fake(p, now=NOW)
        assert out["score"] > 0
        strict = score_fake(p, now=NOW, threshold=out["score"] - 0.001)
        lenient = score_fake(p, now=NOW, threshold=out["score"] + 0.5)
        assert strict["is_suspect"] is True
        assert lenient["is_suspect"] is False
