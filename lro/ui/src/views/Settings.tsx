import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useConfig, useSaveConfig } from "@/hooks/useConfig";
import { ResumeSection } from "@/components/settings/ResumeSection";
import { TitlesSection } from "@/components/settings/TitlesSection";
import { ScoringSection } from "@/components/settings/ScoringSection";
import { FreshnessSection } from "@/components/settings/FreshnessSection";
import { LocationSection } from "@/components/settings/LocationSection";
import { CompaniesSection } from "@/components/settings/CompaniesSection";
import { ModelsSection } from "@/components/settings/ModelsSection";
import { DangerSection } from "@/components/settings/DangerSection";
import { CheckCircle2, AlertCircle, Save } from "lucide-react";

/**
 * Settings — single scrolling form with one SAVE button at the bottom.
 *
 * State design (and the v1 bug we're explicitly preventing):
 *
 *   server config (TanStack Query, hydrate-once)
 *           │  reset() once on hydration
 *           ▼
 *   form state (react-hook-form)  ◄── inputs read & write here
 *           │  POST on submit
 *           ▼
 *   server config (mutation invalidates query cache → re-hydrate next mount)
 *
 * Polling NEVER touches form state. The 2s /api/status poll runs
 * unaffected, and TanStack Query's staleTime: Infinity on /api/config
 * ensures the form values are sticky until the user saves.
 *
 * The shape below is the SOURCE OF TRUTH for what's editable. Each
 * section file imports `SettingsFormShape` so the field names line up.
 */
export interface SettingsFormShape {
  // Titles
  role_keywords: string;
  blocked_title_keywords: string;
  // Scoring
  threshold: number;
  ghost_weight: number;
  ghost_flag_threshold: number;
  ghost_warn_threshold: number;
  salary_floor_usd: number;
  salary_weight: number;
  years_experience: number;
  // Location — multi-select substring lists. v1 was free-text, v2
  // was a pin-on-a-map filter, v3 (current) is dropdown chips with
  // a free-text fallback for one-off substrings. See
  // LocationSection.tsx for the history note.
  allowed_locations: string[];
  blocked_locations: string[];
  // Freshness windows (per company-size tier — see lib/companyTier.ts)
  freshness_window_mega_days: number;
  freshness_window_large_days: number;
  freshness_window_growth_days: number;
  // Model picks per task. Each maps to config.{task}.model on the wire.
  model_parse: string;
  model_match: string;
  model_analyze: string;
  model_digest: string;
  model_cover_letter: string;
}

export function Settings() {
  const config = useConfig();
  const save = useSaveConfig();

  const form = useForm<SettingsFormShape>({
    defaultValues: emptyFormValues(),
  });

  // Bridge to ResumeSection — keeps the profile form's state local
  // to that component (because it POSTs to a different endpoint),
  // but lets the bottom SAVE button orchestrate both saves in parallel.
  // - resumeSubmitRef.current() awaits the profile POST
  // - resumeDirty mirrors the profile form's isDirty so we know
  //   whether to enable the SAVE button when only resume is edited
  const resumeSubmitRef = useRef<(() => Promise<void>) | null>(null);
  const [resumeDirty, setResumeDirty] = useState(false);
  const [combinedSaving, setCombinedSaving] = useState(false);
  const [combinedSaved, setCombinedSaved] = useState(false);
  const [combinedError, setCombinedError] = useState<string | null>(null);

  // Hydrate the form ONCE when config arrives. We use `reset` (not
  // setValue) because reset also clears the dirty flag — so the SAVE
  // button correctly stays disabled until the user actually edits.
  // The hasReset flag guards against re-running on every status poll.
  const hasReset = form.formState.isDirty || form.formState.isSubmitted;
  useEffect(() => {
    if (config.data && !hasReset) {
      form.reset(configToFormValues(config.data));
    }
  }, [config.data, hasReset, form]);

  const onSubmit = form.handleSubmit(async (values) => {
    setCombinedSaving(true);
    setCombinedError(null);
    setCombinedSaved(false);
    try {
      // Run both saves in parallel. We only POST the config side if
      // its form is dirty — same skip-if-clean rule applies to the
      // resume side inside ResumeSection's submit handler. This
      // means clicking SAVE with no edits is a no-op (no wasted
      // round trips).
      const promises: Promise<unknown>[] = [];
      if (form.formState.isDirty) {
        promises.push(save.mutateAsync(formValuesToConfig(values)));
      }
      if (resumeDirty && resumeSubmitRef.current) {
        promises.push(resumeSubmitRef.current());
      }
      await Promise.all(promises);
      // Reset the config form's dirty flag (resume side resets itself
      // via its own onSuccess inside ResumeSection).
      form.reset(values);
      setCombinedSaved(true);
    } catch (err) {
      setCombinedError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setCombinedSaving(false);
    }
  });

  const anyDirty = form.formState.isDirty || resumeDirty;

  return (
    <form onSubmit={onSubmit} className="space-y-6 pb-12">
      <ResumeSection submitRef={resumeSubmitRef} onDirtyChange={setResumeDirty} />
      <TitlesSection
        roleRegister={form.register("role_keywords")}
        blockedRegister={form.register("blocked_title_keywords")}
      />
      <ScoringSection control={form.control} />
      <FreshnessSection control={form.control} />
      <LocationSection control={form.control} />
      <CompaniesSection />
      <ModelsSection control={form.control} />

      {/* Sticky save bar — single button orchestrates BOTH the config
       *  save (POST /api/config) AND the resume profile save
       *  (POST /api/resume/profile). Skips whichever side has no
       *  edits so a click is never a wasted round trip. */}
      <Card className="sticky bottom-4 shadow-lg border-accent/30">
        <CardContent className="pt-4 pb-4 flex items-center justify-between gap-3">
          <div className="text-sm text-muted-foreground">
            {combinedError && (
              <span className="text-destructive flex items-center gap-1.5">
                <AlertCircle className="h-4 w-4" />
                {combinedError}
              </span>
            )}
            {combinedSaved && !anyDirty && (
              <span className="text-ghost-clear flex items-center gap-1.5">
                <CheckCircle2 className="h-4 w-4" />
                Saved
              </span>
            )}
            {anyDirty && !combinedSaving && <span>Unsaved changes</span>}
          </div>
          <Button type="submit" variant="accent" disabled={!anyDirty || combinedSaving}>
            <Save className="h-4 w-4 mr-2" />
            {combinedSaving ? "Saving…" : "Save settings"}
          </Button>
        </CardContent>
      </Card>

      {/* DangerSection sits below the sticky save bar — visually
          separated from the editable form so destructive actions
          can't be confused with "save my changes." */}
      <DangerSection />
    </form>
  );
}

// ─── Form ↔ config shape adapters ──────────────────────────────────────
// Keep these next to the form definition so changes to the form fields
// and the wire format are obvious in one read.

function emptyFormValues(): SettingsFormShape {
  return {
    role_keywords: "",
    blocked_title_keywords: "",
    threshold: 0.55,
    ghost_weight: 0.35,
    ghost_flag_threshold: 0.45,
    ghost_warn_threshold: 0.30,
    salary_floor_usd: 0,
    salary_weight: 0.15,
    years_experience: 0,
    allowed_locations: [],
    blocked_locations: [],
    freshness_window_mega_days: 30,
    freshness_window_large_days: 14,
    freshness_window_growth_days: 7,
    model_parse: "",
    model_match: "",
    model_analyze: "",
    model_digest: "",
    model_cover_letter: "",
  };
}

function configToFormValues(c: import("@/types/config").AppConfig): SettingsFormShape {
  // Defensive coercion. The server tolerates a missing or partially-
  // shaped config; the form should match that posture. Anything
  // unexpected becomes the safe default rather than crashing on a
  // `.join is not a function` or `.map is not a function`.
  const prefs = c.preferences ?? {};
  const fd = c.fake_detection ?? {};
  const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
  const num = (v: unknown, fb: number): number => (typeof v === "number" ? v : fb);
  const str = (v: unknown): string => (typeof v === "string" ? v : "");

  return {
    role_keywords: arr<string>(c.ingest?.role_keywords).join(", "),
    blocked_title_keywords: arr<string>(c.preferences?.blocked_title_keywords).join(", "),
    threshold: num(c.match?.threshold, 0.55),
    ghost_weight: num(fd.ghost_weight, 0.35),
    ghost_flag_threshold: num(fd.flag_threshold, 0.45),
    ghost_warn_threshold: num(fd.warn_threshold, 0.30),
    salary_floor_usd: num(prefs.salary_floor_usd, 0),
    salary_weight: num(prefs.salary_weight, 0.15),
    years_experience: num(prefs.years_experience, 0),
    allowed_locations: arr<unknown>(prefs.allowed_locations)
      .map(str)
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean),
    blocked_locations: arr<unknown>(prefs.blocked_locations)
      .map(str)
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean),
    freshness_window_mega_days: num(prefs.freshness_window_mega_days, 30),
    freshness_window_large_days: num(prefs.freshness_window_large_days, 14),
    freshness_window_growth_days: num(prefs.freshness_window_growth_days, 7),
    model_parse: str(c.parse?.model),
    model_match: str(c.match?.model),
    model_analyze: str(c.analyze?.model),
    model_digest: str(c.digest?.model),
    model_cover_letter: str(c.cover_letter?.model),
  };
}

function formValuesToConfig(v: SettingsFormShape): Partial<import("@/types/config").AppConfig> {
  return {
    ingest: {
      role_keywords: v.role_keywords
        .split(",")
        .map((s) => s.trim().toLowerCase())
        .filter(Boolean),
    },
    parse: v.model_parse ? { model: v.model_parse } : undefined,
    match: {
      threshold: v.threshold,
      ...(v.model_match ? { model: v.model_match } : {}),
    },
    analyze: v.model_analyze ? { model: v.model_analyze } : undefined,
    digest: v.model_digest ? { model: v.model_digest } : undefined,
    cover_letter: v.model_cover_letter ? { model: v.model_cover_letter } : undefined,
    fake_detection: {
      ghost_weight: v.ghost_weight,
      flag_threshold: v.ghost_flag_threshold,
      warn_threshold: v.ghost_warn_threshold,
    },
    preferences: {
      blocked_title_keywords: v.blocked_title_keywords
        .split(",")
        .map((s) => s.trim().toLowerCase())
        .filter(Boolean),
      salary_floor_usd: v.salary_floor_usd,
      salary_weight: v.salary_weight,
      years_experience: v.years_experience,
      allowed_locations: v.allowed_locations
        .map((s) => s.trim().toLowerCase())
        .filter(Boolean),
      blocked_locations: v.blocked_locations
        .map((s) => s.trim().toLowerCase())
        .filter(Boolean),
      freshness_window_mega_days: v.freshness_window_mega_days,
      freshness_window_large_days: v.freshness_window_large_days,
      freshness_window_growth_days: v.freshness_window_growth_days,
    },
  };
}
