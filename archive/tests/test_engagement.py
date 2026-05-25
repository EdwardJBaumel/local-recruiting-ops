"""Tests for the engagement / re-engagement helpers.

All pure functions so no network or filesystem is touched. We pin `now`
explicitly everywhere - the module accepts an optional clock argument
specifically so these tests don't drift with wall time.
"""
from datetime import datetime, timedelta, timezone

import pytest

from sentinel.core import engagement as eng


UTC = timezone.utc


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ─── classify_tier ────────────────────────────────────────────────
class TestClassifyTier:
    def test_none_last_is_unknown(self):
        assert eng.classify_tier(None, None) == "unknown"

    def test_fresh_install_is_new(self):
        # 1h since last, 1h since first -> inside the 48h new window.
        assert eng.classify_tier(1.0, 1.0) == "new"

    def test_active_when_past_new_window_but_recent(self):
        # 10h since last, 100h since first -> active.
        assert eng.classify_tier(10.0, 100.0) == "active"

    def test_active_when_no_first_launch_but_recent_last(self):
        # first_launch unknown; last launch very recent -> active.
        assert eng.classify_tier(2.0, None) == "active"

    def test_dormant_at_48h_boundary(self):
        # Exactly 48h is dormant (>=).
        assert eng.classify_tier(48.0, 10_000.0) == "dormant"

    def test_dormant_between_48h_and_14d(self):
        assert eng.classify_tier(100.0, 10_000.0) == "dormant"

    def test_lapsed_at_14d_boundary(self):
        assert eng.classify_tier(14 * 24.0, 10_000.0) == "lapsed"

    def test_lapsed_well_past_14d(self):
        assert eng.classify_tier(30 * 24.0, 10_000.0) == "lapsed"

    def test_negative_delta_clamped_to_zero(self):
        # Clock-skew: last_launch stamped in the future. Must not flip
        # to lapsed just because the arithmetic went negative.
        assert eng.classify_tier(-5.0, -5.0) in {"active", "new"}


# ─── compute_metrics ──────────────────────────────────────────────
class TestComputeMetrics:
    def test_empty_user_data_returns_unknown(self):
        m = eng.compute_metrics({}, now=datetime(2026, 4, 21, tzinfo=UTC))
        assert m.tier == "unknown"
        assert m.hours_since_last_launch is None
        assert m.hours_since_first_launch is None

    def test_non_dict_input_is_safe(self):
        m = eng.compute_metrics(None, now=datetime(2026, 4, 21, tzinfo=UTC))
        assert m.tier == "unknown"

    def test_basic_active_tier(self):
        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        data = {
            "first_launch_at": _iso(now - timedelta(days=10)),
            "last_launch_at":  _iso(now - timedelta(hours=3)),
        }
        m = eng.compute_metrics(data, now=now)
        assert m.tier == "active"
        assert pytest.approx(m.hours_since_last_launch, abs=1e-6) == 3.0
        assert pytest.approx(m.days_since_last_launch, abs=1e-6) == 3.0 / 24

    def test_new_tier_when_first_within_48h(self):
        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        data = {
            "first_launch_at": _iso(now - timedelta(hours=5)),
            "last_launch_at":  _iso(now - timedelta(hours=1)),
        }
        assert eng.compute_metrics(data, now=now).tier == "new"

    def test_dormant_tier(self):
        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        data = {
            "first_launch_at": _iso(now - timedelta(days=60)),
            "last_launch_at":  _iso(now - timedelta(days=3)),
        }
        assert eng.compute_metrics(data, now=now).tier == "dormant"

    def test_lapsed_tier(self):
        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        data = {
            "first_launch_at": _iso(now - timedelta(days=100)),
            "last_launch_at":  _iso(now - timedelta(days=30)),
        }
        assert eng.compute_metrics(data, now=now).tier == "lapsed"

    def test_malformed_iso_falls_back_to_unknown(self):
        m = eng.compute_metrics(
            {"last_launch_at": "not-a-date", "first_launch_at": "also-bad"},
            now=datetime(2026, 4, 21, tzinfo=UTC),
        )
        assert m.tier == "unknown"

    def test_accepts_trailing_z_timestamp(self):
        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        data = {"last_launch_at": "2026-04-21T10:00:00Z"}
        m = eng.compute_metrics(data, now=now)
        assert m.tier == "active"
        assert pytest.approx(m.hours_since_last_launch, abs=1e-6) == 2.0

    def test_naive_timestamp_treated_as_utc(self):
        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        data = {"last_launch_at": "2026-04-21T10:00:00"}
        m = eng.compute_metrics(data, now=now)
        assert pytest.approx(m.hours_since_last_launch, abs=1e-6) == 2.0

    def test_to_dict_roundtrip_has_expected_keys(self):
        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        data = {"last_launch_at": _iso(now - timedelta(hours=1)),
                "first_launch_at": _iso(now - timedelta(days=5))}
        d = eng.compute_metrics(data, now=now).to_dict()
        assert set(d.keys()) == {
            "hours_since_last_launch", "hours_since_first_launch",
            "days_since_last_launch", "days_since_first_launch",
            "tier", "as_of",
        }


# ─── should_reengage ──────────────────────────────────────────────
class TestShouldReengage:
    def _metrics(self, hours_since_last):
        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        last = now - timedelta(hours=hours_since_last)
        data = {"first_launch_at": _iso(now - timedelta(days=50)),
                "last_launch_at": _iso(last)}
        return eng.compute_metrics(data, now=now), now

    def test_no_state_means_no_nudge(self):
        m = eng.compute_metrics({}, now=datetime(2026, 4, 21, tzinfo=UTC))
        fire, reason = eng.should_reengage(m, last_nudge_at=None)
        assert fire is False
        assert reason == "unknown_state"

    def test_recently_active_suppresses_nudge(self):
        m, now = self._metrics(2.0)
        fire, reason = eng.should_reengage(m, last_nudge_at=None, now=now)
        assert fire is False
        assert reason == "recently_active"

    def test_idle_past_threshold_fires(self):
        m, now = self._metrics(80.0)
        fire, reason = eng.should_reengage(m, last_nudge_at=None, now=now)
        assert fire is True
        assert reason == "idle_exceeded"

    def test_cooldown_blocks_repeat(self):
        m, now = self._metrics(80.0)
        last_nudge = _iso(now - timedelta(hours=10))
        fire, reason = eng.should_reengage(m, last_nudge_at=last_nudge, now=now)
        assert fire is False
        assert reason == "cooldown"

    def test_after_cooldown_fires_again(self):
        m, now = self._metrics(80.0)
        last_nudge = _iso(now - timedelta(hours=60))  # > default 48h cooldown
        fire, reason = eng.should_reengage(m, last_nudge_at=last_nudge, now=now)
        assert fire is True
        assert reason == "idle_exceeded"

    def test_future_stamped_nudge_counts_as_cooldown(self):
        m, now = self._metrics(80.0)
        future = _iso(now + timedelta(hours=5))
        fire, reason = eng.should_reengage(m, last_nudge_at=future, now=now)
        assert fire is False
        assert reason == "cooldown"

    def test_malformed_nudge_iso_is_ignored(self):
        m, now = self._metrics(80.0)
        fire, reason = eng.should_reengage(m, last_nudge_at="garbage", now=now)
        assert fire is True
        assert reason == "idle_exceeded"

    def test_custom_threshold_respected(self):
        m, now = self._metrics(5.0)
        fire, reason = eng.should_reengage(
            m, last_nudge_at=None,
            idle_threshold_hours=4.0, now=now,
        )
        assert fire is True

    def test_custom_cooldown_respected(self):
        m, now = self._metrics(100.0)
        last_nudge = _iso(now - timedelta(hours=10))
        fire, _ = eng.should_reengage(
            m, last_nudge_at=last_nudge,
            cooldown_hours=5.0, now=now,
        )
        assert fire is True


# ─── build_discord_payload ────────────────────────────────────────
class TestDiscordPayload:
    def _m(self, tier_hours_last):
        now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
        data = {"first_launch_at": _iso(now - timedelta(days=50)),
                "last_launch_at": _iso(now - timedelta(hours=tier_hours_last))}
        return eng.compute_metrics(data, now=now)

    def test_shape_has_username_and_embed(self):
        payload = eng.build_discord_payload(self._m(80.0),
                                            dashboard_url="http://x/")
        assert payload["username"] == "SENTINEL"
        assert isinstance(payload["embeds"], list) and len(payload["embeds"]) == 1
        emb = payload["embeds"][0]
        assert emb["url"] == "http://x/"
        assert "title" in emb and emb["title"]
        assert "description" in emb and emb["description"]

    def test_tier_field_present(self):
        payload = eng.build_discord_payload(self._m(80.0),
                                            dashboard_url="http://x/")
        fields = payload["embeds"][0]["fields"]
        tier_field = next(f for f in fields if f["name"] == "Tier")
        assert tier_field["value"] == "dormant"

    def test_lapsed_uses_stronger_title(self):
        payload = eng.build_discord_payload(self._m(20 * 24.0),
                                            dashboard_url="http://x/")
        assert "waiting" in payload["embeds"][0]["title"].lower()

    def test_top_match_included_when_given(self):
        payload = eng.build_discord_payload(
            self._m(80.0), dashboard_url="http://x/",
            top_match={"title": "Staff PM", "company": "Acme", "score": 0.87},
        )
        fields = payload["embeds"][0]["fields"]
        top = next(f for f in fields if f["name"] == "Top unreviewed")
        assert "Staff PM" in top["value"]
        assert "Acme" in top["value"]
        assert "0.87" in top["value"]

    def test_top_match_without_score_still_renders(self):
        payload = eng.build_discord_payload(
            self._m(80.0), dashboard_url="http://x/",
            top_match={"title": "PM", "company": "Acme"},
        )
        top = next(f for f in payload["embeds"][0]["fields"]
                   if f["name"] == "Top unreviewed")
        assert "PM" in top["value"]

    def test_malformed_top_match_ignored(self):
        payload = eng.build_discord_payload(
            self._m(80.0), dashboard_url="http://x/",
            top_match={"no_title": True},
        )
        names = [f["name"] for f in payload["embeds"][0]["fields"]]
        assert "Top unreviewed" not in names

    def test_custom_username(self):
        payload = eng.build_discord_payload(
            self._m(80.0), dashboard_url="http://x/", username="Pip",
        )
        assert payload["username"] == "Pip"


# ─── days_phrase ──────────────────────────────────────────────────
class TestNudgeStateFile:
    def test_missing_file_returns_empty(self, tmp_path):
        assert eng.load_nudge_state(tmp_path) == {}

    def test_corrupt_file_returns_empty(self, tmp_path):
        (tmp_path / eng.NUDGE_STATE_FILE).write_text("{not json", encoding="utf-8")
        assert eng.load_nudge_state(tmp_path) == {}

    def test_roundtrip_persists(self, tmp_path):
        eng.save_nudge_state(tmp_path, {"last_nudge_at": "2026-04-21T00:00:00+00:00"})
        assert eng.load_nudge_state(tmp_path) == {"last_nudge_at": "2026-04-21T00:00:00+00:00"}


class TestFireIfDue:
    def _user_data(self, now, hours_idle):
        return {
            "first_launch_at": _iso(now - timedelta(days=50)),
            "last_launch_at": _iso(now - timedelta(hours=hours_idle)),
        }

    def test_no_webhook_short_circuits(self):
        now = datetime(2026, 4, 21, tzinfo=UTC)
        calls = []
        def post(url, payload):
            calls.append((url, payload)); return True
        out = eng.fire_if_due(
            user_data=self._user_data(now, 100),
            nudge_state={}, webhook_url="",
            dashboard_url="http://x/", http_post=post, now=now,
        )
        assert out["fired"] is False
        assert out["reason"] == "no_webhook"
        assert calls == []

    def test_fires_and_stamps_state(self):
        now = datetime(2026, 4, 21, tzinfo=UTC)
        calls = []
        def post(url, payload):
            calls.append((url, payload)); return True
        out = eng.fire_if_due(
            user_data=self._user_data(now, 100),
            nudge_state={}, webhook_url="http://hook/",
            dashboard_url="http://x/", http_post=post, now=now,
        )
        assert out["fired"] is True
        assert out["reason"] == "idle_exceeded"
        assert out["next_state"]["last_nudge_at"]
        assert len(calls) == 1

    def test_post_failure_leaves_state_unchanged(self):
        now = datetime(2026, 4, 21, tzinfo=UTC)
        def post(url, payload):
            return False
        out = eng.fire_if_due(
            user_data=self._user_data(now, 100),
            nudge_state={}, webhook_url="http://hook/",
            dashboard_url="http://x/", http_post=post, now=now,
        )
        assert out["fired"] is False
        assert out["reason"] == "post_failed"
        assert out["next_state"] == {}

    def test_post_raises_is_swallowed(self):
        now = datetime(2026, 4, 21, tzinfo=UTC)
        def post(url, payload):
            raise RuntimeError("network down")
        out = eng.fire_if_due(
            user_data=self._user_data(now, 100),
            nudge_state={}, webhook_url="http://hook/",
            dashboard_url="http://x/", http_post=post, now=now,
        )
        assert out["fired"] is False
        assert out["reason"] == "post_failed"

    def test_cooldown_respected(self):
        now = datetime(2026, 4, 21, tzinfo=UTC)
        def post(url, payload):
            raise AssertionError("should not post inside cooldown")
        out = eng.fire_if_due(
            user_data=self._user_data(now, 100),
            nudge_state={"last_nudge_at": _iso(now - timedelta(hours=5))},
            webhook_url="http://hook/",
            dashboard_url="http://x/", http_post=post, now=now,
        )
        assert out["fired"] is False
        assert out["reason"] == "cooldown"

    def test_recently_active_no_post(self):
        now = datetime(2026, 4, 21, tzinfo=UTC)
        def post(url, payload):
            raise AssertionError("should not post when active")
        out = eng.fire_if_due(
            user_data=self._user_data(now, 2),
            nudge_state={}, webhook_url="http://hook/",
            dashboard_url="http://x/", http_post=post, now=now,
        )
        assert out["fired"] is False
        assert out["reason"] == "recently_active"


class TestDaysPhrase:
    def test_none_is_unknown(self):
        assert eng._days_phrase(None) == "unknown"

    def test_sub_day_reports_hours(self):
        assert "hour" in eng._days_phrase(3 / 24.0)

    def test_exactly_one_day_reads_singular(self):
        assert eng._days_phrase(1.0) == "1 day"

    def test_multi_day_plural(self):
        assert eng._days_phrase(5.0) == "5 days"
