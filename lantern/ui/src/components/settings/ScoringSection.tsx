import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Separator } from "@/components/ui/separator";
import { Sliders } from "lucide-react";
import { type Control, Controller } from "react-hook-form";
import type { SettingsFormShape } from "@/views/Settings";

/**
 * Scoring section — every knob that shapes how matches get ranked.
 *
 * Five sliders, every one supports a "0 = off" position:
 *   - threshold       (0.4–0.95)  match strictness
 *   - ghost weight    (0–0.8)     how hard the ghost penalty bites
 *   - ghost flag      (0.2–0.9)   suspect threshold
 *   - ghost warn      (0.1–0.8)   aging threshold
 *   - salary floor    (0–400k)    soft minimum salary
 *   - salary weight   (0–0.4)     how much salary affects ranking
 *   - years exp       (0–30)      override resume parser
 *
 * `Controller` from react-hook-form bridges the shadcn Slider (which
 * isn't a native form control) to the form's register/value state.
 * Without Controller, react-hook-form wouldn't see slider changes.
 */
interface Props {
  control: Control<SettingsFormShape>;
}

export function ScoringSection({ control }: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sliders className="h-5 w-5" />
          Match scoring
        </CardTitle>
        <CardDescription>
          Every weight is exposed. Drag any slider left to its 0 position to turn that signal off entirely.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <Controller
          control={control}
          name="threshold"
          render={({ field }) => (
            <SliderRow
              label="Match threshold"
              hint="Postings below this score get hidden. 55–65% is the comfortable starting band for most users."
              value={field.value}
              onChange={field.onChange}
              min={0.4}
              max={0.95}
              step={0.05}
              format={(v) => `${Math.round(v * 100)}%`}
              leftLabel="40% — see more"
              rightLabel="95% — only the strongest"
            />
          )}
        />

        <Separator />

        <div className="space-y-1">
          <h4 className="text-sm font-semibold">Ghost-job filter</h4>
          <p className="text-xs text-muted-foreground">
            Listings get a 0–100 ghost score from nine deterministic signals (post age, vague location, missing apply
            link, etc). The penalty weight controls how much that score hurts the final ranking; the thresholds
            control the Aging / Suspect badge cutoffs.
          </p>
        </div>

        <Controller
          control={control}
          name="ghost_weight"
          render={({ field }) => (
            <SliderRow
              label="Ghost penalty weight"
              hint="Final score = fit × (1 − weight × ghost). At 0 the ghost score is advisory only; at 80% a full-ghost posting loses 80% of its fit score."
              value={field.value}
              onChange={field.onChange}
              min={0}
              max={0.8}
              step={0.05}
              format={(v) => (v > 0 ? `${Math.round(v * 100)}%` : "off")}
              leftLabel="0% — advisory only"
              rightLabel="80% — crush suspects"
            />
          )}
        />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Controller
            control={control}
            name="ghost_flag_threshold"
            render={({ field }) => (
              <SliderRow
                label="Suspect threshold"
                value={field.value}
                onChange={field.onChange}
                min={0.2}
                max={0.9}
                step={0.05}
                format={(v) => `${Math.round(v * 100)}%`}
                leftLabel="20%"
                rightLabel="90%"
              />
            )}
          />
          <Controller
            control={control}
            name="ghost_warn_threshold"
            render={({ field }) => (
              <SliderRow
                label="Aging threshold"
                value={field.value}
                onChange={field.onChange}
                min={0.1}
                max={0.8}
                step={0.05}
                format={(v) => `${Math.round(v * 100)}%`}
                leftLabel="10%"
                rightLabel="80%"
              />
            )}
          />
        </div>

        <Separator />

        <Controller
          control={control}
          name="salary_floor_usd"
          render={({ field }) => (
            <SliderRow
              label="Minimum salary"
              hint="Soft signal — jobs below this floor get ranked down, never silently dropped. Listings without a posted salary always pass."
              value={field.value}
              onChange={field.onChange}
              min={0}
              max={400000}
              step={5000}
              format={(v) => (v > 0 ? `$${(v / 1000).toFixed(0)}k` : "off")}
              leftLabel="0 — off"
              rightLabel="$400k"
            />
          )}
        />

        <Controller
          control={control}
          name="salary_weight"
          render={({ field }) => (
            <SliderRow
              label="Salary ranking weight"
              hint="How much the salary signal affects ranking."
              value={field.value}
              onChange={field.onChange}
              min={0}
              max={0.4}
              step={0.05}
              format={(v) => (v > 0 ? `${Math.round(v * 100)}%` : "off")}
              leftLabel="0 — off"
              rightLabel="40% — strong"
            />
          )}
        />

        <Separator />

        <Controller
          control={control}
          name="years_experience"
          render={({ field }) => (
            <SliderRow
              label="Years of experience"
              hint="Override the resume parser's value. Drives the 'roles wanting way more YoE than you have' soft penalty."
              value={field.value}
              onChange={field.onChange}
              min={0}
              max={30}
              step={1}
              format={(v) => (v > 0 ? `${v} yr${v === 1 ? "" : "s"}` : "off / let parser decide")}
              leftLabel="0 — off"
              rightLabel="30+ yrs"
            />
          )}
        />
      </CardContent>
    </Card>
  );
}

/** One slider row with a label, value readout, and min/max captions. */
function SliderRow({
  label,
  hint,
  value,
  onChange,
  min,
  max,
  step,
  format,
  leftLabel,
  rightLabel,
}: {
  label: string;
  hint?: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  leftLabel: string;
  rightLabel: string;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-sm font-medium">{label}</div>
        <div className="text-sm font-mono text-accent tabular-nums">{format(value)}</div>
      </div>
      {hint && <p className="text-xs text-muted-foreground leading-relaxed">{hint}</p>}
      <Slider
        value={[value]}
        onValueChange={([v]) => onChange(v ?? min)}
        min={min}
        max={max}
        step={step}
      />
      <div className="flex justify-between text-[10px] text-muted-foreground font-mono">
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
    </div>
  );
}
