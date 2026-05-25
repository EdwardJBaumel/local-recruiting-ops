"""Tests for core/preferences.py — location, salary, experience rules.

Behavioural contracts only. No magic numbers beyond public defaults.
"""
import pytest

from sentinel.core.preferences import (
    ExperienceFilter,
    ExperienceScorer,
    LocationFilter,
    LocationScorer,
    SalaryScorer,
    extract_salary_usd,
)


# ───────── LocationFilter ─────────

class TestLocationFilter:
    def test_inactive_when_no_rules(self):
        f = LocationFilter({"location_mode": "hard"})
        assert f.active is False
        keep, _ = f.evaluate({"location": "London", "remote": "onsite"})
        assert keep is True

    def test_remote_only(self):
        f = LocationFilter({"location_mode": "hard", "work_modes": ["remote"]})
        keep, reason = f.evaluate({"location": "London", "remote": "onsite"})
        assert keep is False and "onsite" in reason
        keep, _ = f.evaluate({"location": "Anywhere", "remote": "remote"})
        assert keep is True

    def test_allowed_locations_gate_hybrid_onsite(self):
        f = LocationFilter({
            "location_mode": "hard",
            "work_modes": ["hybrid", "onsite"],
            "allowed_locations": ["london"],
        })
        keep, _ = f.evaluate({"location": "London, UK", "remote": "hybrid"})
        assert keep is True
        keep, reason = f.evaluate({"location": "Paris", "remote": "onsite"})
        assert keep is False and "allowed" in reason

    def test_blocklist_beats_everything(self):
        f = LocationFilter({
            "location_mode": "hard",
            "blocked_locations": ["berlin"],
        })
        keep, reason = f.evaluate({"location": "Berlin, Germany"})
        assert keep is False and "blocked" in reason

    def test_remote_bypasses_allowed_list(self):
        f = LocationFilter({
            "location_mode": "hard",
            "allowed_locations": ["london"],
        })
        keep, _ = f.evaluate({"location": "Anywhere", "remote": "remote"})
        assert keep is True

    def test_soft_mode_never_drops(self):
        f = LocationFilter({
            "location_mode": "soft",
            "work_modes": ["remote"],
        })
        keep, _ = f.evaluate({"location": "London", "remote": "onsite"})
        assert keep is True
        assert f.active is False

    def test_legacy_remote_only_migrates(self):
        f = LocationFilter({"location_mode": "hard", "remote_only": True, "allow_remote": True})
        assert f.work_modes == {"remote"}


class TestLocationScorer:
    def test_inactive_in_hard_mode(self):
        s = LocationScorer({"location_mode": "hard", "work_modes": ["remote"]})
        assert s.active is False

    def test_applies_penalty_in_soft_mode(self):
        s = LocationScorer({
            "location_mode": "soft",
            "work_modes": ["remote"],
            "location_weight": 0.1,
        })
        new_score, delta, _ = s.adjust(0.7, {"location": "London", "remote": "onsite"})
        assert delta < 0
        assert new_score < 0.7

    def test_no_penalty_when_mode_matches(self):
        s = LocationScorer({
            "location_mode": "soft",
            "work_modes": ["remote"],
            "location_weight": 0.1,
        })
        new_score, delta, _ = s.adjust(0.7, {"location": "Anywhere", "remote": "remote"})
        assert delta == 0
        assert new_score == 0.7


# ───────── Salary ─────────

class TestSalaryParse:
    @pytest.mark.parametrize("raw, expected", [
        ("$160,000", 160_000),
        ("$160K", 160_000),
        ("$1.5M", 1_500_000),
        ("USD 175000", 175_000),
        ("£120k", 120_000 * 1.27),
        ("€140 000", 140_000 * 1.08),
    ])
    def test_single_values(self, raw, expected):
        got = extract_salary_usd(raw)
        assert got == pytest.approx(expected, rel=0.01)

    def test_range_midpoint(self):
        got = extract_salary_usd("$160K-200K")
        assert got == pytest.approx(180_000, rel=0.01)

    def test_missing_returns_none(self):
        assert extract_salary_usd(None) is None
        assert extract_salary_usd("") is None
        assert extract_salary_usd("competitive") is None

    def test_rejects_small_noise(self):
        assert extract_salary_usd("2026") is None
        assert extract_salary_usd("5 years") is None


class TestSalaryScorer:
    def test_inactive_without_floor(self):
        s = SalaryScorer({"salary_weight": 0.15})
        assert s.active is False
        new, delta, _ = s.adjust(0.7, {"salary": "$200k"})
        assert delta == 0 and new == 0.7

    def test_above_floor_bonus(self):
        s = SalaryScorer({"salary_floor_usd": 160_000, "salary_weight": 0.15})
        new, delta, _ = s.adjust(0.5, {"salary": "$220k"})
        assert delta > 0 and new > 0.5

    def test_below_floor_penalty(self):
        s = SalaryScorer({"salary_floor_usd": 160_000, "salary_weight": 0.15})
        new, delta, _ = s.adjust(0.7, {"salary": "$90k"})
        assert delta < 0 and new < 0.7

    def test_missing_salary_mild_penalty(self):
        s = SalaryScorer({"salary_floor_usd": 160_000, "salary_weight": 0.15})
        new, delta, reason = s.adjust(0.7, {"salary": None})
        assert delta < 0
        assert "missing" in reason
        # mild: shouldn't tank the score by the full weight
        assert abs(delta) < 0.15


# ───────── Experience ─────────

class TestExperienceFilter:
    def test_inactive_when_unset(self):
        f = ExperienceFilter({})
        assert f.active is False
        keep, _ = f.evaluate({"title": "Director of Product", "description": "10+ years"})
        assert keep is True

    def test_director_trapdoor(self):
        f = ExperienceFilter({"years_experience": 5, "current_level": "senior"})
        keep, reason = f.evaluate({"title": "Director of Product", "description": ""})
        assert keep is False and "director" in reason.lower()

    def test_years_gap_drop(self):
        f = ExperienceFilter({"years_experience": 2, "current_level": "mid"})
        keep, reason = f.evaluate({
            "title": "Senior Engineer",
            "description": "We need 12+ years of experience.",
        })
        assert keep is False and "years" in reason.lower()

    def test_level_gap_drop(self):
        f = ExperienceFilter({"years_experience": 8, "current_level": "junior"})
        keep, reason = f.evaluate({"title": "Staff Engineer", "description": ""})
        assert keep is False and "bands" in reason.lower()

    def test_within_gap_passes(self):
        f = ExperienceFilter({"years_experience": 5, "current_level": "senior"})
        keep, _ = f.evaluate({
            "title": "Senior Product Manager",
            "description": "We want 5+ years PM experience.",
        })
        assert keep is True

    def test_trapdoor_disableable(self):
        # With trapdoor off AND a level close enough not to trip the gap
        # rule, a Director role with <10 years of experience should pass.
        f = ExperienceFilter({
            "years_experience": 5,
            "current_level": "staff",
            "trapdoor_enabled": False,
            "max_level_gap": 3,
        })
        keep, reason = f.evaluate({"title": "Director of Product", "description": ""})
        assert keep is True, f"expected keep, got reason={reason!r}"


class TestExperienceScorer:
    def test_inactive_without_years(self):
        s = ExperienceScorer({})
        assert s.active is False
        new, delta, _ = s.adjust(0.7, {"description": "10+ years"})
        assert delta == 0 and new == 0.7

    def test_no_penalty_below_soft_start(self):
        s = ExperienceScorer({"years_experience": 5, "years_weight": 0.04})
        # gap 2 (needs 7, has 5) is under soft_start=3
        new, delta, _ = s.adjust(0.7, {"description": "7+ years required."})
        assert delta == 0

    def test_gap_scales_with_weight(self):
        s = ExperienceScorer({"years_experience": 2, "years_weight": 0.04})
        # gap 5 → 3 steps × 0.04 = 0.12 off
        new, delta, _ = s.adjust(0.8, {"description": "7+ years required."})
        assert delta == pytest.approx(-0.12, abs=0.001)
        assert new == pytest.approx(0.68, abs=0.001)

    def test_no_bonus_for_overshoot(self):
        s = ExperienceScorer({"years_experience": 15, "years_weight": 0.04})
        new, delta, _ = s.adjust(0.7, {"description": "3+ years required."})
        assert delta == 0
