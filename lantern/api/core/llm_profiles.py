"""
LLM PROFILE REGISTRY

The pipeline has four distinct LLM tasks (parse, match, analyse,
digest) plus a separate cover-letter task driven from the Matches
panel. Historically each had its own model which meant up to four
12-14B models rotating through VRAM per cycle. On a 12 GB card that
swap cost dominates wall-clock. This module centralises the "which
model for which task" mapping so the user picks a single profile name
and the stages inherit consistently.

(There used to be a fifth `chat` task driving an in-app dashboard
chat UI. That UI was removed; the chat config plumbing was removed
with it.)

Profiles (ordered cheap → heavy):

  a. `lightweight` — one 7-8B model for everything. Suitable for 6 GB
     cards or users who only care about the matching stage.
  b. `compact` — two models: a fast 7-8B for parse/QA, a 12B for
     match/analyse/digest. Two resident at most; one swap between
     the ingest phase and the match phase.
  c. `full` — four-model rotation, the historical default. Best per-
     stage quality, worst swap cost.
  d. `custom` — marker: treat every per-stage config field as
     authoritative. Profile defaults are not applied.

Any per-stage field explicitly set in config overrides the profile
default. That means a user can pick `compact` and still pin
`analyze.model` to something different.

Resolution order inside apply_profile(cfg):

  a. If `cfg.llm_profile` is unset or "custom", cfg is returned
     unchanged.
  b. Else every stage model path (parse.model, match.model, etc.) is
     set to the profile's recommendation IF the stage currently has no
     value in cfg (None / missing / empty string). Existing values
     are left alone.

The function is pure: it operates on a deep copy of cfg so callers
don't risk mutating the source dict.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class LLMProfile:
    name: str
    parse: str
    match: str
    analyze: str
    digest: str
    cover_letter: str
    approx_unique_models: int
    notes: str


PROFILES: dict[str, LLMProfile] = {
    "lightweight": LLMProfile(
        name="lightweight",
        parse="qwen3:8b",
        match="qwen3:8b",
        analyze="qwen3:8b",
        digest="qwen3:8b",
        cover_letter="qwen3:8b",
        approx_unique_models=1,
        notes="Single 7-8B model for every stage. Best for 6 GB GPUs.",
    ),
    "compact": LLMProfile(
        name="compact",
        parse="qwen3:8b",
        match="qwen3:14b",
        analyze="qwen3:14b",
        digest="qwen3:14b",
        cover_letter="qwen3:14b",
        approx_unique_models=2,
        notes="Two models: qwen3:8b for parse + always-on fallback, qwen3:14b for everything else. ~16 GB GPU comfortable; no swap thrash between tasks.",
    ),
    "full": LLMProfile(
        name="full",
        parse="qwen3:8b",
        match="qwen3:14b",
        analyze="qwen3:14b",
        digest="gemma3:12b",
        cover_letter="qwen3:14b",
        approx_unique_models=3,
        notes="Three models: parse-fast, qwen3:14b for reasoning, gemma3:12b for digest's narrative prose. Slightly more VRAM swap per cycle but the digest reads slightly warmer.",
    ),
}

DEFAULT_PROFILE = "compact"

# Keys in the config dict where a per-stage model lives. (section, key)
# pairs; None section means top-level. This is the authoritative map
# between profile fields and config shape.
_CONFIG_PATHS: dict[str, tuple[Optional[str], str]] = {
    "parse":        ("parse",        "model"),
    "match":        ("match",        "model"),
    "analyze":      (None,           "analyze_model"),
    "digest":       (None,           "digest_model"),
    "cover_letter": (None,           "cover_letter_model"),
}


def list_profiles() -> list[dict]:
    """Serialisable snapshot for the wizard / settings UI."""
    return [
        {
            "name": p.name,
            "parse": p.parse, "match": p.match, "analyze": p.analyze,
            "digest": p.digest, "cover_letter": p.cover_letter,
            "approx_unique_models": p.approx_unique_models,
            "notes": p.notes,
        }
        for p in PROFILES.values()
    ]


def _get_stage_value(cfg: dict, stage: str) -> Optional[str]:
    section, key = _CONFIG_PATHS[stage]
    if section is None:
        return cfg.get(key)
    sec = cfg.get(section)
    if not isinstance(sec, dict):
        return None
    return sec.get(key)


def _set_stage_value(cfg: dict, stage: str, value: str) -> None:
    section, key = _CONFIG_PATHS[stage]
    if section is None:
        cfg[key] = value
        return
    sec = cfg.get(section)
    if not isinstance(sec, dict):
        sec = {}
        cfg[section] = sec
    sec[key] = value


def apply_profile(cfg: dict) -> dict:
    """Return a new cfg with profile defaults filled in where the user
    hasn't already set a per-stage model. See module docstring for
    resolution order.
    """
    if not isinstance(cfg, dict):
        return cfg  # defensive; callers always pass a dict in prod
    new_cfg = copy.deepcopy(cfg)
    profile_name = (new_cfg.get("llm_profile") or "").strip().lower()
    if not profile_name or profile_name == "custom":
        return new_cfg
    profile = PROFILES.get(profile_name)
    if profile is None:
        # Unknown profile name: fall through without mutating. Caller
        # can log and decide what to do.
        return new_cfg
    for stage in _CONFIG_PATHS:
        current = _get_stage_value(new_cfg, stage)
        if current is None or (isinstance(current, str) and not current.strip()):
            _set_stage_value(new_cfg, stage, getattr(profile, stage))
    return new_cfg


def describe(profile_name: Optional[str]) -> dict:
    """Metadata + serialisable view of a named profile. Returns a stub
    for unknown names so callers can render "custom" in the UI."""
    if not profile_name:
        return {"name": None, "known": False, "stages": {}, "notes": "No profile"}
    key = profile_name.strip().lower()
    if key == "custom":
        return {"name": "custom", "known": True, "stages": {},
                "notes": "Every stage configured manually."}
    p = PROFILES.get(key)
    if p is None:
        return {"name": key, "known": False, "stages": {},
                "notes": "Unknown profile."}
    return {
        "name": p.name,
        "known": True,
        "stages": {stage: getattr(p, stage) for stage in _CONFIG_PATHS},
        "approx_unique_models": p.approx_unique_models,
        "notes": p.notes,
    }
