"""Tests for the near-dupe fallback in MatchRegistry.upsert_matches."""
from sentinel.core.match_registry import (
    MatchRegistry,
    _jaccard,
    _title_tokens,
)


def _m(title, company="Acme", location="San Francisco, CA", score=0.8, **extra):
    return {
        "title": title,
        "company": company,
        "location": location,
        "url": f"https://example.com/{title}",
        "_match_score": score,
        **extra,
    }


class TestTokenHelpers:
    def test_stopwords_dropped_seniority_kept(self):
        toks = _title_tokens("Senior Product Manager, Ads Platform")
        assert "senior" in toks
        assert "platform" in toks
        assert "the" not in toks and "and" not in toks

    def test_jaccard_identical_is_one(self):
        a = _title_tokens("Senior PM Ads")
        assert _jaccard(a, a) == 1.0

    def test_jaccard_empty_is_zero(self):
        assert _jaccard(frozenset(), _title_tokens("anything")) == 0.0


class TestNearDupeUpsert:
    def test_exact_key_still_dominates(self, tmp_path):
        reg = MatchRegistry(tmp_path)
        reg.upsert_matches([_m("Senior Product Manager")], cycle=1)
        reg.upsert_matches([_m("Senior Product Manager")], cycle=2)
        assert reg.stats()["total"] == 1

    def test_near_dupe_merges(self, tmp_path):
        reg = MatchRegistry(tmp_path)
        reg.upsert_matches([_m("Senior Product Manager, Ads")], cycle=1)
        reg.upsert_matches([_m("Senior Product Manager, Ads Platform")], cycle=2)
        # Same company + location + ~0.75 token overlap → same entry.
        assert reg.stats()["total"] == 1

    def test_distinct_titles_stay_distinct(self, tmp_path):
        reg = MatchRegistry(tmp_path)
        reg.upsert_matches([_m("Senior Product Manager, Ads")], cycle=1)
        reg.upsert_matches([_m("Senior Product Manager, Payments")], cycle=2)
        # "ads" vs "payments" — only senior/product/manager overlap (3/5) = 0.6.
        assert reg.stats()["total"] == 2

    def test_different_company_stays_distinct(self, tmp_path):
        reg = MatchRegistry(tmp_path)
        reg.upsert_matches([_m("Senior PM", company="Acme")], cycle=1)
        reg.upsert_matches([_m("Senior PM", company="Globex")], cycle=2)
        assert reg.stats()["total"] == 2

    def test_different_location_stays_distinct(self, tmp_path):
        reg = MatchRegistry(tmp_path)
        reg.upsert_matches([_m("Senior PM", location="San Francisco, CA")], cycle=1)
        reg.upsert_matches([_m("Senior PM", location="New York, NY")], cycle=2)
        assert reg.stats()["total"] == 2

    def test_higher_score_wins_on_merge(self, tmp_path):
        reg = MatchRegistry(tmp_path)
        reg.upsert_matches([_m("Senior PM, Ads", score=0.6)], cycle=1)
        reg.upsert_matches([_m("Senior PM, Ads Platform", score=0.85)], cycle=2)
        entry = reg.all_entries()[0]
        assert entry["score"] == 0.85
