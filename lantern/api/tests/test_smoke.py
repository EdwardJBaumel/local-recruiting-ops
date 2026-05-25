"""
Smoke tests: every module under core/ and agents/ must import without
raising, and the cheap pure-Python agents/scorers must instantiate with
an empty/default config.

These catch the "someone added a top-level `import torch` / broke a
syntax / left a bad relative import" class of regression that unit
tests on individual functions can miss.

Module discovery is dynamic (filesystem glob) so the suite stays
honest as files are added or removed.
"""
import importlib
from pathlib import Path

import pytest

# tests/ -> lantern/api
_API_ROOT = Path(__file__).resolve().parent.parent


def _module_names(package: str) -> list[str]:
    """All importable `package.x` modules, skipping __init__ and dunders."""
    pkg_dir = _API_ROOT / package
    names: list[str] = []
    for py in sorted(pkg_dir.glob("*.py")):
        stem = py.stem
        if stem.startswith("_"):
            continue
        names.append(f"{package}.{stem}")
    return names


CORE_MODULES = _module_names("core")
AGENT_MODULES = _module_names("agents")


def test_module_discovery_found_files():
    # Guard against a glob that silently matched nothing.
    assert len(CORE_MODULES) > 10, CORE_MODULES
    assert len(AGENT_MODULES) > 5, AGENT_MODULES


@pytest.mark.parametrize("modname", CORE_MODULES)
def test_core_module_imports(modname):
    importlib.import_module(modname)


@pytest.mark.parametrize("modname", AGENT_MODULES)
def test_agent_module_imports(modname):
    importlib.import_module(modname)


# ── entry-point / top-level modules ───────────────────────────────
@pytest.mark.parametrize("modname", ["digest", "tracker"])
def test_toplevel_module_imports(modname):
    # main.py / orchestrator.py / server.py have import-time side effects
    # (or bind sockets) so they're deliberately not smoke-imported here.
    importlib.import_module(modname)


# ── cheap pure-Python objects instantiate with default config ─────
def test_qa_agent_instantiates_with_empty_config():
    from agents.qa import QAAgent

    agent = QAAgent({})
    assert agent.stats == {"passed": 0, "warned": 0, "failed": 0}
    # The constructor also tolerates being called with no argument.
    assert QAAgent() is not None


def test_fake_job_detector_instantiates_with_empty_config():
    from agents.fakejob import FakeJobDetector

    det = FakeJobDetector({})
    assert det.enabled is True  # defaults on
    assert FakeJobDetector() is not None


def test_preferences_scorers_instantiate_with_empty_config():
    from core.preferences import (
        TitleScorer, SalaryScorer, ExperienceScorer,
        LocationScorer, LocationFilter, ExperienceFilter, CountryFilter,
    )

    # All should construct from {} and report inactive (nothing configured).
    assert TitleScorer({}).active is False
    assert SalaryScorer({}).active is False
    assert ExperienceScorer({}).active is False
    assert LocationScorer({}).active is False
    assert ExperienceFilter({}).active is False
    assert CountryFilter({}).active is False
    # LocationFilter defaults to soft mode → inactive.
    assert LocationFilter({}).active is False


def test_fit_gap_analyzer_instantiates_with_empty_config():
    from agents.analyzer import FitGapAnalyzer

    analyzer = FitGapAnalyzer({})
    assert analyzer.profile == ""


def test_digest_generator_instantiates(tmp_path):
    from digest import DigestGenerator

    gen = DigestGenerator({"data_dir": str(tmp_path)})
    assert gen.digest_dir.exists()


def test_application_tracker_instantiates(tmp_path):
    from tracker import ApplicationTracker

    tracker = ApplicationTracker(str(tmp_path))
    assert tracker.applications == []
    assert tracker.decisions == []
