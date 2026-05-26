import pytest

from core.dimensions import (
    ProfileFitScorer,
    merge_profile_prefs,
    score_dimensions,
)


PROFILE = {
    "seniority": "senior",
    "years_experience": 5,
    "technologies": ["python", "react", "kubernetes"],
    "domains": ["devtools", "platform"],
    "target_roles": ["Senior Product Manager", "Platform PM"],
}


def test_merge_profile_prefs_backfills_from_resume():
    merged = merge_profile_prefs({"years_experience": 0}, PROFILE)
    assert merged["years_experience"] == 5
    assert merged["current_level"] == "senior"


def test_merge_profile_prefs_keeps_explicit_settings():
    merged = merge_profile_prefs(
        {"years_experience": 8, "current_level": "staff"},
        PROFILE,
    )
    assert merged["years_experience"] == 8
    assert merged["current_level"] == "staff"


def test_score_dimensions_group_pm_uses_director_band():
    dims = score_dimensions(PROFILE, {"title": "Group Product Manager, Ads"})
    assert dims["job_seniority"] == "director"
    assert dims["seniority_fit"] == pytest.approx(0.15)
    assert dims["years_fit"] == pytest.approx(0.5)


def test_profile_fit_scorer_penalises_over_leveled_title():
    scorer = ProfileFitScorer(PROFILE)
    score, delta, reason = scorer.adjust(
        0.62, {"title": "Group Product Manager, Monetization"},
    )
    assert delta < -0.15
    assert score < 0.50
    assert "seniority director" in reason


def test_profile_fit_scorer_penalises_data_pm_lane_for_platform_profile():
    scorer = ProfileFitScorer(PROFILE)
    _, delta, reason = scorer.adjust(
        0.62,
        {
            "title": "Data Product Manager, Finance",
            "description": "data warehouse analytics sql pipelines finance reporting",
        },
    )
    assert delta < -0.10
    assert "lane" in reason or "domains" in reason


def test_score_dimensions_platform_pm_aligns_with_profile():
    dims = score_dimensions(
        PROFILE,
        {
            "title": "Senior Product Manager, Platform",
            "description": "kubernetes python devtools developer platform",
            "technologies": ["kubernetes", "python"],
        },
    )
    assert (dims.get("lane_fit") or 0) >= 0.5
    assert (dims.get("domain_fit") or 0) >= 0.2
