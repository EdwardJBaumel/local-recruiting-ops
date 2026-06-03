import { useEffect, useRef } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useCycleHistory } from "@/hooks/useCycleHistory";
import { useLogs } from "@/hooks/useLogs";
import { useStatus } from "@/hooks/useStatus";
import { displayPath, sanitiseLogLines } from "@/lib/displayPath";

export function History() {
  const history = useCycleHistory(500);
  const logs = useLogs(400);
  const status = useStatus();
  const rows = history.data ?? [];
  const latest = rows[0];
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logs.data?.lines.length) {
      logEndRef.current?.scrollIntoView({ block: "end" });
    }
  }, [logs.data?.lines.length, logs.dataUpdatedAt]);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">History</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Cycle timeline from <span className="font-mono">cycle_times.json</span> and live pipeline log tail.
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>Cycle timeline</CardTitle>
              <CardDescription>
                {latest ? `Latest: cycle #${latest.cycle} at ${formatTimestamp(latest.ts)}` : "No cycles recorded yet"}
              </CardDescription>
            </div>
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

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">Pipeline log</CardTitle>
              <CardDescription>
                {logs.data?.exists
                  ? `Tail of ${displayPath(logs.data.path)}`
                  : "Log file not found yet — run a cycle to create logs/lro.log"}
              </CardDescription>
            </div>
            {status.data?.cycle_in_progress && (
              <Badge variant="secondary">{status.data.progress?.stage_label ?? "Running"}</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {logs.isError ? (
            <p className="text-sm text-destructive">{logs.error.message}</p>
          ) : !logs.data?.exists ? (
            <p className="text-sm text-muted-foreground">No log output yet.</p>
          ) : (
            <pre className="max-h-72 overflow-auto rounded-md border bg-secondary/30 p-3 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all">
              {sanitiseLogLines(logs.data.lines ?? []).join("\n")}
              <div ref={logEndRef} />
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
  );
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
