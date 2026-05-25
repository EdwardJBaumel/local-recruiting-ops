"""
Unit tests for core.fake_detector.

Pure deterministic scoring — 9 weighted signals, no LLM. Covers the
per-signal scorers, the composer (score_fake), the per-signal floor
caps, the missing-posted-date soft cap, and resolve_threshold.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core import fake_detector as fd


# A fixed "now" so age-based tests are deterministic.
NOW = datetime(2026, 5, 14, tzinfo=timezone.utc)


def _days_ago(n: int) -> str:
    return (NOW - timedelta(days=n)).date().isoformat()


# ─────────────────────────────────────────────────────────────────
# (a) age curve — _score_age
# ─────────────────────────────────────────────────────────────────
def test_age_no_date_returns_none():
    assert fd._score_age({}, now=NOW) is None


def test_age_fresh_posting_scores_zero():
    # Within the 14-day floor → 0.0.
    score, reason = fd._score_age({"posted_at": _days_ago(5)}, now=NOW)
    assert score == 0.0


def test_age_curve_is_monotonic_increasing():
    # Older postings score strictly higher up the exponential curve.
    s30 = fd._score_age({"posted_at": _days_ago(30)}, now=NOW)[0]
    s60 = fd._score_age({"posted_at": _days_ago(60)}, now=NOW)[0]
    s90 = fd._score_age({"posted_at": _days_ago(90)}, now=NOW)[0]
    assert 0.0 < s30 < s60 < s90 <= 1.0


def test_age_curve_anchor_values():
    # Docstring anchors: 30d ~0.41, 60d ~0.79, 90d ~0.93. Allow slack.
    s30 = fd._score_age({"posted_at": _days_ago(30)}, now=NOW)[0]
    s60 = fd._score_age({"posted_at": _days_ago(60)}, now=NOW)[0]
    assert abs(s30 - 0.41) < 0.05
    assert abs(s60 - 0.79) < 0.05


def test_age_stale_flag_in_reason():
    _, reason = fd._score_age({"posted_at": _days_ago(90)}, now=NOW)
    assert "stale" in reason


def test_age_accepts_epoch_seconds():
    epoch = (NOW - timedelta(days=60)).timestamp()
    score, _ = fd._score_age({"posted_at": epoch}, now=NOW)
    assert score > 0.5


# ─────────────────────────────────────────────────────────────────
# (b) salary-band-width — _score_salary_band
# ─────────────────────────────────────────────────────────────────
def test_salary_band_absent_returns_none():
    assert fd._score_salary_band({}) is None


def test_salary_band_narrow_scores_zero():
    # 2x ratio is below the 3.5 soft threshold.
    score, _ = fd._score_salary_band({"salary_min": 100000, "salary_max": 200000})
    assert score == 0.0


def test_salary_band_very_wide_saturates():
    # 8x ratio is above the 6.0 hard threshold → 1.0.
    score, _ = fd._score_salary_band({"salary_min": 50000, "salary_max": 400000})
    assert score == 1.0


def test_salary_band_mid_range_scaled():
    # 4.75x sits between soft (3.5) and hard (6.0) → partial score.
    score, _ = fd._score_salary_band({"salary_min": 40000, "salary_max": 190000})
    assert 0.0 < score < 1.0


def test_salary_band_inverted_returns_none():
    # hi < lo is invalid data.
    assert fd._score_salary_band({"salary_min": 200000, "salary_max": 100000}) is None


# ─────────────────────────────────────────────────────────────────
# (c) vague-location tiers — _score_location_vague
# ─────────────────────────────────────────────────────────────────
def test_location_absent_returns_none():
    assert fd._score_location_vague({}) is None


def test_location_empty_string_scores_high():
    score, _ = fd._score_location_vague({"location": ""})
    assert score == 1.0


def test_location_severe_vague():
    # "Remote" / "Worldwide" with no geography → 0.7 severe tier.
    assert fd._score_location_vague({"location": "Worldwide"})[0] == 0.7
    assert fd._score_location_vague({"location": "Anywhere"})[0] == 0.7


def test_location_country_only_light_tier():
    # Country-only → 0.3 light tier.
    score, _ = fd._score_location_vague({"location": "United States"})
    assert score == 0.3


def test_location_country_only_with_remote_flag_is_benign():
    # remote=True downgrades country-only to 0.0.
    score, _ = fd._score_location_vague(
        {"location": "United States", "remote": True}
    )
    assert score == 0.0


def test_location_specific_city_scores_zero():
    score, _ = fd._score_location_vague({"location": "San Francisco, CA"})
    assert score == 0.0


# ─────────────────────────────────────────────────────────────────
# (f) missing fields — _score_missing_fields
# This is the headline behaviour: technologies must NOT be penalised.
# ─────────────────────────────────────────────────────────────────
def test_missing_fields_all_present_scores_zero():
    payload = {
        "title": "Senior Product Manager",
        "company": "Acme Corp",
        "description": "x" * 250,
    }
    score, reason = fd._score_missing_fields(payload)
    assert score == 0.0
    assert reason == "fields present"


def test_missing_technologies_is_NOT_penalised():
    # A job with a full description/company/title but NO technologies
    # field must not be dinged — JSON ATS APIs never ship technologies.
    payload = {
        "title": "Senior Product Manager",
        "company": "Acme Corp",
        "description": "x" * 250,
        # technologies deliberately absent
    }
    score, reason = fd._score_missing_fields(payload)
    assert score == 0.0
    assert "technolog" not in reason.lower()


def test_missing_technologies_empty_list_also_not_penalised():
    payload = {
        "title": "Senior Product Manager",
        "company": "Acme Corp",
        "description": "x" * 250,
        "technologies": [],
    }
    assert fd._score_missing_fields(payload)[0] == 0.0


def test_thin_description_is_flagged():
    payload = {
        "title": "Senior Product Manager",
        "company": "Acme Corp",
        "description": "Too short.",  # < 200 chars
    }
    score, reason = fd._score_missing_fields(payload)
    assert score > 0.0
    assert "description" in reason


def test_missing_company_is_flagged():
    payload = {
        "title": "Senior Product Manager",
        "company": "",
        "description": "x" * 250,
    }
    score, reason = fd._score_missing_fields(payload)
    assert score > 0.0
    assert "company" in reason


def test_missing_title_is_flagged():
    payload = {
        "title": "",
        "company": "Acme Corp",
        "description": "x" * 250,
    }
    score, reason = fd._score_missing_fields(payload)
    assert score > 0.0
    assert "title" in reason


def test_missing_fields_score_caps_at_one():
    # All three issues fire: 0.6 + 0.4 + 0.6 = 1.6, capped to 1.0.
    payload = {"title": "", "company": "", "description": ""}
    score, _ = fd._score_missing_fields(payload)
    assert score == 1.0


# ─────────────────────────────────────────────────────────────────
# (e) buzzword density — _score_buzzword_density
# ─────────────────────────────────────────────────────────────────
def test_buzzword_short_text_returns_none():
    # Under 80 words can't be scored meaningfully.
    assert fd._score_buzzword_density({"description": "short text", "title": "PM"}) is None


def test_buzzword_clean_long_text_scores_zero():
    desc = "responsibility " * 100  # 100 content words, no buzzwords
    score, reason = fd._score_buzzword_density({"description": desc, "title": "PM"})
    assert score == 0.0
    assert reason == "no buzzwords"


def test_buzzword_dense_text_scores_high():
    # Pad to >80 words, then sprinkle distinct buzzword phrases.
    filler = "team delivery roadmap planning execution " * 16  # ~80 words
    buzz = ("rockstar ninja guru wizard superstar unicorn")
    desc = filler + " " + buzz
    score, _ = fd._score_buzzword_density({"description": desc, "title": "PM"})
    assert score > 0.5


# ─────────────────────────────────────────────────────────────────
# (h) apply URL — _score_apply_url
# ─────────────────────────────────────────────────────────────────
def test_apply_url_missing_scores_high():
    score, _ = fd._score_apply_url({})
    assert score == 0.8


def test_apply_url_present_scores_zero():
    score, _ = fd._score_apply_url({"url": "https://jobs.example.com/123"})
    assert score == 0.0


# ─────────────────────────────────────────────────────────────────
# score_fake — composer
# ─────────────────────────────────────────────────────────────────
def test_score_fake_empty_payload():
    out = fd.score_fake({})
    assert out["score"] == 0.0
    assert out["is_suspect"] is False
    assert out["signals"] == {}


def test_score_fake_clean_recent_job_scores_low():
    payload = {
        "title": "Senior Product Manager",
        "company": "Acme Corp",
        "description": "x" * 400,
        "location": "San Francisco, CA",
        "url": "https://jobs.acme.com/123",
        "posted_at": _days_ago(3),
        "salary_min": 150000,
        "salary_max": 200000,
    }
    out = fd.score_fake(payload, now=NOW)
    assert out["score"] < fd.GHOST_SUSPECT_THRESHOLD
    assert out["is_suspect"] is False


def test_score_fake_stale_thin_job_scores_high():
    payload = {
        "title": "Product Manager",
        "company": "",  # missing company
        "description": "Short.",  # thin description
        "location": "Worldwide",  # vague
        # no apply url, posted long ago
        "posted_at": _days_ago(200),
    }
    out = fd.score_fake(payload, now=NOW)
    assert out["score"] >= fd.GHOST_SUSPECT_THRESHOLD
    assert out["is_suspect"] is True


def test_score_fake_missing_posted_date_caps_at_029():
    # No posted date at all → final score capped <= 0.29 even when
    # several other signals fire hard.
    payload = {
        "title": "",            # missing title
        "company": "",          # missing company
        "description": "x",     # thin
        "location": "",         # no location
        # NO posted_at / posted_date — the cap trigger
    }
    out = fd.score_fake(payload, now=NOW)
    assert out["score"] <= 0.29
    # The raw (pre-cap) score should be meaningfully higher, proving
    # the cap is what's holding the displayed score down.
    assert out["score_raw"] > 0.29


def test_score_fake_floor_cap_buzzword_cannot_suspect_alone():
    # A fresh, well-described job whose ONLY issue is heavy buzzwords
    # must not cross the suspect line — buzzword_density floor cap 0.28.
    filler = "team delivery roadmap planning execution shipping scope " * 14
    buzz = "rockstar ninja guru wizard superstar unicorn disruptive synergy"
    payload = {
        "title": "Senior Product Manager",
        "company": "Acme Corp",
        "description": filler + " " + buzz,
        "location": "San Francisco, CA",
        "url": "https://jobs.acme.com/123",
        "posted_at": _days_ago(2),
    }
    out = fd.score_fake(payload, now=NOW)
    assert out["signals"]["buzzword_density"]["score"] > 0.5
    assert out["is_suspect"] is False


def test_score_fake_duplicate_title_signal_fires_on_second_sighting():
    title_index: dict = {}
    payload = {
        "title": "Product Manager",
        "company": "Acme Corp",
        "location": "Remote",
        "description": "x" * 400,
        "url": "https://jobs.acme.com/1",
        "posted_at": _days_ago(3),
    }
    first = fd.score_fake(dict(payload), title_index=title_index, now=NOW)
    second = fd.score_fake(dict(payload), title_index=title_index, now=NOW)
    assert first["signals"]["duplicate_title"]["score"] == 0.0
    assert second["signals"]["duplicate_title"]["score"] == 1.0


def test_score_fake_respects_explicit_threshold():
    payload = {
        "title": "Senior Product Manager",
        "company": "Acme Corp",
        "description": "x" * 400,
        "location": "San Francisco, CA",
        "url": "https://jobs.acme.com/123",
        "posted_at": _days_ago(3),
    }
    # With a near-zero threshold even a clean job is "suspect".
    out = fd.score_fake(payload, now=NOW, threshold=0.0)
    assert out["threshold"] == 0.0
    assert out["is_suspect"] is True


# ─────────────────────────────────────────────────────────────────
# resolve_threshold
# ─────────────────────────────────────────────────────────────────
def test_resolve_threshold_preset_names():
    assert fd.resolve_threshold("low") == fd.AGGRESSIVENESS_PRESETS["low"]
    assert fd.resolve_threshold("balanced") == fd.AGGRESSIVENESS_PRESETS["balanced"]
    assert fd.resolve_threshold("strict") == fd.AGGRESSIVENESS_PRESETS["strict"]


def test_resolve_threshold_case_insensitive():
    assert fd.resolve_threshold("  STRICT ") == fd.AGGRESSIVENESS_PRESETS["strict"]


def test_resolve_threshold_numeric_passthrough():
    assert fd.resolve_threshold(0.5) == 0.5
    assert fd.resolve_threshold(0) == 0.0
    assert fd.resolve_threshold(1) == 1.0


def test_resolve_threshold_out_of_range_falls_back():
    # Numeric outside [0,1] is rejected → module default.
    assert fd.resolve_threshold(1.5) == fd.GHOST_SUSPECT_THRESHOLD
    assert fd.resolve_threshold(-0.2) == fd.GHOST_SUSPECT_THRESHOLD


def test_resolve_threshold_unknown_string_falls_back():
    assert fd.resolve_threshold("nonsense") == fd.GHOST_SUSPECT_THRESHOLD


def test_resolve_threshold_none_falls_back():
    assert fd.resolve_threshold(None) == fd.GHOST_SUSPECT_THRESHOLD
