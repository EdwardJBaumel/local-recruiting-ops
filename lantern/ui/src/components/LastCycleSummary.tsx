import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useStatus } from "@/hooks/useStatus";
import type { LastCycleStats } from "@/types/status";
import { Loader2 } from "lucide-react";

/**
 * Brief-tab summary of the last pipeline run.
 *
 * These counts are not one strict funnel — "matched" is scored against your
 * profile, "new" is first-seen in the registry, "analysed" is top-N LLM
 * fit/gap. We label them plainly instead of chaining arrows that imply
 * every step should shrink.
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
        fake_blocked: num(liveCounts.fake_blocked) ?? last?.fake_blocked,
        new_jobs: num(liveCounts.new_jobs) ?? last?.new_jobs,
        matches: num(liveCounts.matches) ?? last?.matches,
        fit_gaps: num(liveCounts.fit_gaps) ?? last?.fit_gaps,
      }
    : last ?? null;

  const rows = buildSummaryRows(stats);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">Last cycle</CardTitle>
            <CardDescription>
              {inProgress
                ? "Live counts while the pipeline runs"
                : stats?.cycle != null
                  ? `Cycle #${stats.cycle} · what changed on the last run`
                  : "Run a cycle to see fetch → match → analyse counts"}
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
        {rows.length > 0 ? (
          <dl className="grid gap-2 sm:grid-cols-2">
            {rows.map((row) => (
              <div
                key={row.label}
                className="flex items-baseline justify-between gap-3 rounded-md border bg-muted/40 px-3 py-2"
              >
                <div className="min-w-0">
                  <dt className="text-sm font-medium">{row.label}</dt>
                  {row.hint && (
                    <dd className="text-[11px] text-muted-foreground leading-snug mt-0.5">{row.hint}</dd>
                  )}
                </div>
                <dd className="font-mono tabular-nums text-lg font-semibold shrink-0">{row.value}</dd>
              </div>
            ))}
          </dl>
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

interface SummaryRow {
  label: string;
  value: string;
  hint?: string;
}

function buildSummaryRows(stats: LastCycleStats | null | undefined): SummaryRow[] {
  if (!stats) return [];

  const rows: SummaryRow[] = [
    {
      label: "Fetched",
      value: fmt(stats.ingested),
      hint: "Postings pulled from configured sources",
    },
  ];

  if ((stats.fake_blocked ?? 0) > 0) {
    rows.push({
      label: "Ghosts removed",
      value: fmt(stats.fake_blocked),
      hint: "Likely fake or stale listings dropped",
    });
  }

  if ((stats.qa_fail ?? 0) > 0) {
    rows.push({
      label: "Failed QA",
      value: fmt(stats.qa_fail),
      hint: "Malformed cards rejected before scoring",
    });
  }

  rows.push(
    {
      label: "Matched your profile",
      value: fmt(stats.matches),
      hint: "Passed your score threshold this run",
    },
    {
      label: "New listings",
      value: fmt(stats.new_jobs),
      hint: "First time seen in your registry",
    },
    {
      label: "Top matches analysed",
      value: fmt(stats.fit_gaps),
      hint: "LLM fit/gap breakdown on your highest-scoring roles",
    },
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
