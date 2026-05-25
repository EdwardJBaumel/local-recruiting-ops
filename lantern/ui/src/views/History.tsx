import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useCycleHistory, type CycleHistoryEntry } from "@/hooks/useCycleHistory";
import { useStatus } from "@/hooks/useStatus";

export function History() {
  const history = useCycleHistory(500);
  const status = useStatus();
  const rows = history.data ?? [];
  const latest = rows[0];
  const totalCycles = status.data?.cycles_recorded ?? rows.length;
  const totals = summarise(rows);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">History</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Long-run cycle timeline from <span className="font-mono">cycle_times.json</span>. Charts use this and market history,
          not the raw match snapshot folders.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Metric label="Cycles stored" value={String(totalCycles)} />
        <Metric label="Showing" value={String(rows.length)} hint="Newest first" />
        <Metric label="Avg total" value={formatDuration(totals.avgSeconds)} hint="Shown cycles" />
        <Metric label="Avg matches" value={totals.avgMatches != null ? totals.avgMatches.toFixed(1) : "—"} hint="Shown cycles" />
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>Cycle Timeline</CardTitle>
              <CardDescription>
                {latest ? `Latest: cycle #${latest.cycle} at ${formatTimestamp(latest.ts)}` : "No cycles recorded yet"}
              </CardDescription>
            </div>
            {status.data?.cycle_in_progress && (
              <Badge variant="secondary">{status.data.progress?.stage_label ?? "Running"}</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {history.isError ? (
            <p className="text-sm text-destructive">{history.error.message}</p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">Run a cycle to start building history.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="text-left py-2 pr-4">Cycle</th>
                    <th className="text-left py-2 pr-4">When</th>
                    <th className="text-right py-2 px-3">Total</th>
                    <th className="text-right py-2 px-3">Scrape</th>
                    <th className="text-right py-2 px-3">Pipeline</th>
                    <th className="text-right py-2 px-3">Ingested</th>
                    <th className="text-right py-2 px-3">New</th>
                    <th className="text-right py-2 px-3">Matches</th>
                    <th className="text-right py-2 pl-3">Fit gaps</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c, i) => (
                    <tr key={`${c.cycle}-${c.ts}-${i}`} className="border-b last:border-0">
                      <td className="py-2 pr-4 font-mono tabular-nums">{c.cycle}</td>
                      <td className="py-2 pr-4 text-muted-foreground whitespace-nowrap">{formatTimestamp(c.ts)}</td>
                      <td className="py-2 px-3 text-right font-mono tabular-nums">{formatDuration(c.seconds)}</td>
                      <td className="py-2 px-3 text-right font-mono tabular-nums">{formatDuration(c.ingest_seconds)}</td>
                      <td className="py-2 px-3 text-right font-mono tabular-nums">{formatDuration(c.pipeline_seconds)}</td>
                      <td className="py-2 px-3 text-right font-mono tabular-nums">{fmt(c.ingested)}</td>
                      <td className="py-2 px-3 text-right font-mono tabular-nums">{fmt(c.new_jobs)}</td>
                      <td className="py-2 px-3 text-right font-mono tabular-nums font-semibold">{fmt(c.matches)}</td>
                      <td className="py-2 pl-3 text-right font-mono tabular-nums">{fmt(c.fit_gaps)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="text-xs text-muted-foreground uppercase tracking-wider">{label}</div>
        <div className="text-2xl font-semibold mt-1 font-mono tabular-nums">{value}</div>
        {hint && <div className="text-xs text-muted-foreground mt-1">{hint}</div>}
      </CardContent>
    </Card>
  );
}

function summarise(rows: CycleHistoryEntry[]) {
  const seconds = rows.map((r) => r.seconds).filter((n): n is number => typeof n === "number");
  const matches = rows.map((r) => r.matches).filter((n): n is number => typeof n === "number");
  return {
    avgSeconds: seconds.length ? seconds.reduce((a, b) => a + b, 0) / seconds.length : null,
    avgMatches: matches.length ? matches.reduce((a, b) => a + b, 0) / matches.length : null,
  };
}

function fmt(n: number | undefined): string {
  return n != null ? String(n) : "—";
}

function formatTimestamp(ts: string | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts.replace("T", " ").slice(0, 19);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
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
