import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useStatus } from "@/hooks/useStatus";
import type { LastCycleStats } from "@/types/status";
import { ArrowRight, Loader2 } from "lucide-react";

/**
 * Brief-tab synthesis of per-cycle pipeline outputs.
 *
 * On disk: match_registry.json (live union), matches/cycle_*.json
 * (per-run history), cycle_times.json (funnel), market_intel.json.
 */
export function LastCycleSummary() {
  const status = useStatus();
  const s = status.data;
  const inProgress = !!s?.cycle_in_progress;
  const last = s?.last_cycle;
  const liveCounts = inProgress ? s?.progress?.counts : undefined;
  const stage = s?.progress?.stage_label;

  const stats: LastCycleStats | null = inProgress && liveCounts
    ? {
        cycle: s?.progress ? undefined : last?.cycle,
        ingested: num(liveCounts.ingested) ?? last?.ingested,
        parsed: num(liveCounts.parsed) ?? last?.parsed,
        qa_pass: num(liveCounts.qa_pass) ?? last?.qa_pass,
        new_jobs: num(liveCounts.new_jobs) ?? last?.new_jobs,
        matches: num(liveCounts.matches) ?? last?.matches,
        fit_gaps: num(liveCounts.fit_gaps) ?? last?.fit_gaps,
      }
    : last ?? null;

  const steps = buildFunnelSteps(stats);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">Last cycle funnel</CardTitle>
            <CardDescription>
              {inProgress
                ? "Live counts while the pipeline runs"
                : stats?.cycle != null
                  ? `Cycle #${stats.cycle} · synthesised from cycle stats (registry is canonical for matches)`
                  : "Run a cycle to see ingest → match funnel"}
            </CardDescription>
          </div>
          {inProgress && (
            <Badge variant="secondary" className="gap-1.5 shrink-0">
              <Loader2 className="h-3 w-3 animate-spin" />
              {stage ?? "Running"}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {steps.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {steps.map((step, i) => (
              <span key={step.label} className="flex items-center gap-2">
                {i > 0 && <ArrowRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />}
                <span className="rounded-md border bg-muted/40 px-2.5 py-1.5">
                  <span className="text-muted-foreground text-xs uppercase tracking-wide">{step.label}</span>
                  <span className="ml-2 font-mono tabular-nums font-semibold">{step.value}</span>
                </span>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No cycle data yet.</p>
        )}

        {!inProgress && stats && (stats.ingest_seconds != null || stats.duration_seconds != null) && (
          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground font-mono tabular-nums">
            {stats.ingest_seconds != null && (
              <span>Scrape {formatDuration(stats.ingest_seconds)}</span>
            )}
            {stats.pipeline_seconds != null && stats.pipeline_seconds > 0 && (
              <span>Pipeline {formatDuration(stats.pipeline_seconds)}</span>
            )}
            {stats.duration_seconds != null && (
              <span>Total {formatDuration(stats.duration_seconds)}</span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function buildFunnelSteps(stats: LastCycleStats | null | undefined) {
  if (!stats) return [];
  const rows: { label: string; value: string }[] = [
    { label: "Ingested", value: fmt(stats.ingested) },
    { label: "Parsed", value: fmt(stats.parsed) },
    { label: "QA pass", value: fmt(stats.qa_pass) },
  ];
  if ((stats.fake_blocked ?? 0) > 0) {
    rows.push({ label: "Fake blocked", value: fmt(stats.fake_blocked) });
  }
  rows.push(
    { label: "New jobs", value: fmt(stats.new_jobs) },
    { label: "Matches", value: fmt(stats.matches) },
    { label: "Fit gaps", value: fmt(stats.fit_gaps) },
  );
  return rows;
}

function fmt(n: number | undefined): string {
  return n != null ? String(n) : "—";
}

function num(v: unknown): number | undefined {
  return typeof v === "number" ? v : undefined;
}

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
