"""
Unit tests for core.preferences scorers.

Pure Python — no Ollama, no embedder. Covers TitleScorer (boost +
hard-penalty short-circuit + word-boundary matching), SalaryScorer,
ExperienceScorer, extract_salary_usd, and the module-level
title_has_blocked_keyword helper.
"""
import pytest

from core.preferences import (
    TitleScorer,
    SeniorityScorer,
    SalaryScorer,
    ExperienceScorer,
    extract_salary_usd,
    title_has_blocked_keyword,
    _infer_user_level,
)


# ─────────────────────────────────────────────────────────────────
# title_has_blocked_keyword — whole-word, case-insensitive
# ─────────────────────────────────────────────────────────────────
def test_blocked_keyword_whole_word_hit():
    assert title_has_blocked_keyword("Software Engineer", ["engineer"]) == "engineer"


def test_blocked_keyword_does_not_match_substring():
    # "engineer" must NOT fire on "Engineering".
    assert title_has_blocked_keyword("Engineering Manager", ["engineer"]) is None


def test_blocked_keyword_case_insensitive():
    assert title_has_blocked_keyword("SENIOR DESIGNER", ["designer"]) == "designer"


def test_blocked_keyword_returns_first_hit():
    hit = title_has_blocked_keyword("Engineer and Designer", ["designer", "engineer"])
    assert hit in ("designer", "engineer")


def test_blocked_keyword_none_when_no_match():
    assert title_has_blocked_keyword("Product Manager", ["engineer", "designer"]) is None


def test_blocked_keyword_empty_inputs():
    assert title_has_blocked_keyword("", ["engineer"]) is None
    assert title_has_blocked_keyword("Product Manager", []) is None
    assert title_has_blocked_keyword("Product Manager", None) is None


# ─────────────────────────────────────────────────────────────────
# TitleScorer
# ─────────────────────────────────────────────────────────────────
def test_title_scorer_inactive_with_empty_config():
    scorer = TitleScorer({})
    assert scorer.active is False
    score, delta, reason = scorer.adjust(0.5, {"title": "Product Manager"})
    assert (score, delta, reason) == (0.5, 0.0, "")


def test_title_scorer_role_keyword_boost():
    scorer = TitleScorer({"role_keywords": ["product manager"], "title_weight": 0.2})
    score, delta, reason = scorer.adjust(0.5, {"title": "Senior Product Manager"})
    assert delta == 0.2
    assert score == pytest.approx(0.7)
    assert "product manager" in reason


def test_title_scorer_boost_is_flat_not_scaled():
    # Short exact title gets the full bump too.
    scorer = TitleScorer({"role_keywords": ["tpm"], "title_weight": 0.2})
    _, delta, _ = scorer.adjust(0.5, {"title": "TPM, Cloud"})
    assert delta == 0.2


def test_title_scorer_word_boundary_engineer_matches_engineer():
    scorer = TitleScorer({"role_keywords": ["engineer"], "title_weight": 0.2})
    _, delta, _ = scorer.adjust(0.5, {"title": "Software Engineer"})
    assert delta == 0.2


def test_title_scorer_word_boundary_engineer_not_engineering():
    # "engineer" keyword must NOT boost "Engineering Manager".
    scorer = TitleScorer({"role_keywords": ["engineer"], "title_weight": 0.2})
    _, delta, _ = scorer.adjust(0.5, {"title": "Engineering Manager"})
    assert delta == 0.0


def test_title_scorer_blocked_keyword_hard_penalty():
    scorer = TitleScorer({
        "blocked_title_keywords": ["engineer"],
        "title_penalty": 0.6,
    })
    score, delta, reason = scorer.adjust(0.8, {"title": "Software Engineer"})
    assert delta == -0.6
    assert score == pytest.approx(0.2)
    assert "blocked keyword" in reason


def test_title_scorer_blocked_short_circuits_before_boost():
    # A title that hits BOTH a role keyword and a blocked keyword takes
    # the penalty and never earns the boost.
    scorer = TitleScorer({
        "role_keywords": ["product"],
        "blocked_title_keywords": ["engineer"],
        "title_weight": 0.2,
        "title_penalty": 0.6,
    })
    score, delta, _ = scorer.adjust(0.8, {"title": "Product Engineer"})
    assert delta == -0.6  # penalty, NOT +0.2 boost
    assert score == pytest.approx(0.2)


def test_title_scorer_blocked_word_boundary():
    # Blocked "engineer" must not fire on "Engineering".
    scorer = TitleScorer({
        "blocked_title_keywords": ["engineer"],
        "title_penalty": 0.6,
    })
    _, delta, _ = scorer.adjust(0.8, {"title": "Engineering Program Manager"})
    assert delta == 0.0


def test_title_scorer_clamps_to_unit_interval():
    # Penalty bigger than the base score clamps at 0.0, not negative.
    scorer = TitleScorer({
        "blocked_title_keywords": ["engineer"],
        "title_penalty": 0.9,
    })
    score, _, _ = scorer.adjust(0.3, {"title": "Software Engineer"})
    assert score == 0.0


def test_title_scorer_empty_title_no_change():
    scorer = TitleScorer({"role_keywords": ["product manager"], "title_weight": 0.2})
    score, delta, reason = scorer.adjust(0.5, {"title": ""})
    assert (score, delta, reason) == (0.5, 0.0, "")


def test_title_scorer_no_keyword_match_no_change():
    scorer = TitleScorer({"role_keywords": ["product manager"], "title_weight": 0.2})
    score, delta, _ = scorer.adjust(0.5, {"title": "Data Scientist"})
    assert (score, delta) == (0.5, 0.0)


def test_title_scorer_skips_boost_when_title_above_user_level():
    scorer = TitleScorer({
        "role_keywords": ["product manager"],
        "title_weight": 0.2,
        "years_experience": 6,
        "current_level": "senior",
    })
    score, delta, reason = scorer.adjust(0.5, {"title": "Staff Product Manager"})
    assert (score, delta) == (0.5, 0.0)
    assert reason == ""


def test_title_scorer_still_boosts_at_user_level():
    scorer = TitleScorer({
        "role_keywords": ["product manager"],
        "title_weight": 0.2,
        "years_experience": 6,
        "current_level": "senior",
    })
    score, delta, _ = scorer.adjust(0.5, {"title": "Senior Product Manager"})
    assert delta == pytest.approx(0.2)
    assert score == pytest.approx(0.7)


def test_infer_user_level_from_years_when_level_unset():
    assert _infer_user_level({"years_experience": 5}) == "senior"
    assert _infer_user_level({"years_experience": 9}) == "staff"
    assert _infer_user_level({"years_experience": 2}) == "mid"


def test_seniority_scorer_penalises_staff_above_senior_user():
    scorer = SeniorityScorer({"years_experience": 6, "current_level": "senior", "level_weight": 0.16})
    score, delta, reason = scorer.adjust(0.52, {"title": "Staff Product Manager, Platform"})
    assert delta == pytest.approx(-0.16)
    assert score == pytest.approx(0.36)
    assert "1 band" in reason


def test_seniority_scorer_inactive_without_profile():
    scorer = SeniorityScorer({})
    assert scorer.active is False
    score, delta, reason = scorer.adjust(0.5, {"title": "Staff Product Manager"})
    assert (score, delta, reason) == (0.5, 0.0, "")


def test_infer_job_level_title_staff_beats_tagged_senior():
    from core.preferences import _infer_job_level
    level = _infer_job_level({
        "title": "Staff Product Manager, Growth",
        "seniority": "senior",
    })
    assert level == "staff"


def test_infer_job_level_group_product_manager_is_director():
    from core.preferences import _infer_job_level
    assert _infer_job_level({"title": "Group Product Manager, Ads"}) == "director"


def test_title_scorer_boosts_role_keyword_without_profile_path():
    scorer = TitleScorer({"role_keywords": ["product manager"], "title_weight": 0.2})
    score, delta, reason = scorer.adjust(0.55, {"title": "Senior Product Manager, Billing"})
    assert delta == pytest.approx(0.2)
    assert "product manager" in reason

def test_seniority_scorer_group_pm_vs_senior_user():
    scorer = SeniorityScorer({"years_experience": 5, "level_weight": 0.16})
    score, delta, reason = scorer.adjust(
        0.58, {"title": "Group Product Manager, Monetization"},
    )
    assert delta == pytest.approx(-0.48)
    assert "3 band" in reason


def test_experience_filter_drops_group_pm_for_five_years():
    from core.preferences import ExperienceFilter
    filt = ExperienceFilter({"years_experience": 5, "trapdoor_enabled": True})
    keep, reason = filt.evaluate({"title": "Group Product Manager, Growth"})
    assert keep is False
    assert "director" in reason or "10" in reason


# ─────────────────────────────────────────────────────────────────
# SalaryScorer
# ─────────────────────────────────────────────────────────────────
def test_salary_scorer_inactive_without_floor():
    scorer = SalaryScorer({})
    assert scorer.active is False
    score, delta, reason = scorer.adjust(0.5, {"salary": "$200,000"})
    assert (score, delta, reason) == (0.5, 0.0, "")


def test_salary_scorer_above_floor_positive_delta():
    scorer = SalaryScorer({"salary_floor_usd": 100000, "salary_weight": 0.15})
    score, delta, reason = scorer.adjust(0.5, {"salary": "$200,000"})
    assert delta > 0
    assert score > 0.5
    assert "floor" in reason


def test_salary_scorer_below_floor_full_negative_weight():
    scorer = SalaryScorer({"salary_floor_usd": 200000, "salary_weight": 0.15})
    score, delta, reason = scorer.adjust(0.5, {"salary": "$120,000"})
    assert delta == pytest.approx(-0.15)
    assert score == pytest.approx(0.35)
    assert "< floor" in reason


def test_salary_scorer_missing_salary_mild_penalty():
    scorer = SalaryScorer({"salary_floor_usd": 150000, "salary_weight": 0.15})
    score, delta, reason = scorer.adjust(0.5, {})
    # missing penalty is 0.3 * weight.
    assert delta == pytest.approx(-0.15 * 0.3)
    assert reason == "salary missing"


def test_salary_scorer_clamps_result():
    scorer = SalaryScorer({"salary_floor_usd": 200000, "salary_weight": 0.5})
    # Base 0.1 minus 0.5 below-floor penalty would go negative; clamps to 0.
    score, _, _ = scorer.adjust(0.1, {"salary": "$80,000"})
    assert score == 0.0


# ─────────────────────────────────────────────────────────────────
# ExperienceScorer
# ─────────────────────────────────────────────────────────────────
def test_experience_scorer_inactive_with_zero_years():
    scorer = ExperienceScorer({})
    assert scorer.active is False
    score, delta, reason = scorer.adjust(0.5, {"description": "10+ years required"})
    assert (score, delta, reason) == (0.5, 0.0, "")


def test_experience_scorer_no_penalty_below_soft_start():
    # User has 5y, role wants 7y → gap 2 < soft_start 3 → no penalty.
    scorer = ExperienceScorer({"years_experience": 5, "years_weight": 0.04})
    score, delta, _ = scorer.adjust(
        0.6, {"description": "Requires 7 years of product management experience."}
    )
    assert (score, delta) == (0.6, 0.0)


def test_experience_scorer_penalty_at_gap_three():
    # User 5y, role wants 8y → gap 3 → one step → -0.04.
    scorer = ExperienceScorer({"years_experience": 5, "years_weight": 0.04})
    score, delta, reason = scorer.adjust(
        0.6, {"description": "Requires 8 years of product management experience."}
    )
    assert delta == pytest.approx(-0.04)
    assert score == pytest.approx(0.56)
    assert "gap 3" in reason


def test_experience_scorer_penalty_scales_with_gap():
    # Larger gap → larger penalty, but the two are monotonic.
    scorer = ExperienceScorer({"years_experience": 3, "years_weight": 0.04})
    _, small_gap_delta, _ = scorer.adjust(
        0.6, {"description": "Requires 7 years of product experience."}
    )
    _, big_gap_delta, _ = scorer.adjust(
        0.6, {"description": "Requires 10 years of product experience."}
    )
    assert big_gap_delta < small_gap_delta < 0


def test_experience_scorer_no_requirement_no_change():
    scorer = ExperienceScorer({"years_experience": 5, "years_weight": 0.04})
    score, delta, _ = scorer.adjust(0.6, {"description": "A great role."})
    assert (score, delta) == (0.6, 0.0)


def test_experience_scorer_overshoot_no_bonus():
    # User has 15y, role wants 5y → no positive delta (no over-reward).
    scorer = ExperienceScorer({"years_experience": 15, "years_weight": 0.04})
    score, delta, _ = scorer.adjust(
        0.6, {"description": "Requires 5 years of experience."}
    )
    assert delta == 0.0


# ─────────────────────────────────────────────────────────────────
# extract_salary_usd
# ─────────────────────────────────────────────────────────────────
def test_extract_salary_usd_single_value():
    assert extract_salary_usd("$180,000") == pytest.approx(180000.0)


def test_extract_salary_usd_k_suffix_range_midpoint():
    # "$160K-200K" → midpoint of 160k and 200k = 180k.
    assert extract_salary_usd("$160K-200K") == pytest.approx(180000.0)


def test_extract_salary_usd_m_suffix():
    assert extract_salary_usd("$1.5M") == pytest.approx(1_500_000.0)


def test_extract_salary_usd_gbp_conversion():
    # £120k * 1.27 GBP→USD rate.
    out = extract_salary_usd("£120k")
    assert out == pytest.approx(120000 * 1.27)


def test_extract_salary_usd_thin_space_number():
    # "€140 000" — thin space inside the number is collapsed.
    out = extract_salary_usd("€140 000")
    assert out == pytest.approx(140000 * 1.08)


def test_extract_salary_usd_rejects_small_numbers():
    # Year-like / count-like values under 10k are noise → None.
    assert extract_salary_usd("2026") is None
    assert extract_salary_usd("5 years") is None


def test_extract_salary_usd_none_and_empty():
    assert extract_salary_usd(None) is None
    assert extract_salary_usd("") is None
    assert extract_salary_usd("competitive") is None
