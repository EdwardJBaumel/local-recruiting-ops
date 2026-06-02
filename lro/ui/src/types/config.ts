/** Mirrors data/config.json shape. Only the fields the UI edits. */
export interface AppConfig {
  ingest?: {
    role_keywords?: string[];
    greenhouse_companies?: string[];
    lever_companies?: string[];
    ashby_companies?: [string, string][];
    // Custom-scraper toggles. Each maps to a hand-written scraper in
    // agents/ingest.py against a public-feed endpoint that is silent
    // or explicitly open per the company's TOS / robots.txt. Removed
    // entries (Netflix, Salesforce, IBM, Cisco) had drifting
    // endpoints — see the ingest.py archaeology comment for context.
    enable_amazon?: boolean;
    enable_google?: boolean;
    enable_nvidia?: boolean;  // Workday
    enable_adobe?: boolean;   // Workday
    enable_intel?: boolean;   // Workday
  };
  match?: { threshold?: number; model?: string; profile_text?: string };
  parse?: { model?: string };
  analyze?: { model?: string; top_n?: number };
  digest?: { model?: string };
  cover_letter?: { model?: string };
  preferences?: {
    work_modes?: ("remote" | "hybrid" | "onsite")[];
    allowed_locations?: string[];
    blocked_locations?: string[];
    /**
     * Title-keyword block list. A posting whose title whole-word-matches
     * any of these is skipped at scrape time (ingest) AND hidden in the
     * Matches view AND penalised by the match score. Word-boundary
     * matched: "engineer" catches "Software Engineer" but not
     * "Engineering". Edit in Settings → Job titles.
     */
    blocked_title_keywords?: string[];
    location_mode?: "hard" | "soft";
    allowed_countries?: string[];
    salary_floor_usd?: number;
    salary_weight?: number;
    years_experience?: number;
    years_weight?: number;
    current_level?: string;
    trapdoor_enabled?: boolean;
    /**
     * Per-tier freshness windows (days). Postings older than the
     * tier-appropriate window are hidden in the Matches view. The
     * tiers are computed client-side via lib/companyTier.ts because
     * hiring velocity differs wildly by company size:
     *   - mega   (Amazon, Google, Workday tenants): 30d default —
     *     evergreen reqs that stay open across hiring waves.
     *   - large  (Stripe, Databricks, OpenAI, public mid-caps): 14d
     *     default — structured loops, mid-funnel by day 14.
     *   - growth (everything else): 7d default — early-bird
     *     advantage is real for smaller / faster-moving employers.
     * Setting any value to 0 disables that tier's filter (show all
     * ages for that tier).
     */
    freshness_window_mega_days?: number;
    freshness_window_large_days?: number;
    freshness_window_growth_days?: number;
  };
  fake_detection?: {
    aggressiveness?: "low" | "balanced" | "strict";
    ghost_weight?: number;
    flag_threshold?: number;
    warn_threshold?: number;
  };
  pipeline?: { auto_start?: boolean };
}

export interface ResumeState {
  has_resume: boolean;
  metadata?: {
    filename?: string;
    char_count?: number;
    size_bytes?: number;
    uploaded_at?: string;
  };
  additional_notes_len?: number;
}

export interface ResumeProfile {
  name?: string;
  headline?: string;
  years_experience?: number;
  seniority?: string;
  skills?: string[];
  technologies?: string[];
  domains?: string[];
  target_roles?: string[];
  summary?: string;
  _fallback?: boolean;
  generated_at?: string;
  model?: string;
  /** Set by the backend when the user has manually edited the profile.
   *  Cleared on the next re-parse since that overwrites everything. */
  _user_edited?: boolean;
  _user_edited_at?: string;
}
