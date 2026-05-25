import { useEffect, useRef, type MutableRefObject } from "react";
import { useForm } from "react-hook-form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { useResume, useResumeProfile, useUploadResume, useReparseResume, useSaveProfile } from "@/hooks/useResume";
import type { ResumeProfile } from "@/types/config";
import { Upload, RefreshCw, FileText, AlertCircle } from "lucide-react";

/**
 * Resume section — upload + re-parse + EDITABLE parsed-fields form.
 *
 * Why editable: the LLM parser is fast but imperfect. It might propose
 * "Director of Engineering" as a target role for a 5-year PM, or get
 * the seniority slightly off. The user knows their own background;
 * letting them override what the parser produced is the simplest fix.
 *
 * Form-state design:
 *   - The PROFILE form is its own react-hook-form instance, separate
 *     from the parent Settings form. We don't want a small profile
 *     edit to mark the entire Settings form as dirty (and vice-versa).
 *   - Hydrates from useResumeProfile() once — same hydrate-once pattern
 *     as the main Settings form, so polling can never clobber typing.
 *   - Save calls useSaveProfile() which POSTs the patch and invalidates
 *     the profile cache so the readout reflects the new values.
 *
 * Reparse re-runs the LLM and OVERWRITES user edits. We surface this
 * with a "Last edited by you" / "Last edited by parser" indicator so
 * the user can tell whose version is current.
 */
type ProfileFormShape = {
  summary: string;
  seniority: string;
  years_experience: number;
  target_roles_text: string;     // newline-separated for editing
  technologies_text: string;     // comma-separated for editing
  domains_text: string;
};

// Backend stores the lowercase canonical token (matches resume parser
// output and the seniority bands in core/fake_detector). The UI shows
// proper Title Case, with VP / CXO rendered as acronyms instead of
// "Vp" / "Cxo" — what a generic capitalize() helper would produce.
const SENIORITY_OPTIONS: { value: string; label: string }[] = [
  { value: "junior",    label: "Junior" },
  { value: "mid",       label: "Mid" },
  { value: "senior",    label: "Senior" },
  { value: "staff",     label: "Staff" },
  { value: "principal", label: "Principal" },
  { value: "director",  label: "Director" },
  { value: "vp",        label: "VP" },
  { value: "cxo",       label: "CXO" },
];

/**
 * Props let the parent Settings form orchestrate a single save. Why
 * not just lift the whole form up: the resume profile goes to a
 * DIFFERENT endpoint (POST /api/resume/profile) than the rest of
 * settings (POST /api/config). Keeping the local form means the
 * mutation, validation, and reset all stay co-located. The parent
 * just needs to know "is anything to save" (onDirtyChange) and
 * "do the save" (submitRef.current()).
 */
interface Props {
  /** Parent assigns its own ref here. We populate `.current` with a
   *  function the parent can `await` from its own onSubmit handler. */
  submitRef: MutableRefObject<(() => Promise<void>) | null>;
  /** Bubble dirty state up so the parent's combined SAVE button
   *  enables when EITHER form has unsaved edits. */
  onDirtyChange: (dirty: boolean) => void;
}

export function ResumeSection({ submitRef, onDirtyChange }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const resume = useResume();
  const profile = useResumeProfile();
  const upload = useUploadResume();
  const reparse = useReparseResume();
  const save = useSaveProfile();

  const m = resume.data?.metadata;
  const p = profile.data;

  // Profile-edit form. Empty defaults until profile.data arrives, then
  // we reset() once. Same pattern as the main Settings form.
  const form = useForm<ProfileFormShape>({
    defaultValues: emptyProfileForm(),
  });

  // Hydrate-once on profile arrival.
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (p && !hydratedRef.current) {
      form.reset(profileToFormValues(p));
      hydratedRef.current = true;
    }
  }, [p, form]);

  // After re-parse, the parser writes a fresh profile. Reset the form
  // to the new values so the readout matches reality. Without this,
  // the user would keep seeing their stale edits in the form even
  // though the backend just overwrote profile.json.
  useEffect(() => {
    if (reparse.isSuccess && p) {
      form.reset(profileToFormValues(p));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reparse.isSuccess, p?.generated_at]);

  // Expose the submit handler to the parent. We use mutateAsync so the
  // parent can await ALL saves in parallel (config + profile) and only
  // mark the SAVE button "done" once both complete.
  useEffect(() => {
    submitRef.current = async () => {
      // Only POST if there's something to save. Avoids hitting the
      // backend with an empty patch when only Settings was edited.
      if (!form.formState.isDirty) return;
      const values = form.getValues();
      await save.mutateAsync(formValuesToPatch(values));
      form.reset(values);
    };
  }, [submitRef, form, save]);

  // Bubble dirty state up so the parent's combined SAVE button knows
  // to enable when only the resume form has unsaved edits.
  useEffect(() => {
    onDirtyChange(form.formState.isDirty);
  }, [form.formState.isDirty, onDirtyChange]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileText className="h-5 w-5" />
          Resume
        </CardTitle>
        <CardDescription>
          Upload a PDF or DOCX. Stored locally under <code className="text-xs">data/resume/</code>. Nothing leaves
          your machine. Edit any of the parsed fields below to override what the LLM extracted — useful when the
          parser proposes titles that aren't actually a fit for your level.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Upload state — unchanged */}
        {resume.data?.has_resume ? (
          <div className="rounded-md border border-border p-3 bg-secondary/30 flex items-center justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <div className="font-medium text-sm truncate">{m?.filename ?? "resume"}</div>
              <div className="text-xs text-muted-foreground font-mono">
                {(m?.char_count ?? 0).toLocaleString()} chars
                {m?.size_bytes ? ` · ${(m.size_bytes / 1024).toFixed(1)} kB` : ""}
                {m?.uploaded_at ? ` · ${m.uploaded_at.replace("T", " ").slice(0, 16)}` : ""}
              </div>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()} disabled={upload.isPending}>
                <Upload className="h-3.5 w-3.5 mr-1.5" />
                Replace
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => reparse.mutate()}
                disabled={reparse.isPending}
                title="Re-runs the LLM parser. Overwrites any manual edits below."
              >
                <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${reparse.isPending ? "animate-spin" : ""}`} />
                {reparse.isPending ? "Parsing…" : "Re-parse"}
              </Button>
            </div>
          </div>
        ) : (
          <Button onClick={() => fileInputRef.current?.click()} disabled={upload.isPending} variant="accent">
            <Upload className="h-4 w-4 mr-2" />
            {upload.isPending ? "Uploading…" : "Upload resume"}
          </Button>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload.mutate(file);
          }}
          className="hidden"
        />

        {(upload.error ?? reparse.error) && (
          <div className="text-sm text-destructive flex items-start gap-2">
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{(upload.error ?? reparse.error)?.message}</span>
          </div>
        )}

        {/* EDITABLE parsed-fields form. Only renders when there's a
            cached profile to populate it from. */}
        {p && (
          <div className="rounded-md border bg-secondary/20 p-4 space-y-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="text-xs uppercase tracking-wider text-muted-foreground">
                Parsed by {p.model ?? "—"}
                {p._fallback && (
                  <Badge variant="aging" className="ml-2 text-[10px]">heuristic fallback</Badge>
                )}
                {p._user_edited && (
                  <Badge variant="clear" className="ml-2 text-[10px]">edited by you</Badge>
                )}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="profile-summary">Summary</Label>
              <Textarea id="profile-summary" rows={3} {...form.register("summary")} />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="profile-seniority">Seniority</Label>
                <select
                  id="profile-seniority"
                  className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  {...form.register("seniority")}
                >
                  {SENIORITY_OPTIONS.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="profile-years">Years of experience</Label>
                <Input
                  id="profile-years"
                  type="number"
                  min={0}
                  max={50}
                  step={1}
                  {...form.register("years_experience", { valueAsNumber: true })}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="profile-targets">Target roles (one per line)</Label>
              <Textarea
                id="profile-targets"
                rows={4}
                placeholder={"Senior Product Manager\nStaff Product Manager\nLead Product Manager"}
                {...form.register("target_roles_text")}
              />
              <p className="text-xs text-muted-foreground">
                These feed the embedding search. Each line is a separate target title — keep them in your seniority
                band so the matcher doesn't drift into wrong-archetype territory.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="profile-tech">Tech stack (comma-separated)</Label>
              <Textarea
                id="profile-tech"
                rows={2}
                placeholder="sql, python, figma, aws"
                {...form.register("technologies_text")}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="profile-domains">Domains (comma-separated)</Label>
              <Input
                id="profile-domains"
                placeholder="healthcare, retail, fintech"
                {...form.register("domains_text")}
              />
            </div>

            {/* Save button intentionally LIVES IN THE PARENT — see
             *  Settings.tsx. The bottom "Save settings" sticky bar
             *  fires both the config save AND this profile save in
             *  parallel. Surface only the resume-side error inline
             *  so the user can see WHICH side failed if a save errors. */}
            {save.isError && (
              <div className="text-sm text-destructive flex items-center gap-1.5">
                <AlertCircle className="h-4 w-4" />
                Profile save failed: {save.error.message}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Form ↔ profile shape adapters ──────────────────────────────────

function emptyProfileForm(): ProfileFormShape {
  return {
    summary: "",
    seniority: "senior",
    years_experience: 0,
    target_roles_text: "",
    technologies_text: "",
    domains_text: "",
  };
}

function profileToFormValues(p: ResumeProfile): ProfileFormShape {
  return {
    summary: p.summary ?? "",
    seniority: p.seniority ?? "senior",
    years_experience: p.years_experience ?? 0,
    target_roles_text: (p.target_roles ?? []).join("\n"),
    technologies_text: (p.technologies ?? []).join(", "),
    domains_text: (p.domains ?? []).join(", "),
  };
}

function formValuesToPatch(v: ProfileFormShape): Partial<ResumeProfile> {
  return {
    summary: v.summary.trim(),
    seniority: v.seniority,
    years_experience: Number(v.years_experience) || 0,
    target_roles: v.target_roles_text.split(/\n+/).map((s) => s.trim()).filter(Boolean),
    technologies: v.technologies_text.split(",").map((s) => s.trim()).filter(Boolean),
    domains: v.domains_text.split(",").map((s) => s.trim()).filter(Boolean),
  };
}
