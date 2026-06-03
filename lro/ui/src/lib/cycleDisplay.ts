import type { LastCycleStats } from "@/types/status";

export function num(v: unknown): number | undefined {
  return typeof v === "number" && !Number.isNaN(v) ? v : undefined;
}

/**
 * Orchestrator zeroes progress.counts at cycle start. Until ingest > 0,
 * keep showing the previous cycle's funnel stats in the UI.
 */
export function liveFunnelActive(
  inProgress: boolean,
  counts: Record<string, number> | undefined,
): boolean {
  if (!inProgress || !counts) return false;
  const ingested = num(counts.ingested);
  return ingested != null && ingested > 0;
}

export function mergeFunnelStats(
  inProgress: boolean,
  live: Record<string, number> | undefined,
  last: LastCycleStats | null | undefined,
): { stats: LastCycleStats | null; showingLive: boolean } {
  const useLive = liveFunnelActive(inProgress, live);
  if (!useLive) {
    return { stats: last ?? null, showingLive: false };
  }
  return {
    stats: {
      cycle: last?.cycle,
      ingested: num(live?.ingested) ?? last?.ingested,
      parsed: num(live?.parsed) ?? last?.parsed,
      qa_pass: num(live?.qa_pass) ?? last?.qa_pass,
      qa_fail: num(live?.qa_fail) ?? last?.qa_fail,
      fake_blocked: num(live?.fake_blocked) ?? last?.fake_blocked,
      new_jobs: num(live?.new_jobs) ?? last?.new_jobs,
      matches: num(live?.matches) ?? last?.matches,
      fit_gaps: num(live?.fit_gaps) ?? last?.fit_gaps,
    },
    showingLive: true,
  };
}

export function matchRateDisplay(
  inProgress: boolean,
  counts: Record<string, number> | undefined,
  lastCycle: LastCycleStats | null | undefined,
): { pct: number; matched: number; ingested: number; live: boolean } | null {
  const useLive = liveFunnelActive(inProgress, counts);
  const ingested = useLive ? num(counts?.ingested) : lastCycle?.ingested;
  const matched = useLive ? num(counts?.matches) : lastCycle?.matches;
  if (ingested == null || matched == null || ingested <= 0) return null;
  return {
    pct: Math.round((matched / ingested) * 100),
    matched,
    ingested,
    live: useLive,
  };
}
