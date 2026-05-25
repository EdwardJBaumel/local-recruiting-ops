/** Mirrors GET /api/status response shape. Only fields the UI consumes. */
export interface StatusResponse {
  status: string;
  pipeline_running: boolean;
  cycle_in_progress: boolean;
  current_tiers: string[] | null;
  matches_count: number;
  cycles_recorded: number;
  avg_scrape_seconds: number | null;
  avg_pipeline_seconds: number | null;
  avg_cycle_seconds: number | null;
  setup_completed: boolean;
  last_cycle_ts: string | null;
  models?: {
    parse?: string;
    match?: string;
    analyze?: string;
    digest?: string;
  };
  model_fallback?: {
    missing: string[];
    substitutes: Record<string, string>;
  };
  match?: {
    mode: string;
    median_latency_ms: number | null;
    threshold: number;
    embeddings_active: boolean;
    sample_count: number;
  };
  progress?: {
    stage: string | null;
    stage_label: string | null;
    stage_index: number | null;
    counts?: Record<string, number>;
  };
  /** Per-source counts written at the end of every INGEST stage.
   *  Wire shape: { cycle, ts, sources: { "<provider>:<slug>": {jobs, errors} } }.
   *  Keys in `sources` are flat strings — "greenhouse:stripe", "amazon",
   *  "google" — that we split on `:` for per-provider rollup in the UI. */
  ingest_sources?: {
    cycle?: number;
    ts?: string;
    sources?: Record<string, { jobs: number; errors: number }>;
  };
  /** Slugs that returned 404 last cycle. Indicates scraper config rot. */
  dead_slugs?: { source: string; slug: string; ts: string }[];
}
