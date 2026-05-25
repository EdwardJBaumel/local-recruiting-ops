"""Tests for the manual-vs-auto cycle scheduling decision.

We target the pure helper `_resolve_manual_mode(config, env)` so the
test is decoupled from the heavyweight Orchestrator __init__ (agents,
embeddings, data dir). This mirrors the pattern used for the dead-slug
cooldown and match-registry helpers.
"""
import pytest

from sentinel.orchestrator import _resolve_manual_mode


class TestResolveManualMode:
    # ── env wins over config ─────────────────────────────────────────
    def test_env_truthy_manual_values(self):
        for val in ("1", "true", "True", "yes", "YES", "on"):
            assert _resolve_manual_mode({}, {"SENTINEL_MANUAL_MODE": val}) is True

    def test_env_falsy_auto_values(self):
        for val in ("0", "false", "False", "no", "off"):
            assert _resolve_manual_mode(
                {"pipeline": {"auto_start": False}},  # config says manual
                {"SENTINEL_MANUAL_MODE": val},
            ) is False, f"env {val!r} should force auto and beat config"

    def test_env_blank_falls_through_to_config(self):
        assert _resolve_manual_mode(
            {"pipeline": {"auto_start": False}},
            {"SENTINEL_MANUAL_MODE": ""},
        ) is True
        assert _resolve_manual_mode(
            {"pipeline": {"auto_start": True}},
            {"SENTINEL_MANUAL_MODE": "   "},
        ) is False

    def test_env_unknown_value_falls_through(self):
        # A stray "maybe" shouldn't silently flip a mode; fall through.
        assert _resolve_manual_mode({}, {"SENTINEL_MANUAL_MODE": "maybe"}) is False

    # ── config pipeline.auto_start ───────────────────────────────────
    def test_config_auto_start_false_is_manual(self):
        assert _resolve_manual_mode({"pipeline": {"auto_start": False}}, {}) is True

    def test_config_auto_start_true_is_auto(self):
        assert _resolve_manual_mode({"pipeline": {"auto_start": True}}, {}) is False

    def test_missing_pipeline_section_defaults_to_auto(self):
        assert _resolve_manual_mode({}, {}) is False

    def test_missing_auto_start_key_defaults_to_auto(self):
        assert _resolve_manual_mode({"pipeline": {}}, {}) is False

    def test_null_config_defaults_to_auto(self):
        assert _resolve_manual_mode(None, {}) is False  # defensive

    # ── resolution order (env beats config beats default) ────────────
    def test_env_manual_beats_config_auto(self):
        assert _resolve_manual_mode(
            {"pipeline": {"auto_start": True}},
            {"SENTINEL_MANUAL_MODE": "1"},
        ) is True

    def test_env_auto_beats_config_manual(self):
        assert _resolve_manual_mode(
            {"pipeline": {"auto_start": False}},
            {"SENTINEL_MANUAL_MODE": "0"},
        ) is False


class TestEnvDefaultFromOsEnviron:
    """Smoke test that the instance method reads os.environ when no
    override is passed. Uses monkeypatch instead of instantiating the
    full Orchestrator (too heavy for a unit test)."""

    def test_os_environ_respected(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_MANUAL_MODE", "1")
        assert _resolve_manual_mode({}) is True

    def test_os_environ_unset_default_auto(self, monkeypatch):
        monkeypatch.delenv("SENTINEL_MANUAL_MODE", raising=False)
        assert _resolve_manual_mode({}) is False
