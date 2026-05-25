"""Tests for core/dimensions.py — transparent sub-scores.

Missing profile data must return None per-dimension, not silent zero.
"""
from sentinel.core.dimensions import score_dimensions


PROFILE = {
    "seniority": "senior",
    "years_experience": 5,
    "technologies": ["Python", "Postgres", "C++", ".NET"],
    "domains": ["fintech", "devtools"],
}


def _job(**over):
    base = {
        "title": "Senior Software Engineer",
        "description": "We use Python and Postgres. 5+ years required.",
        "technologies": ["Python", "Postgres"],
        "seniority": "senior",
    }
    base.update(over)
    return base


class TestSeniority:
    def test_exact_match(self):
        out = score_dimensions(PROFILE, _job())
        assert out["seniority_fit"] == 1.0

    def test_one_band_off(self):
        out = score_dimensions(PROFILE, _job(title="Staff Engineer", seniority="staff"))
        assert out["seniority_fit"] == 0.75

    def test_two_bands_off(self):
        out = score_dimensions(PROFILE, _job(title="Principal Engineer", seniority="principal"))
        assert out["seniority_fit"] == 0.40

    def test_unknown_returns_none_not_zero(self):
        p = {**PROFILE, "seniority": ""}
        out = score_dimensions(p, _job())
        assert out["seniority_fit"] is None


class TestTechOverlap:
    def test_full_overlap(self):
        out = score_dimensions(PROFILE, _job(
            technologies=["Python", "Postgres", "C++", ".NET"],
            description="We use everything.",
        ))
        assert out["tech_fit"] == 1.0

    def test_partial_overlap(self):
        out = score_dimensions(PROFILE, _job(
            technologies=["Python"],
            description="Python role.",
        ))
        assert 0 < out["tech_fit"] < 1.0

    def test_description_counts(self):
        out = score_dimensions(PROFILE, _job(
            technologies=[],
            description="We love Python, Postgres, C++, and .NET here.",
        ))
        assert out["tech_fit"] == 1.0

    def test_punctuation_safe_boundaries(self):
        # "cpp" must not match "c++" (different token) and vice versa.
        p = {**PROFILE, "technologies": ["C++"]}
        out = score_dimensions(p, _job(
            technologies=[],
            description="This role uses cpp loosely.",
        ))
        assert out["tech_fit"] == 0.0

    def test_empty_profile_techs_returns_none(self):
        p = {**PROFILE, "technologies": []}
        out = score_dimensions(p, _job())
        assert out["tech_fit"] is None


class TestDomain:
    def test_domain_mentioned(self):
        out = score_dimensions(PROFILE, _job(
            description="We build fintech tools for devtools teams.",
        ))
        assert out["domain_fit"] == 1.0

    def test_partial(self):
        out = score_dimensions(PROFILE, _job(description="Fintech product."))
        assert out["domain_fit"] == 0.5

    def test_none_when_no_domains(self):
        p = {**PROFILE, "domains": []}
        out = score_dimensions(p, _job())
        assert out["domain_fit"] is None


class TestYears:
    def test_meets_floor(self):
        out = score_dimensions(PROFILE, _job())  # senior floor=5, profile 5
        assert out["years_fit"] == 1.0

    def test_overshoot_capped_at_one(self):
        p = {**PROFILE, "years_experience": 20}
        out = score_dimensions(p, _job())
        assert out["years_fit"] == 1.0

    def test_undershoot_scales(self):
        p = {**PROFILE, "years_experience": 2}
        out = score_dimensions(p, _job())  # floor 5, 2/5 = 0.4
        assert out["years_fit"] == 0.4

    def test_junior_role_zero_floor(self):
        p = {**PROFILE, "years_experience": 0}
        out = score_dimensions(p, _job(title="Junior Engineer", seniority="junior"))
        assert out["years_fit"] == 1.0


class TestRequirementsFit:
    def test_blend_present(self):
        out = score_dimensions(PROFILE, _job())
        assert 0.0 <= out["requirements_fit"] <= 1.0

    def test_none_when_all_components_missing(self):
        # Profile with nothing to match on.
        p = {"seniority": "", "years_experience": 0, "technologies": [], "domains": []}
        out = score_dimensions(p, _job())
        assert out["requirements_fit"] is None


def test_empty_profile_returns_empty_dict():
    assert score_dimensions({}, _job()) == {}
    assert score_dimensions(None, _job()) == {}
