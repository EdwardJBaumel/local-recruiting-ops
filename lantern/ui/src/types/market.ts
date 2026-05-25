/**
 * Mirrors GET /api/market response. Backend writes one entry per
 * cycle to data/market_intel.json (the actual on-disk shape — verified
 * Apr 2026 against a real cycle dump).
 *
 * Field names match orchestrator._save_market_intel in orchestrator.py
 * exactly. Past versions of this type used invented names
 * (`by_company`, `by_archetype`, `ts`) that didn't exist on the wire,
 * which made the Brief view's top-companies and archetype panels
 * silently empty. Don't add fields here without checking the actual
 * JSON output.
 */
export interface MarketCycleEntry {
  timestamp: string;
  cycle: number;
  /** {Company display name → count of postings ingested this cycle}. */
  company_volume?: Record<string, number>;
  /** {source slug ("greenhouse"/"lever"/...) → count of packets emitted}. */
  source_breakdown?: Record<string, number>;
  /** Up to N truncated salary-line excerpts seen this cycle. */
  salary_samples?: string[];
  /** Remote/hybrid/onsite/unknown split. */
  work_model?: Record<string, number>;
  /** Seniority bucket counts (junior/mid/senior/staff/director/...). */
  seniority_distribution?: Record<string, number>;
}
