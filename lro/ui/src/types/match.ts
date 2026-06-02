/**
 * Match payload as written by lro/api/agents/match.py. Every field is
 * optional from the wire because the backend lazily populates fields
 * across stages — a row may have a fit-gap if it's been through
 * ANALYZE, but a fresh match from the matching phase won't.
 */
export interface MatchPayload {
  url: string;
  title: string;
  company: string;
  location?: string;
  posted_date?: string;
  description?: string;
  tags?: string[];
  technologies?: string[];
  archetype?: string;
  archetype_label?: string;
  salary?: { min?: number; max?: number; currency?: string };
  years_experience?: number;
  work_mode?: string;

  // Local Recruiting Ops-internal fields (prefixed _ in the payload).
  _match_score?: number;          // post-ghost-fold final on raw 0-1 scale
  _match_score_display?: number;  // post-ghost-fold + calibrated for UI, 0-1
  _match_score_pre_ghost?: number; // pre-ghost-fold (pure resume↔JD fit), 0-1
  _match_score_pre_learned?: number;
  _learned_bonus?: number;
  _ghost_penalty?: number;        // 0-1, fraction Fit was knocked down by
  _match_tier?: "match" | "maybe";
  _location?: string;             // normalised location
  _country?: string;
  _seen?: boolean;
  _starred?: boolean;
  _dismissed?: boolean;
  _removed?: boolean;             // posting expired / 404
  _last_seen_at?: string;
  _first_seen_at?: string;
  _source?: string;               // "greenhouse:stripe", "lever:notion", etc.

  /**
   * Ghost / fake-job detection result. Wire shape mirrors
   * `score_fake()` in api/core/fake_detector.py exactly — do not add
   * `reasons` or `badge` fields here; they never existed on the wire.
   * The UI derives the clear/caution/suspect band from `score` itself
   * (see GhostBadge), and the per-signal `reason` strings live inside
   * the `signals` dict.
   */
  _fake?: {
    score: number;                  // 0-1, calibrated ghost suspicion (display)
    score_raw?: number;             // 0-1, pre-calibration raw score
    is_suspect?: boolean;           // score >= threshold
    threshold?: number;             // effective suspect threshold used
    /**
     * Per-signal breakdown. Keyed by signal name (age_stale,
     * missing_fields, duplicate_title, …). A signal only appears here
     * if it could be evaluated; `score === 0` means it was evaluated
     * but didn't fire.
     */
    signals?: Record<string, { score: number; reason: string }>;
  };

  _fit_gap?: {
    summary?: string;
    matched?: string[];
    gaps?: string[];
    rationale?: string;
  };

  /** LLM-generated 3-4 sentence narrative summary of the JD. Populated
   *  on demand via the "Summarize" button on MatchDetail and cached
   *  back into the registry, so subsequent renders show it instantly. */
  _summary?: string;
  /** Model that produced `_summary` — surfaced as a tiny caption
   *  beneath the summary for traceability. */
  _summary_model?: string;
}
