import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useStatus } from "@/hooks/useStatus";
import { AlertTriangle, CheckCircle2, Activity } from "lucide-react";

/**
 * SourceHealth — at-a-glance view of what each scraper produced last
 * cycle, plus any tenants that 404'd. Answers the question "are my
 * scrapers actually working?" without you having to tail the logs.
 *
 * Wire shape from `/api/status.ingest_sources`:
 *
 *   { "cycle": 1, "ts": "...",
 *     "sources": {
 *       "greenhouse:stripe": { "jobs": 29, "errors": 0 },
 *       "amazon":            { "jobs": 31, "errors": 0 },
 *       ...
 *     }
 *   }
 *
 * Keys are flat "provider:slug" strings (or just "provider" for big-tech
 * sources that don't have tenant slugs like Amazon/Google). We split on
 * `:` for per-provider rollup. Values are {jobs, errors} — the {jobs} count
 * is what we treat as "did this source actually produce anything."
 */
export function SourceHealth() {
  const status = useStatus();
  const sources = status.data?.ingest_sources?.sources ?? {};
  const dead = status.data?.dead_slugs ?? [];

  // Roll up per-provider. Each key like "greenhouse:stripe" splits to
  // [provider, tenant]. Keys without a `:` (e.g. "amazon") are their own
  // provider with a single virtual tenant.
  type Tenant = { slug: string; jobs: number; errors: number };
  const byProvider = new Map<string, Tenant[]>();
  for (const [key, value] of Object.entries(sources)) {
    const [provider, tenant] = key.includes(":") ? key.split(":", 2) : [key, key];
    const list = byProvider.get(provider!) ?? [];
    list.push({
      slug: tenant ?? provider!,
      jobs: typeof value?.jobs === "number" ? value.jobs : 0,
      errors: typeof value?.errors === "number" ? value.errors : 0,
    });
    byProvider.set(provider!, list);
  }

  const totals = [...byProvider.entries()]
    .map(([provider, tenants]) => {
      const totalJobs = tenants.reduce((s, t) => s + t.jobs, 0);
      const productive = tenants.filter((t) => t.jobs > 0).length;
      return {
        provider,
        tenants: [...tenants].sort((a, b) => b.jobs - a.jobs),
        totalJobs,
        productive,
        total: tenants.length,
      };
    })
    .sort((a, b) => b.totalJobs - a.totalJobs);

  const hasData = totals.length > 0;
  const hasDead = dead.length > 0;

  // Group dead slugs by source for tidy display.
  const deadBySource = dead.reduce<Record<string, string[]>>((acc, d) => {
    (acc[d.source] ??= []).push(d.slug);
    return acc;
  }, {});

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="h-5 w-5" />
          Scraper health
        </CardTitle>
        <CardDescription>
          Last-cycle counts per source. Zero usually means the source returned no roles matching your keywords —
          not necessarily that the scraper failed.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!hasData && !hasDead && (
          // Note: this is a <div>, NOT a <p>. Badge renders a <div>
          // internally, and HTML doesn't allow <div> inside <p>. The
          // resulting hydration error was previously trying to surface
          // through a render-blocking exception.
          <div className="text-sm text-muted-foreground py-2 flex items-center gap-2">
            <span>No source data yet. Click</span>
            <Badge variant="outline">Run Pipeline</Badge>
            <span>to populate.</span>
          </div>
        )}

        {hasData &&
          totals.map((p) => (
            <div key={p.provider} className="space-y-1.5">
              <div className="flex items-baseline justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium capitalize">{p.provider}</span>
                  <Badge variant="outline" className="text-[10px] font-mono">
                    {p.productive}/{p.total} producing
                  </Badge>
                </div>
                <span className="text-sm font-mono tabular-nums text-accent font-semibold">
                  {p.totalJobs} jobs
                </span>
              </div>
              <div className="flex flex-wrap gap-1">
                {p.tenants.slice(0, 8).map((t) => {
                  const isErr = t.errors > 0;
                  const isProd = t.jobs > 0;
                  return (
                    <Badge
                      key={t.slug}
                      variant={isErr ? "suspect" : isProd ? "secondary" : "outline"}
                      className={`text-[10px] font-mono ${!isProd && !isErr ? "opacity-50" : ""}`}
                      title={
                        isErr
                          ? `${t.slug}: ${t.errors} error${t.errors === 1 ? "" : "s"} last cycle`
                          : `${t.slug}: ${t.jobs} matching job${t.jobs === 1 ? "" : "s"} last cycle`
                      }
                    >
                      {t.slug} · {t.jobs}
                    </Badge>
                  );
                })}
                {p.tenants.length > 8 && (
                  <Badge variant="outline" className="text-[10px] font-mono opacity-60">
                    +{p.tenants.length - 8} more
                  </Badge>
                )}
              </div>
            </div>
          ))}

        {hasDead && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium text-destructive">
              <AlertTriangle className="h-4 w-4" />
              {dead.length} tenant{dead.length === 1 ? "" : "s"} returned 404
            </div>
            <p className="text-xs text-muted-foreground">
              These slugs are wrong, or the company moved off that ATS. Remove them from your config to stop burning
              cycles on dead endpoints.
            </p>
            {Object.entries(deadBySource).map(([src, slugs]) => (
              <div key={src} className="text-xs">
                <span className="font-mono uppercase text-muted-foreground">{src}:</span>{" "}
                {slugs.map((s, i) => (
                  <span key={s} className="font-mono">
                    {s}
                    {i < slugs.length - 1 ? ", " : ""}
                  </span>
                ))}
              </div>
            ))}
          </div>
        )}

        {hasData && !hasDead && (
          <div className="text-xs text-ghost-clear flex items-center gap-1.5">
            <CheckCircle2 className="h-4 w-4" />
            All configured sources reachable.
          </div>
        )}
      </CardContent>
    </Card>
  );
}
