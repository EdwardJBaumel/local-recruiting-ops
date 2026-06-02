"""
APPLICATION FUNNEL TRACKER + DECISION LOG
State machine: Discovered > Evaluated > Applied > Responded > Interview > Offer | Passed
Logs every decision with timestamps and reasons.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

from core.io_safe import write_text_atomic

logger = logging.getLogger("lro.tracker")

STATES = ["discovered", "evaluated", "applied", "responded", "interview", "offer", "rejected", "passed"]


class ApplicationTracker:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.tracker_file = self.data_dir / "tracker.json"
        self.decision_log_file = self.data_dir / "decision_log.json"
        self.applications = self._load(self.tracker_file, [])
        self.decisions = self._load(self.decision_log_file, [])

    def _load(self, path, default):
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return default

    def _save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.tracker_file, json.dumps(self.applications, indent=2))
        write_text_atomic(self.decision_log_file, json.dumps(self.decisions, indent=2))

    def _key(self, title, company):
        return f"{title.lower().strip()}||{company.lower().strip()}"

    def _find(self, title, company):
        k = self._key(title, company)
        for app in self.applications:
            if self._key(app.get("title", ""), app.get("company", "")) == k:
                return app
        return None

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def discover(self, title, company, url="", source="", match_score=0, fit_gap=None):
        if self._find(title, company):
            return
        app = {
            "title": title, "company": company, "url": url, "source": source,
            "match_score": match_score, "state": "discovered",
            "history": [{"state": "discovered", "ts": self._now()}],
            "fit_gap": fit_gap,
        }
        self.applications.append(app)

    def transition(self, title, company, state, notes=""):
        app = self._find(title, company)
        if not app:
            return
        app["state"] = state
        app["history"].append({"state": state, "ts": self._now(), "notes": notes})

    def log_pass(self, title, company, reason, score=0):
        self.decisions.append({
            "title": title, "company": company, "reason": reason,
            "score": score, "ts": self._now(),
        })
        app = self._find(title, company)
        if app:
            app["state"] = "passed"
            app["history"].append({"state": "passed", "ts": self._now(), "notes": reason})

    def bulk_discover(self, matched_packets, fit_reports=None):
        gap_map = {}
        if fit_reports:
            for r in fit_reports:
                k = self._key(r.get("title", ""), r.get("company", ""))
                gap_map[k] = r

        for pkt in matched_packets:
            p = pkt.payload
            title = p.get("title", "")
            company = p.get("company", "")
            k = self._key(title, company)
            self.discover(
                title=title, company=company,
                url=p.get("url", ""), source=p.get("_source", ""),
                match_score=p.get("_match_score", 0),
                fit_gap=gap_map.get(k),
            )
        self._save()

    def funnel_metrics(self) -> dict:
        counts = Counter(app.get("state", "unknown") for app in self.applications)
        total = len(self.applications)
        applied = counts.get("applied", 0) + counts.get("responded", 0) + counts.get("interview", 0) + counts.get("offer", 0)
        responded = counts.get("responded", 0) + counts.get("interview", 0) + counts.get("offer", 0)
        return {
            "total_discovered": total,
            "by_state": dict(counts),
            "discovery_to_apply": f"{(applied/max(total,1))*100:.1f}%",
            "apply_to_response": f"{(responded/max(applied,1))*100:.1f}%",
            "total_passed": len(self.decisions),
        }

    def company_signals(self) -> dict:
        """Per-company score multiplier based on tracker history.

        Returns {company_lower: bonus in [-0.08, +0.08]}. The idea: if
        the user has already had good interactions with Stripe (applied
        twice, got an interview), future Stripe matches get a small
        boost so they surface higher. Rejected companies get a small
        penalty. Passed (user-dismissed) companies get a tiny penalty.

        Weights are deliberately mild — +8% max — so this layer nudges
        rather than dominates. The ghost penalty + fit score still call
        the shots.

        Called from MatchAgent at scoring time (hot path). Cheap — a
        single pass over self.applications, no I/O.
        """
        import math
        positive = {"applied": 1, "responded": 2, "interview": 3, "offer": 5}
        # "Stale-discovered" threshold: postings that sat in discovered
        # state past this many days without being applied to or passed
        # on count as a weak implicit negative. Rationale: the user saw
        # the role surface in their feed, didn't engage either way —
        # that's softer than an explicit pass (-0.5) but still signal.
        # 7 days is long enough to filter out "haven't looked at the
        # dashboard yet" noise. Weight ≈ 25% of an explicit pass.
        STALE_DAYS = 7
        STALE_WEIGHT = -0.125
        now = datetime.now(timezone.utc)
        raw = {}
        for app in self.applications:
            company = (app.get("company") or "").lower().strip()
            if not company:
                continue
            state = app.get("state", "")
            delta = 0.0
            if state in positive:
                delta = positive[state]
            elif state == "rejected":
                delta = -2.0
            elif state == "passed":
                delta = -0.5
            elif state == "discovered":
                # Use the first history entry's timestamp (the discover
                # event); fall back to skipping if the history is
                # malformed. Cheap — datetime parse + timedelta.
                history = app.get("history") or []
                if history:
                    ts = history[0].get("ts")
                    try:
                        discovered_at = datetime.fromisoformat(ts)
                        if (now - discovered_at).days >= STALE_DAYS:
                            delta = STALE_WEIGHT
                    except (TypeError, ValueError):
                        pass
            raw[company] = raw.get(company, 0.0) + delta
        out = {}
        for company, score in raw.items():
            # tanh smoothly saturates at ±1; scale to ±0.08 so the
            # multiplier never swings the match score by more than 8
            # percentage points.
            out[company] = round(math.tanh(score / 10.0) * 0.08, 4)
        return out

    def export_for_dashboard(self) -> dict:
        return {
            "applications": self.applications,
            "decisions": self.decisions,
            "metrics": self.funnel_metrics(),
            "company_signals": self.company_signals(),
        }
