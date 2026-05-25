"""Tests for the Brief-tab resource panel aggregator.

The aggregator is deliberately built as pure helpers + dependency
injection for nvidia-smi so we don't need a GPU or running pipeline to
exercise any path. Covers:
  a. summarise_cycles — median, tail-cap, missing/corrupt handling
  b. summarise_match_stats — shape stability when stats are absent
  c. probe_gpu_vram — happy path + every failure mode (no binary, bad
     exit, malformed CSV, timeout)
  d. collect — end-to-end snapshot against a fixture directory
"""
import json
import subprocess
from pathlib import Path

import pytest

from sentinel.core import resource_snapshot as rs


# ─── summarise_cycles ─────────────────────────────────────────────
class TestSummariseCycles:
    def test_none_returns_empty_shape(self):
        out = rs.summarise_cycles(None)
        assert out == {"count": 0, "median_seconds": None, "last": None}

    def test_empty_list_returns_empty_shape(self):
        assert rs.summarise_cycles([])["count"] == 0

    def test_median_on_tail_only(self):
        cycles = [{"seconds": 10}] * 30 + [{"seconds": 100}] * 5
        out = rs.summarise_cycles(cycles, keep=5)
        # Keep=5 keeps only the 100s entries → median 100, not 10.
        assert out["median_seconds"] == 100.0

    def test_count_reflects_lifetime(self):
        cycles = [{"seconds": 1}] * 50
        out = rs.summarise_cycles(cycles, keep=10)
        assert out["count"] == 50

    def test_last_summary_has_whitelist_fields(self):
        cycles = [{
            "cycle": 7,
            "seconds": 42.0,
            "ts": "2026-04-21T12:00:00+00:00",
            "matches": 3,
            "new_jobs": 1,
            "raw_packets": ["huge blob"],  # should be stripped
        }]
        out = rs.summarise_cycles(cycles)
        assert out["last"]["cycle"] == 7
        assert out["last"]["matches"] == 3
        assert "raw_packets" not in out["last"]

    def test_corrupt_entries_are_ignored(self):
        cycles = [{"seconds": "not-a-number"}, {"seconds": 5}, "garbage"]
        out = rs.summarise_cycles(cycles)
        assert out["median_seconds"] == 5

    def test_all_corrupt_returns_none_median(self):
        out = rs.summarise_cycles([{"x": 1}, "garbage"])
        assert out["median_seconds"] is None


# ─── summarise_match_stats ────────────────────────────────────────
class TestSummariseMatchStats:
    def test_none_returns_all_none_dict(self):
        out = rs.summarise_match_stats(None)
        assert set(out.keys()) == {
            "mode", "median_latency_ms", "threshold",
            "embeddings_active", "sample_count"
        }
        assert all(v is None for v in out.values())

    def test_full_payload_passthrough(self):
        payload = {
            "mode": "embeddings",
            "median_latency_ms": 195.9,
            "threshold": 0.45,
            "embeddings_active": True,
            "sample_count": 386,
            "extra_field": "ignored",
        }
        out = rs.summarise_match_stats(payload)
        assert out["mode"] == "embeddings"
        assert out["median_latency_ms"] == 195.9
        assert "extra_field" not in out


# ─── probe_gpu_vram ───────────────────────────────────────────────
class _FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _runner(stdout="", returncode=0):
    def run(*args, **kwargs):
        return _FakeProc(stdout=stdout, returncode=returncode)
    return run


class TestProbeGpuVram:
    def test_happy_path(self):
        out = rs.probe_gpu_vram(
            runner=_runner("NVIDIA GeForce RTX 4070 Super, 12288, 4096\n")
        )
        assert out == {
            "name": "NVIDIA GeForce RTX 4070 Super",
            "total_mib": 12288,
            "used_mib": 4096,
            "used_pct": 33.3,
        }

    def test_multi_gpu_uses_first_line(self):
        out = rs.probe_gpu_vram(
            runner=_runner("Card A, 8192, 1024\nCard B, 24576, 0\n")
        )
        assert out["name"] == "Card A"
        assert out["total_mib"] == 8192

    def test_missing_binary_returns_none(self):
        def raise_fnf(*a, **kw):
            raise FileNotFoundError("nvidia-smi not found")
        assert rs.probe_gpu_vram(runner=raise_fnf) is None

    def test_timeout_returns_none(self):
        def raise_to(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=1)
        assert rs.probe_gpu_vram(runner=raise_to) is None

    def test_non_zero_exit_returns_none(self):
        assert rs.probe_gpu_vram(runner=_runner(returncode=9)) is None

    def test_empty_stdout_returns_none(self):
        assert rs.probe_gpu_vram(runner=_runner(stdout="")) is None

    def test_malformed_line_returns_none(self):
        # Only one field - can't parse.
        assert rs.probe_gpu_vram(runner=_runner(stdout="some garbage\n")) is None

    def test_non_integer_memory_returns_none(self):
        out = rs.probe_gpu_vram(runner=_runner("A, N/A, 0\n"))
        assert out is None

    def test_zero_total_vram_guards_divide(self):
        out = rs.probe_gpu_vram(runner=_runner("Card, 0, 0\n"))
        assert out is not None
        assert out["used_pct"] is None


# ─── collect ──────────────────────────────────────────────────────
class TestCollect:
    def test_empty_dir_produces_complete_shape(self, tmp_path):
        out = rs.collect(tmp_path, include_gpu=False, include_memory=False)
        assert set(out.keys()) >= {"cycles", "match", "gpu", "memory", "as_of"}
        assert out["cycles"]["count"] == 0
        assert out["match"]["mode"] is None
        assert out["gpu"] is None
        assert out["memory"] is None
        assert isinstance(out["as_of"], str) and out["as_of"].endswith("+00:00")

    def test_reads_fixture_files(self, tmp_path):
        (tmp_path / "cycle_times.json").write_text(json.dumps([
            {"cycle": 1, "seconds": 100.0, "matches": 1, "ts": "t"},
            {"cycle": 2, "seconds": 200.0, "matches": 2, "ts": "t"},
        ]), encoding="utf-8")
        (tmp_path / "match_stats.json").write_text(json.dumps({
            "mode": "embeddings", "median_latency_ms": 195.9,
            "threshold": 0.45, "embeddings_active": True,
            "sample_count": 50,
        }), encoding="utf-8")
        out = rs.collect(tmp_path, include_gpu=False, include_memory=False)
        assert out["cycles"]["count"] == 2
        assert out["cycles"]["median_seconds"] == 150.0
        assert out["match"]["mode"] == "embeddings"
        assert out["match"]["median_latency_ms"] == 195.9

    def test_corrupt_files_do_not_crash(self, tmp_path):
        (tmp_path / "cycle_times.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "match_stats.json").write_text("also not json",
                                                   encoding="utf-8")
        out = rs.collect(tmp_path, include_gpu=False, include_memory=False)
        assert out["cycles"]["count"] == 0
        assert out["match"]["mode"] is None

    def test_gpu_runner_injection(self, tmp_path):
        runner = _runner("Stub GPU, 1000, 500\n")
        out = rs.collect(tmp_path, include_gpu=True, include_memory=False,
                         gpu_runner=runner)
        assert out["gpu"]["name"] == "Stub GPU"
        assert out["gpu"]["used_mib"] == 500

    def test_include_gpu_false_returns_none(self, tmp_path):
        out = rs.collect(tmp_path, include_gpu=False, include_memory=False)
        assert out["gpu"] is None
