import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * Brief tab — Recharts-based metric panels (ghost rate trend + salary
 * distribution).
 *
 * Why one file for both charts
 * ----------------------------
 * Recharts pulls in d3-scale + d3-shape (~90 KB gzipped). We code-split
 * the whole subtree so the charts only load when the Brief tab actually
 * mounts. Putting both charts in one module means the user pays for
 * the Recharts download exactly once — every chart on the page
 * resolves from the same chunk.
 *
 * Wire format
 * -----------
 * Each component takes a fully prepared `data` array. Computation lives
 * in Brief.tsx where the source hooks (useMatches, useMarket) are
 * already wired. That keeps the chart components dumb and easy to swap.
 *
 * Tooltip / colour styling
 * ------------------------
 * Every Tooltip uses the same CSS-variable styling so the chart chrome
 * matches the surrounding cards in dark and light mode. The accent
 * colour is the app's saffron (--accent) and the muted text uses
 * --muted-foreground so axis labels stay readable in both themes.
 */

const TOOLTIP_STYLE = {
  background: "hsl(var(--popover))",
  border: "1px solid hsl(var(--border))",
  borderRadius: "0.375rem",
  fontSize: "12px",
} as const;

// -----------------------------------------------------------------------------
// GHOST RATE OVER TIME
// -----------------------------------------------------------------------------
// What "ghost rate" means here
// ----------------------------
// The ghost detector tags each match with a freshness verdict (clear /
// aging / suspect). Ghost rate at week W is suspectCount / totalSeen
// for matches whose `_first_seen_at` falls inside that week. A rising
// ghost rate over time signals that the firehose is being polluted by
// stale postings — a real-world maintenance signal a recruiter would
// recognise.
//
// Why a 0–60% Y-axis ceiling
// --------------------------
// In practice ghost rate sits in the 5–25% band. Letting Recharts
// auto-scale 0–100 makes every line look flat near the bottom of the
// chart. Capping the Y axis at 60 keeps small movements visible without
// being misleading (60 is well above the typical worst-case of ~30%).
// -----------------------------------------------------------------------------

export interface GhostRatePoint {
  /** ISO date for the start of the bucket (week). */
  week: string;
  /** Display label, e.g. "Apr 21". */
  label: string;
  /** 0..100 — percentage of suspect rows in the bucket. */
  pct: number;
  /** Sample size for the bucket — surfaced in the tooltip. */
  n: number;
}

export function GhostRateTrend({ data }: { data: GhostRatePoint[] }) {
  if (data.length === 0) {
    return (
      <div className="h-56 flex items-center justify-center text-xs text-muted-foreground">
        Run a cycle to populate — one point plots after the first, a trend builds over weeks.
      </div>
    );
  }
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis dataKey="label" stroke="hsl(var(--muted-foreground))" fontSize={11} />
          <YAxis
            stroke="hsl(var(--muted-foreground))"
            fontSize={11}
            domain={[0, 60]}
            tickFormatter={(v) => `${v}%`}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value, _name, item) => [
              `${value}% (n=${(item as { payload?: GhostRatePoint })?.payload?.n ?? 0})`,
              "Ghost rate",
            ]}
          />
          <Line
            type="monotone"
            dataKey="pct"
            stroke="hsl(var(--accent))"
            strokeWidth={2}
            dot={{ r: 3, fill: "hsl(var(--accent))" }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// -----------------------------------------------------------------------------
// SALARY DISTRIBUTION
// -----------------------------------------------------------------------------
// Source data
// -----------
// Each match's `salary` field is `{min, max, currency}`. We compute the
// midpoint (`(min+max)/2`, or `min` if max is missing) and bucket into
// $20k bins. Postings without a salary are simply absent from the
// histogram — we never invent a number.
//
// Target reference line
// ---------------------
// If the user has set a `target_salary_usd` in their profile, a dashed
// vertical line marks it. Recruiters can see at a glance how many
// roles in the dataset hit the user's number — a much sharper read
// than "average salary is $X".
//
// Why $20k buckets
// ----------------
// Tested $10k and $20k. $10k creates a too-jittery histogram on small
// sample sizes (jobs cluster at round numbers like $150k / $160k, so
// bucket counts swing wildly). $20k smooths the noise without losing
// the shape.
// -----------------------------------------------------------------------------

export interface SalaryBucket {
  /** Lower edge of the bucket in USD (e.g. 140000 means $140k–$160k). */
  lo: number;
  /** Display label for the X-axis, e.g. "$140k". */
  label: string;
  count: number;
}

export function SalaryDistribution({
  data,
  targetUsd,
}: {
  data: SalaryBucket[];
  targetUsd?: number;
}) {
  if (data.length === 0) {
    return (
      <div className="h-56 flex items-center justify-center text-xs text-muted-foreground">
        No salary data yet — most postings don't list one.
      </div>
    );
  }
  // Find the bucket label closest to the user's target so the
  // ReferenceLine can snap to a tick that the X axis actually drew.
  // Recharts ReferenceLine's `x` prop on a category axis must equal
  // a real tick — passing a numeric value silently no-ops.
  const targetLabel = (() => {
    if (!targetUsd) return undefined;
    let best = data[0]!;
    let bestDiff = Math.abs(targetUsd - best.lo);
    for (const b of data) {
      const diff = Math.abs(targetUsd - b.lo);
      if (diff < bestDiff) {
        best = b;
        bestDiff = diff;
      }
    }
    return best.label;
  })();
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
          <XAxis dataKey="label" stroke="hsl(var(--muted-foreground))" fontSize={11} />
          <YAxis stroke="hsl(var(--muted-foreground))" fontSize={11} allowDecimals={false} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value, _name, item) => [
              `${value} role${value === 1 ? "" : "s"}`,
              `${(item as { payload?: SalaryBucket })?.payload?.label ?? ""} band`,
            ]}
          />
          {targetLabel && (
            <ReferenceLine
              x={targetLabel}
              stroke="hsl(var(--foreground))"
              strokeDasharray="4 4"
              strokeOpacity={0.6}
              label={{
                value: "Your target",
                position: "top",
                fill: "hsl(var(--muted-foreground))",
                fontSize: 10,
              }}
            />
          )}
          <Bar dataKey="count" fill="hsl(var(--accent))" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
