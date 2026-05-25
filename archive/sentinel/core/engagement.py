"""
ENGAGEMENT METRICS + RE-ENGAGEMENT NUDGES

Derives "how engaged is the user right now" signals from data the app
already records (first_launch_at / last_launch_at in data/user.json)
and decides whether a Discord nudge should fire. Pure functions so the
scheduler, tests, and the UI all share one source of truth.

Tiers (derived, not user-set):

  a. `new`      — under 48 h since first launch. Pip encourages first
                  match review.
  b. `active`   — launched within the last 48 h and not new.
  c. `dormant`  — 48 h to 14 days since last launch.
  d. `lapsed`   — over 14 days since last launch. Stronger nudge.

`should_reengage` additionally enforces a per-user cooldown so we don't
spam the webhook on every process start. `build_discord_payload` emits
a Discord-shaped JSON dict; the actual HTTP post lives in a thin
wrapper so unit tests never hit the network.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


NUDGE_STATE_FILE = "reengage_nudge.json"
_logger = logging.getLogger("sentinel.engagement")


HOUR = 3600.0
DAY_H = 24.0

NEW_WINDOW_HOURS = 48.0
DORMANT_MIN_HOURS = 48.0
LAPSED_MIN_HOURS = 14.0 * DAY_H

DEFAULT_IDLE_THRESHOLD_HOURS = 72.0
DEFAULT_COOLDOWN_HOURS = 48.0


@dataclass(frozen=True)
class Metrics:
    """Snapshot of engagement state at a moment in time."""
    hours_since_last_launch: Optional[float]
    hours_since_first_launch: Optional[float]
    days_since_last_launch: Optional[float]
    days_since_first_launch: Optional[float]
    tier: str
    as_of_iso: str

    def to_dict(self) -> dict:
        return {
            "hours_since_last_launch": self.hours_since_last_launch,
            "hours_since_first_launch": self.hours_since_first_launch,
            "days_since_last_launch": self.days_since_last_launch,
            "days_since_first_launch": self.days_since_first_launch,
            "tier": self.tier,
            "as_of": self.as_of_iso,
        }


def _parse_iso(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # datetime.fromisoformat handles "+00:00" and trailing "Z" on 3.11+.
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def classify_tier(
    hours_since_last: Optional[float],
    hours_since_first: Optional[float],
) -> str:
    """Label the engagement state from two raw deltas.

    Pure so the tests don't need a mocked clock to exercise every
    branch; the caller supplies the deltas that compute_metrics would
    derive from the user store.
    """
    if hours_since_last is None:
        return "unknown"
    # Negative deltas (clock skew, future-stamped data) are clamped to 0
    # so the tier stays sane rather than flipping to lapsed.
    hours_since_last = max(0.0, hours_since_last)
    if hours_since_last >= LAPSED_MIN_HOURS:
        return "lapsed"
    if hours_since_last >= DORMANT_MIN_HOURS:
        return "dormant"
    if hours_since_first is not None and hours_since_first < NEW_WINDOW_HOURS:
        return "new"
    return "active"


def compute_metrics(user_data: dict, now: Optional[datetime] = None) -> Metrics:
    """Build a Metrics snapshot from the user_store dict. Missing or
    unparseable timestamps collapse to None rather than raising; the
    tier degrades to 'unknown' in that case."""
    if not isinstance(user_data, dict):
        user_data = {}
    t_now = _now(now)
    first_dt = _parse_iso(user_data.get("first_launch_at"))
    last_dt = _parse_iso(user_data.get("last_launch_at"))
    hours_last = (t_now - last_dt).total_seconds() / HOUR if last_dt else None
    hours_first = (t_now - first_dt).total_seconds() / HOUR if first_dt else None
    tier = classify_tier(hours_last, hours_first)
    return Metrics(
        hours_since_last_launch=hours_last,
        hours_since_first_launch=hours_first,
        days_since_last_launch=(hours_last / DAY_H) if hours_last is not None else None,
        days_since_first_launch=(hours_first / DAY_H) if hours_first is not None else None,
        tier=tier,
        as_of_iso=t_now.isoformat(),
    )


def should_reengage(
    metrics: Metrics,
    last_nudge_at: Optional[str],
    *,
    idle_threshold_hours: float = DEFAULT_IDLE_THRESHOLD_HOURS,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    """Decide whether to fire a re-engagement nudge now.

    Returns (fire, reason). Reason is a short machine-readable string
    so callers can log or display it without parsing English.

    Rules:
      a. If hours_since_last_launch is missing, don't fire (we don't
         know the state; no point guessing).
      b. If the user launched inside the idle threshold, don't fire.
      c. If we nudged inside the cooldown window, don't fire.
      d. Otherwise fire.
    """
    if metrics.hours_since_last_launch is None:
        return False, "unknown_state"
    if metrics.hours_since_last_launch < idle_threshold_hours:
        return False, "recently_active"
    last_dt = _parse_iso(last_nudge_at)
    if last_dt is not None:
        t_now = _now(now)
        hours_since_nudge = (t_now - last_dt).total_seconds() / HOUR
        # Clock-skew guard: a future-stamped nudge still counts as
        # "inside cooldown" so we don't spam on a skewed clock.
        if hours_since_nudge < cooldown_hours:
            return False, "cooldown"
    return True, "idle_exceeded"


def build_discord_payload(
    metrics: Metrics,
    *,
    dashboard_url: str,
    username: str = "SENTINEL",
    top_match: Optional[dict] = None,
) -> dict:
    """Return a Discord-webhook-shaped dict. No network IO here.

    `top_match` is the best-scoring unreviewed match if caller has one;
    we only include title + company + score to keep the payload tiny
    and avoid leaking URLs or body text into a chat channel.
    """
    tier = metrics.tier
    days = metrics.days_since_last_launch
    if tier == "lapsed":
        title = "SENTINEL has been waiting."
        body = (
            f"Your pipeline hasn't run in {_days_phrase(days)}. "
            "Fresh roles are piling up."
        )
    elif tier == "dormant":
        title = "SENTINEL check-in"
        body = (
            f"Last launch was {_days_phrase(days)} ago. "
            "A quick cycle keeps the match registry warm."
        )
    elif tier == "new":
        title = "Welcome back to SENTINEL"
        body = "Glad to see you again so soon. Review your first matches?"
    else:
        title = "SENTINEL"
        body = "All fresh. Open the dashboard to review today's matches."

    embed = {
        "title": title,
        "description": body,
        "url": dashboard_url,
        "fields": [
            {"name": "Tier", "value": tier, "inline": True},
            {
                "name": "Idle",
                "value": _days_phrase(days) if days is not None else "unknown",
                "inline": True,
            },
        ],
    }
    if isinstance(top_match, dict) and top_match.get("title"):
        score = top_match.get("score")
        score_str = f"{score:.2f}" if isinstance(score, (int, float)) else "-"
        embed["fields"].append({
            "name": "Top unreviewed",
            "value": f"{top_match.get('title')} @ {top_match.get('company', '?')} ({score_str})",
            "inline": False,
        })
    return {
        "username": username,
        "embeds": [embed],
    }


def load_nudge_state(data_dir: Path) -> dict:
    """Read the reengage-nudge state file. Returns {} if missing or
    unreadable; callers treat the absence of last_nudge_at as 'never'."""
    p = Path(data_dir) / NUDGE_STATE_FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_nudge_state(data_dir: Path, state: dict) -> None:
    """Write the nudge state atomically. Cheap - file is tiny."""
    p = Path(data_dir) / NUDGE_STATE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)


def fire_if_due(
    *,
    user_data: dict,
    nudge_state: dict,
    webhook_url: Optional[str],
    dashboard_url: str,
    http_post: Callable[[str, dict], bool],
    top_match: Optional[dict] = None,
    idle_threshold_hours: float = DEFAULT_IDLE_THRESHOLD_HOURS,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    now: Optional[datetime] = None,
) -> dict:
    """Orchestrate one nudge check. Pure except for the injected poster.

    Returns a result dict: {fired, reason, tier, next_state}. Caller
    persists next_state via save_nudge_state when fired=True. The
    http_post callable takes (url, payload) and returns truthy on
    success; failures leave next_state unchanged so we retry next
    tick.
    """
    t_now = _now(now)
    metrics = compute_metrics(user_data, now=t_now)
    if not webhook_url or not webhook_url.strip():
        return {"fired": False, "reason": "no_webhook",
                "tier": metrics.tier, "next_state": nudge_state}
    last_nudge = (nudge_state or {}).get("last_nudge_at")
    fire, reason = should_reengage(
        metrics, last_nudge_at=last_nudge,
        idle_threshold_hours=idle_threshold_hours,
        cooldown_hours=cooldown_hours,
        now=t_now,
    )
    if not fire:
        return {"fired": False, "reason": reason,
                "tier": metrics.tier, "next_state": nudge_state}
    payload = build_discord_payload(
        metrics, dashboard_url=dashboard_url, top_match=top_match,
    )
    try:
        ok = bool(http_post(webhook_url, payload))
    except Exception as e:
        _logger.warning("Nudge post raised: %s", e)
        ok = False
    if not ok:
        return {"fired": False, "reason": "post_failed",
                "tier": metrics.tier, "next_state": nudge_state}
    next_state = dict(nudge_state or {})
    next_state["last_nudge_at"] = t_now.isoformat()
    next_state["last_reason"] = reason
    next_state["last_tier"] = metrics.tier
    return {"fired": True, "reason": reason,
            "tier": metrics.tier, "next_state": next_state}


def _days_phrase(days: Optional[float]) -> str:
    if days is None:
        return "unknown"
    if days < 1.0:
        hours = max(0, round(days * DAY_H))
        if hours <= 1:
            return "1 hour"
        return f"{hours} hours"
    whole = round(days)
    if whole <= 1:
        return "1 day"
    return f"{whole} days"
