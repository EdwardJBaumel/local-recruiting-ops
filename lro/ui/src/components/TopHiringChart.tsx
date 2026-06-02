import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip } from "recharts";

/**
 * Horizontal bar chart used by the Brief tab to show "Top hiring
 * companies this cycle".
 *
 * Why it's a separate file
 * ------------------------
 * Recharts pulls in d3-scale, d3-shape and friends — about 90 KB
 * gzipped that we don't want in the initial bundle. Putting the
 * chart in its own module lets `Brief.tsx` lazy-load it via
 * `React.lazy(() => import('./TopHiringChart'))`, splitting the
 * Recharts subtree into a chunk fetched only when the Brief tab
 * mounts.
 *
 * The component is intentionally thin: parent owns the data shape
 * and decides when to render — this file is purely the chart.
 */
export interface TopHiringDatum {
  name: string;
  count: number;
}

interface Props {
  data: TopHiringDatum[];
}

export default function TopHiringChart({ data }: Props) {
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ left: 80 }}>
          <XAxis type="number" stroke="hsl(var(--muted-foreground))" fontSize={12} />
          <YAxis
            type="category"
            dataKey="name"
            stroke="hsl(var(--muted-foreground))"
            fontSize={12}
            width={80}
          />
          <Tooltip
            contentStyle={{
              background: "hsl(var(--popover))",
              border: "1px solid hsl(var(--border))",
              borderRadius: "0.375rem",
              fontSize: "12px",
            }}
          />
          <Bar dataKey="count" fill="hsl(var(--accent))" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
