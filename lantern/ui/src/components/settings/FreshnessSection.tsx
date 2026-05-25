import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Clock } from "lucide-react";
import { type Control, Controller } from "react-hook-form";
import type { SettingsFormShape } from "@/views/Settings";

/**
 * Freshness section — three sliders, one per company-size tier.
 *
 * Why three sliders instead of one global "max age"?
 * Hiring velocity differs wildly by company size, and a single
 * window either over-filters big-tech (which posts evergreen reqs)
 * or under-filters startups (where the early-bird advantage is
 * real). Splitting by tier lets the user be aggressive on the
 * fast-moving rows AND tolerant on the slow-moving ones in one go.
 *
 * The TIER itself (which company is "mega" vs "large" vs "growth")
 * is hand-curated client-side in lib/companyTier.ts. The Settings
 * UI exposes the WINDOW VALUES, not the lists — that keeps the
 * config schema clean and the surface small. Lists update via
 * code (a deploy), values update via this form (no deploy).
 *
 * 0 days on any slider = "no age filter for this tier" — useful
 * for users who want to see the full inventory of, e.g., Amazon
 * reqs even when they've been up 90 days.
 */
interface Props {
  control: Control<SettingsFormShape>;
}

export function FreshnessSection({ control }: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Clock className="h-5 w-5" />
          Freshness windows
        </CardTitle>
        <CardDescription>
          How recent a posting has to be to show in the Matches view, by company size. Big tech runs evergreen
          reqs (longer windows make sense); growth-stage hires fast (early-bird matters). Defaults are
          research-backed but tune to taste.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <Controller
          control={control}
          name="freshness_window_mega_days"
          render={({ field }) => (
            <FreshnessSlider
              label="Mega-tier"
              hint="Amazon, Google, Nvidia, Adobe, Intel, plus other public big-tech. These run evergreen reqs that stay open across hiring waves — a 30-day window catches roles that are still genuinely active."
              value={field.value}
              onChange={field.onChange}
            />
          )}
        />
        <Controller
          control={control}
          name="freshness_window_large_days"
          render={({ field }) => (
            <FreshnessSlider
              label="Large-tier"
              hint="Decacorns + public mid-caps (Stripe, Databricks, OpenAI, Coinbase, Pinterest, Robinhood, etc.). Structured loops, mid-funnel by day 14 — the 14-day window catches them while still warm."
              value={field.value}
              onChange={field.onChange}
            />
          )}
        />
        <Controller
          control={control}
          name="freshness_window_growth_days"
          render={({ field }) => (
            <FreshnessSlider
              label="Growth-tier"
              hint="Everything else — smaller startups, niche companies, remote-board postings. The early-bird advantage is real here: ~7× higher response rate in the first 4-7 days vs. day 30+."
              value={field.value}
              onChange={field.onChange}
            />
          )}
        />
      </CardContent>
    </Card>
  );
}

/**
 * Single tier slider. 0 means "off" (no age filter for this tier).
 * The visible scale is non-linear at the right end — once you're
 * past 30 days, the response-rate cliff is so steep that a 60-day
 * vs 90-day distinction barely matters, so we cap at 90.
 */
function FreshnessSlider({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-sm font-medium">{label}</div>
        <div className="text-sm font-mono text-accent tabular-nums">
          {value > 0 ? `${value} day${value === 1 ? "" : "s"}` : "no filter"}
        </div>
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">{hint}</p>
      <Slider
        value={[value]}
        onValueChange={([v]) => onChange(v ?? 0)}
        min={0}
        max={90}
        step={1}
      />
      <div className="flex justify-between text-[10px] text-muted-foreground font-mono">
        <span>0 — show all ages</span>
        <span>90 days</span>
      </div>
    </div>
  );
}
