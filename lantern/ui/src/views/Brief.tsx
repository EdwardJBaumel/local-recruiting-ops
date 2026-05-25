import { lazy, Suspense, useMemo } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useStatus } from "@/hooks/useStatus";
import { useMatches } from "@/hooks/useMatches";
import { useMarket } from "@/hooks/useMarket";
import { useConfig } from "@/hooks/useConfig";
import { SourceHealth } from "@/components/SourceHealth";
import { SkillGap } from "@/components/SkillGap";
import { LastCycleSummary } from "@/components/LastCycleSummary";
import type { MatchPayload } from "@/types/match";
import type { GhostRatePoint, SalaryBucket } from "@/components/BriefCharts";

// Recharts is ~90 KB gzipped (d3-scale + d3-shape + friends). We
// only need it on the Brief tab, so we code-split it. After Vite
// hoists the module, the lazy() calls below share ONE chunk — the
// first import pulls Recharts down, the rest resolve from the
// module cache without a second round-trip.
const TopHiringChart = lazy(() => import("@/components/TopHiringChart"));
// React.lazy demands a default export. Our chart module exports each
// chart by name (so the file is greppable), so the .then() shim
// rewrites each named export into a default. Vite still hoists them
// to one chunk because they all import the same source path.
const GhostRateTrend = lazy(() =>
  import("@/components/BriefCharts").then((m) => ({ default: m.GhostRateTrend })),
);
const SalaryDistribution = lazy(() =>
  import("@/components/BriefCharts").then((m) => ({ default: m.SalaryDistribution })),
);

/**
 * Brief tab — at-a-glance market read + skill / market metrics.
 *
 * Reads from THREE server caches:
 *   - useStatus():  the heartbeat. Every 2s. Drives the metric tiles.
 *   - useMarket():  per-cycle aggregates. Refreshes only on cycle-end.
 *   - useMatches(): the match registry. Same cycle-gated polling.
 *
 * Notice we don't `useState` anything here — TanStack Query owns the
 * fetched data. Brief is a pure read view.
 */
export function Brief() {
  const status = useStatus();
  const market = useMarket();
  const matches = useMatches();
  // useConfig powers the salary distribution's "your target" line.
  // Pulled in here next to the other server-state hooks so all hook
  // calls live at the top of the component, per the rules-of-hooks
  // style we use everywhere else in the codebase.
  const config = useConfig();

  // Latest market entry. Backend writes one per cycle, newest at the end.
  const latest = market.data?.[market.data.length - 1];

  // Top-5 companies by ingested-job count this cycle. Recharts wants
  // an array of objects; we turn the {company: count} map into
  // [{name, count}]. Field name on the wire is `company_volume`
  // (not `by_company` — that was a type-mismatch bug that left this
  // panel permanently empty).
  const topCompanies = Object.entries(latest?.company_volume ?? {})
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);

  // Total ingested this cycle = sum across all sources. The backend
  // doesn't write a `total_jobs` field; sum is the source of truth.
  const totalIngested = Object.values(latest?.source_breakdown ?? {})
    .reduce((acc, n) => acc + (typeof n === "number" ? n : 0), 0);

  // Top-5 archetypes — backend's market_intel doesn't bucket by
  // archetype, but each match payload carries `archetype` /
  // `archetype_label`. Aggregate client-side from the matches array
  // so this panel actually shows what's hitting our funnel rather
  // than waiting on a backend feature that doesn't exist yet.
  const topArchetypes = (() => {
    const counts: Record<string, { count: number; label: string }> = {};
    for (const m of matches.data ?? []) {
      if (m._removed || m._dismissed) continue;
      const key = m.archetype ?? "uncategorised";
      const label = m.archetype_label ?? key;
      const slot = counts[key] ?? { count: 0, label };
      slot.count += 1;
      counts[key] = slot;
    }
    return Object.entries(counts)
      .map(([key, { count, label }]) => ({ key, name: label, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 5);
  })();

  // Pull the salary floor for the histogram reference line. We use it
  // as a "your target" marker — it's the same number the backend uses
  // to penalise low-paying roles, so showing it on the chart is
  // self-consistent with the scoring elsewhere.
  const targetSalaryUsd = config.data?.preferences?.salary_floor_usd;

  // Chart data is computed client-side from existing payloads.
  // useMemo because the inputs (matches.data) are stable references
  // between cycle-end refetches — the heavy reduce loops only run
  // when the data actually changes, not on every parent re-render.
  const ghostTrend = useMemo(() => buildGhostTrend(matches.data ?? []), [matches.data]);
  const salaryBuckets = useMemo(() => buildSalaryBuckets(matches.data ?? []), [matches.data]);

  return (
    <div className="space-y-6">
      <MetricStrip />
      <LastCycleSummary />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Top hiring companies</CardTitle>
            <CardDescription>
              {latest
                ? `Cycle #${latest.cycle} · ${totalIngested} postings ingested`
                : "Run a cycle to populate market data"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {topCompanies.length > 0 ? (
              // Suspense fallback height = chart height so the card
              // doesn't reflow when Recharts streams in. After the
              // first chart loads, every other chart on the page
              // resolves instantly because Recharts is already in
              // the module cache.
              <Suspense
                fallback={<div className="h-64 flex items-center justify-center text-xs text-muted-foreground">Loading chart…</div>}
              >
                <TopHiringChart data={topCompanies} />
              </Suspense>
            ) : (
              <p className="text-sm text-muted-foreground py-12 text-center">
                {status.isLoading ? "Loading…" : "No market data yet."}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Top archetypes</CardTitle>
            <CardDescription>What kind of PM roles are dominating</CardDescription>
          </CardHeader>
          <CardContent>
            {topArchetypes.length > 0 ? (
              <ul className="space-y-3">
                {topArchetypes.map((a) => (
                  <li key={a.key} className="flex items-center justify-between gap-3">
                    <span className="text-sm capitalize truncate">{a.name.replace(/_/g, " ")}</span>
                    <span className="text-sm font-mono tabular-nums text-muted-foreground">
                      {a.count}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground py-6 text-center">No archetype data yet.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Skill gap is the most actionable card on the page — it tells
          the user where their resume keeps hitting and where it keeps
          missing across every analysed match. Goes full-width above
          the salary / ghost-rate pair so it gets visual prominence. */}
      <SkillGap />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Salary distribution</CardTitle>
            <CardDescription>
              {targetSalaryUsd
                ? `Midpoint of every posted comp band, $20k bins. Dashed line marks your floor of $${Math.round(targetSalaryUsd / 1000)}k.`
                : "Midpoint of every posted comp band, $20k bins."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Suspense
              fallback={<div className="h-56 flex items-center justify-center text-xs text-muted-foreground">Loading chart…</div>}
            >
              <SalaryDistribution data={salaryBuckets} targetUsd={targetSalaryUsd} />
            </Suspense>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Ghost rate trend</CardTitle>
            <CardDescription>
              Weekly share of postings flagged as suspect by the freshness detector. A rising line
              means more stale reqs in the firehose.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Suspense
              fallback={<div className="h-56 flex items-center justify-center text-xs text-muted-foreground">Loading chart…</div>}
            >
              <GhostRateTrend data={ghostTrend} />
            </Suspense>
          </CardContent>
        </Card>
      </div>

      <SourceHealth />
    </div>
  );
}

// =============================================================================
// CHART DATA BUILDERS
// =============================================================================
// Pure functions that take the matches array Brief already holds and
// return the data shape each chart wants. Memoised in the caller so
// the heavy reduce loops only run on real data changes. They live
// here (not in BriefCharts.tsx) so the chart module stays pure
// presentation code — Brief owns "what data to feed in".
// =============================================================================

/**
 * Bucket matches by ISO week (`_first_seen_at`) and compute the
 * fraction flagged suspect per bucket. We use 7-day windows anchored
 * at Monday because the rolling weekly cadence matches how a recruiter
 * thinks about reqs aging out.
 *
 * The `n` field in each output point feeds the tooltip — a 50% ghost
 * rate from n=2 is noise; from n=40 it's a story. Surfacing the
 * sample size keeps the chart honest.
 */
function buildGhostTrend(matches: MatchPayload[]): GhostRatePoint[] {
  // Bucket key = ISO date of the Monday of the week. Stable across
  // weekday boundaries and DST without us doing time-zone math.
  const buckets = new Map<string, { suspect: number; total: number }>();
  for (const m of matches) {
    if (m._removed) continue;
    const seen = m._first_seen_at;
    if (!seen) continue;
    const d = new Date(seen);
    if (Number.isNaN(d.getTime())) continue;
    // Snap to Monday 00:00 UTC of that week.
    const day = d.getUTCDay(); // 0 = Sunday
    const offsetToMon = day === 0 ? -6 : 1 - day;
    const monday = new Date(d);
    monday.setUTCDate(d.getUTCDate() + offsetToMon);
    monday.setUTCHours(0, 0, 0, 0);
    const key = monday.toISOString().slice(0, 10);
    const slot = buckets.get(key) ?? { suspect: 0, total: 0 };
    slot.total += 1;
    if ((m._fake?.score ?? 0) >= 0.45) slot.suspect += 1;
    buckets.set(key, slot);
  }
  // Sort weeks chronologically so the line moves left → right.
  const sorted = Array.from(buckets.entries()).sort(([a], [b]) => a.localeCompare(b));
  return sorted.map(([week, { suspect, total }]) => {
    const monday = new Date(week);
    const label = monday.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return {
      week,
      label,
      pct: total > 0 ? Math.round((suspect / total) * 100) : 0,
      n: total,
    };
  });
}

/**
 * Histogram salary midpoints across the registry into $20k bins from
 * $80k to $300k. Most posted bands fall in this range; we don't have
 * enough <$80k or >$300k samples for the tails to matter, so we cap
 * the axis there and lump out-of-range rows into the nearest edge bin.
 *
 * Why midpoints, not min or max
 * -----------------------------
 * Backend writes `{min, max, currency}`. A min-only point understates
 * the band; a max-only overstates. The midpoint is the simplest
 * unbiased single number. If max is missing we fall back to min so we
 * still get a reading on partial data.
 */
const SALARY_BIN_SIZE = 20_000;
const SALARY_LOW = 80_000;
const SALARY_HIGH = 300_000;

function buildSalaryBuckets(matches: MatchPayload[]): SalaryBucket[] {
  const counts = new Map<number, number>();
  for (const m of matches) {
    if (m._removed) continue;
    const min = m.salary?.min;
    const max = m.salary?.max;
    if (min == null && max == null) continue;
    const mid = min != null && max != null ? (min + max) / 2 : min ?? max ?? 0;
    if (!mid || mid < 1000) continue; // junk row — likely a parse miss
    // Clamp out-of-range values to the edge bins rather than dropping
    // them — losing them entirely makes the histogram look thinner
    // than reality.
    const clamped = Math.max(SALARY_LOW, Math.min(SALARY_HIGH - 1, mid));
    const lo = Math.floor((clamped - SALARY_LOW) / SALARY_BIN_SIZE) * SALARY_BIN_SIZE + SALARY_LOW;
    counts.set(lo, (counts.get(lo) ?? 0) + 1);
  }
  // Emit a row for every bin in the range, even empty ones, so the
  // X-axis stays evenly spaced. An empty middle bin tells a story
  // ("comp clusters bimodally") that you'd lose by hiding zeros.
  const out: SalaryBucket[] = [];
  for (let lo = SALARY_LOW; lo < SALARY_HIGH; lo += SALARY_BIN_SIZE) {
    out.push({
      lo,
      label: `$${lo / 1000}k`,
      count: counts.get(lo) ?? 0,
    });
  }
  // If the entire histogram is empty (no salaries on the wire yet),
  // return [] so the chart shows the "no data" placeholder rather than
  // a row of zero bars.
  return out.some((b) => b.count > 0) ? out : [];
}

/**
 * Metric tiles — top of the Brief page. Live-updates from /api/status
 * every 2s. Five tiles is the sweet spot: any more and the eye gets
 * lost; any fewer and the strip looks empty.
 */
function MetricStrip() {
  const status = useStatus();
  const matches = useMatches();
  const s = status.data;

  // Current ghost rate — share of registry rows the fake-detector
  // flags "suspect" (ghost score >= 0.45, the same cutoff
  // GhostBadgePill uses). Shown as a live number because the "Ghost
  // rate trend" card below needs multiple weeks of data before it
  // says anything — this tile is useful from cycle 1.
  const ghostRate = (() => {
    const rows = (matches.data ?? []).filter((m) => !m._removed);
    if (rows.length === 0) return null;
    const suspect = rows.filter((m) => (m._fake?.score ?? 0) >= 0.45).length;
    return Math.round((suspect / rows.length) * 100);
  })();

  // Match rate — share of fetched postings that passed the score
  // threshold on the last run (matches / ingested). More useful than
  // per-row embedding latency for day-to-day job search.
  const matchRate = (() => {
    const inProgress = !!s?.cycle_in_progress;
    const ingested = inProgress
      ? num(s?.progress?.counts?.ingested)
      : s?.last_cycle?.ingested;
    const matched = inProgress
      ? num(s?.progress?.counts?.matches)
      : s?.last_cycle?.matches;
    if (ingested == null || matched == null || ingested <= 0) return null;
    return {
      pct: Math.round((matched / ingested) * 100),
      matched,
      ingested,
      live: inProgress,
    };
  })();

  const tiles: { label: string; value: string; hint?: string }[] = [
    {
      label: "Matches in registry",
      value: s?.matches_count != null ? String(s.matches_count) : "—",
    },
    {
      label: "Cycles run",
      value: s?.cycles_recorded != null ? String(s.cycles_recorded) : "—",
    },
    {
      label: "Ghost rate",
      value: ghostRate != null ? `${ghostRate}%` : "—",
      hint: "Flagged suspect",
    },
    {
      label: "Avg scrape",
      value: formatDuration(s?.avg_scrape_seconds),
      hint: "Last 10 cycles",
    },
    {
      label: "Avg pipeline",
      value: formatDuration(s?.avg_pipeline_seconds),
      hint: "Last 10 cycles",
    },
    {
      label: "Match rate",
      value: matchRate != null ? `${matchRate.pct}%` : "—",
      hint: matchRate != null
        ? `${matchRate.matched} of ${matchRate.ingested} ${matchRate.live ? "this cycle" : "last cycle"}`
        : "Run a cycle",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
      {tiles.map((t) => (
        <Card key={t.label}>
          <CardContent className="pt-6">
            <div className="text-xs text-muted-foreground uppercase tracking-wider">{t.label}</div>
            <div className="text-2xl font-semibold mt-1 font-mono tabular-nums">{t.value}</div>
            {t.hint && <div className="text-xs text-muted-foreground mt-1">{t.hint}</div>}
          </CardContent>
        </Card>
      ))}
      {/* Model fallback banner — only renders when something's missing,
          stays invisible at the healthy steady state. */}
      <FallbackBanner />
    </div>
  );
}

/**
 * Format a duration in seconds for display in metric tiles.
 *   < 60 s   → "37s"
 *   < 1 h    → "4m 23s"
 *   ≥ 1 h    → "1h 0m"
 *
 * Long pipelines on cold caches can exceed an hour, and "3623s" is
 * unreadable at a glance — humans think in minutes for anything past
 * a couple of minutes.
 */
function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return rem === 0 ? `${m}m` : `${m}m ${rem}s`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

function num(v: unknown): number | undefined {
  return typeof v === "number" ? v : undefined;
}

function FallbackBanner() {
  const status = useStatus();
  const subs = status.data?.model_fallback?.substitutes ?? {};
  const entries = Object.entries(subs);
  if (entries.length === 0) return null;
  return (
    <Card className="col-span-full border-destructive/50">
      <CardContent className="pt-4 flex items-start gap-3">
        <Badge variant="destructive" className="mt-0.5">FALLBACK</Badge>
        <div className="text-xs space-y-1">
          {entries.map(([missing, sub]) => (
            <div key={missing} className="font-mono">
              <span className="text-destructive">{missing}</span> →{" "}
              <span className="text-accent">{sub}</span>
            </div>
          ))}
          <div className="text-muted-foreground">
            Pull the missing model with <code>ollama pull {entries[0]![0]}</code> or change it in Settings.
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
