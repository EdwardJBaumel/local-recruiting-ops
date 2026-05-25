"""Tests for the dead-slug cooldown logic in IngestAgent.

We don't make real HTTP calls here. The cooldown layer is independent of
the network layer; we only exercise the helpers and the persisted file.
"""
import json
from datetime import datetime, timedelta, timezone

from sentinel.agents.ingest import IngestAgent


def _make(data_dir, cooldown_days=7):
    return IngestAgent(
        {"dead_slug_cooldown_days": cooldown_days, "delay_range": (0, 0)},
        data_dir=data_dir,
    )


class TestDeadSlugCooldown:
    def test_fresh_slug_not_in_cooldown(self, tmp_path):
        agent = _make(tmp_path)
        assert agent._is_in_cooldown("greenhouse", "stripe") is False

    def test_record_writes_file_and_history(self, tmp_path):
        agent = _make(tmp_path)
        agent._record_dead_slug("greenhouse", "does-not-exist")

        assert agent._is_in_cooldown("greenhouse", "does-not-exist") is True
        path = tmp_path / "dead_slugs.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert any(d["source"] == "greenhouse" and d["slug"] == "does-not-exist"
                   for d in data)

    def test_history_survives_restart(self, tmp_path):
        agent = _make(tmp_path)
        agent._record_dead_slug("lever", "stale-co")
        # New instance loads the persisted file.
        agent2 = _make(tmp_path)
        assert agent2._is_in_cooldown("lever", "stale-co") is True

    def test_clear_removes_entry(self, tmp_path):
        agent = _make(tmp_path)
        agent._record_dead_slug("ashby", "came-back")
        assert agent._is_in_cooldown("ashby", "came-back") is True
        agent._clear_dead_slug("ashby", "came-back")
        assert agent._is_in_cooldown("ashby", "came-back") is False

    def test_expired_cooldown_rearms(self, tmp_path):
        agent = _make(tmp_path, cooldown_days=7)
        # Manually poke a historical timestamp 10 days old.
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        agent._dead_history[("greenhouse", "recovered")] = old_ts
        agent._persist_dead_history()
        assert agent._is_in_cooldown("greenhouse", "recovered") is False

    def test_per_cycle_snapshot_dedupes(self, tmp_path):
        agent = _make(tmp_path)
        agent._record_dead_slug("greenhouse", "foo")
        agent._record_dead_slug("greenhouse", "foo")
        assert len([d for d in agent.dead_slugs if d["slug"] == "foo"]) == 1

    def test_corrupt_file_fails_open(self, tmp_path):
        (tmp_path / "dead_slugs.json").write_text("not json {", encoding="utf-8")
        agent = _make(tmp_path)
        # No crash, empty history.
        assert agent._dead_history == {}
        assert agent._is_in_cooldown("greenhouse", "foo") is False
