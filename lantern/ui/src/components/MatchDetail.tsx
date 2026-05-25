import { useEffect, useMemo, useState } from "react";
import DOMPurify from "dompurify";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { useReactToMatch } from "@/hooks/useReact";
import { useGenerateCoverLetter, type CoverLetterTone } from "@/hooks/useCoverLetter";
import { useSummarizeJob } from "@/hooks/useSummarize";
import { useTailorResume } from "@/hooks/useTailorResume";
import { useUIStore } from "@/stores/ui";
import type { MatchPayload } from "@/types/match";
import { trimJobDescription, wasTrimmed } from "@/lib/jdTrim";
import { rowKey } from "@/lib/rowKey";
import {
  ExternalLink,
  Heart,
  X,
  Star,
  FileText,
  Sparkles,
  Loader2,
  Copy,
  Check,
  ChevronDown,
  ChevronUp,
} from "lucide-react";

/**
 * Right-rail detail panel for whichever match is selected.
 *
 * Reads `selectedJobUrl` from Zustand and looks up the row in the
 * matches array passed in via props. If nothing's selected, renders
 * a subtle placeholder. Clean separation: the table emits selection,
 * this view consumes it, no shared local state.
 */
interface Props {
  matches: MatchPayload[];
}

export function MatchDetail({ matches }: Props) {
  const url = useUIStore((s) => s.selectedJobUrl);
  const setUrl = useUIStore((s) => s.setSelectedJobUrl);
  const react = useReactToMatch();

  // Keep cover-letter UI state local to the panel — there's nothing
  // anyone else in the app needs to know about it. Reset implicitly
  // on selection change because the component re-mounts when `url`
  // changes via React's key, but we play safe by re-deriving from `job`.
  const [showFullJd, setShowFullJd] = useState(false);
  const [coverOpen, setCoverOpen] = useState(false);
  const [tone, setTone] = useState<CoverLetterTone>("professional");
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);
  // Editable copy of the generated letter — the user can tweak before
  // copying, so we shadow the mutation result in local state. Sync via
  // useEffect when a new draft arrives.
  const [draftText, setDraftText] = useState("");
  const generate = useGenerateCoverLetter();
  useEffect(() => {
    if (generate.data?.text) setDraftText(generate.data.text);
  }, [generate.data?.text]);

  // Résumé tailoring — same on-demand, deliberate-click model as the
  // cover-letter generator above. Local panel state only.
  const [resumeOpen, setResumeOpen] = useState(false);
  const [resumeCopied, setResumeCopied] = useState(false);
  const tailor = useTailorResume();

  // JD summariser. The fresh-from-mutation `summary` text takes
  // precedence over the registry-cached `_summary` so the user sees
  // their click reflected immediately — without waiting on the next
  // useMatches refetch (~2s). When selection changes, the mutation
  // resets via the `key` on the component-tree higher up; we also
  // explicitly clear local override state on URL change below.
  const summarize = useSummarizeJob();
  useEffect(() => {
    summarize.reset();
    // intentionally only on URL change — `summarize` ref is stable
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  // Use rowKey() so we still find the correct row when `url` is null
  // (legacy Google entries, malformed feeds). The selection key in
  // Zustand mirrors what the table emits, so consistency here matters.
  const job = matches.find((m) => rowKey(m) === url);

  // Compute the trimmed JD up here so the conditional below can also
  // ask "did we trim anything?" without re-running the parser. The
  // useMemo dep is the raw description string — selection changes
  // implicitly invalidate via that.
  const { jdHtmlToShow, trimmedAvailable } = useMemo(() => {
    const raw = job?.description ?? "";
    if (!raw) return { jdHtmlToShow: "", trimmedAvailable: false };
    const trimmed = trimJobDescription(raw);
    const didTrim = wasTrimmed(raw, trimmed);
    return {
      jdHtmlToShow: showFullJd || !didTrim ? raw : trimmed,
      trimmedAvailable: didTrim,
    };
  }, [job?.description, showFullJd]);

  if (!job) {
    return (
      <Card className="h-full">
        <CardContent className="pt-6 text-sm text-muted-foreground text-center">
          Select a row to see the full posting and apply.
        </CardContent>
      </Card>
    );
  }

  // Unified score format — three pills in a row, every metric same
  // visual weight so the eye reads them as a set, not "this one's a
  // bar, this one's a number, this one's a colored badge."
  //
  // Fit       = resume↔JD similarity BEFORE the ghost penalty knocks
  //             it down. Uses _match_score_pre_ghost when present; for
  //             pre-ghost-fold legacy entries we fall back to
  //             _match_score (which IS the post-ghost number on those
  //             rows but it's the best we have).
  // Adjusted  = the same fit AFTER the ghost penalty + a perceptual
  //             calibration that stretches the bunched 0.40-0.70
  //             cosine band into a 5-98% display window. This is the
  //             headline number — sortable, comparable.
  // Ghost     = standalone 0-100 suspicion score from the
  //             fake-detector module. Independent of Fit/Adjusted.
  const fitPct = Math.round(((job._match_score_pre_ghost ?? job._match_score) ?? 0) * 100);
  const adjPct = Math.round((job._match_score_display ?? job._match_score ?? 0) * 100);
  const ghostPenaltyPct = job._ghost_penalty ? Math.round(job._ghost_penalty * 100) : 0;

  const onGenerate = () => {
    setCopied(false);
    generate.mutate({ job, tone, custom_note: note.trim() });
  };

  const onCopy = async () => {
    if (!draftText) return;
    try {
      await navigator.clipboard.writeText(draftText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API can fail in non-https / locked-down browsers.
      // Silent — user can still select-all + Ctrl-C from the textarea.
    }
  };

  const onCopyResume = async () => {
    const d = tailor.data;
    if (!d) return;
    // Flatten the structured draft into one plain-text block so a
    // single "Copy all" lands the whole thing in the clipboard.
    const text = [
      d.summary,
      "",
      ...d.bullets.map((b) => `• ${b}`),
      "",
      d.keywords.length ? `Keywords: ${d.keywords.join(", ")}` : "",
      "",
      d.cover_note,
    ]
      .filter(Boolean)
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setResumeCopied(true);
      window.setTimeout(() => setResumeCopied(false), 1500);
    } catch {
      // Clipboard API can fail in non-https / locked-down browsers.
    }
  };

  return (
    <Card>
      <CardHeader className="space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-xl font-semibold leading-tight">{job.title}</h2>
            <div className="text-sm text-accent font-medium mt-1">{job.company}</div>
          </div>
          <Button variant="ghost" size="icon" onClick={() => setUrl(null)} title="Close detail">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex flex-wrap gap-2">
          {job.location && <Badge variant="outline">{job.location}</Badge>}
          {job.work_mode && <Badge variant="outline">{job.work_mode}</Badge>}
          {job.archetype_label && <Badge variant="outline">{job.archetype_label}</Badge>}
          {/* Salary + YoE badges sit alongside the location/work-mode/
              archetype tags so all the "shape of this role" facts are
              in one row at the top of the panel. Both are conditional —
              we never invent numbers. About 60-70% of postings have a
              salary band visible, ~80% have a YoE signal somewhere. */}
          {formatSalaryBadge(job.salary) && (
            <Badge variant="outline" title="Posted salary band (midpoint shown when range is wide)">
              {formatSalaryBadge(job.salary)}
            </Badge>
          )}
          {formatYearsBadge(job.years_experience) && (
            <Badge variant="outline" title="Years-of-experience signal extracted from the JD">
              {formatYearsBadge(job.years_experience)}
            </Badge>
          )}
          {job._source && (
            <Badge variant="outline" className="font-mono text-[10px]">
              {job._source}
            </Badge>
          )}
        </div>

        {/* Three score pills, identical visual treatment. Each carries
            a `title=` tooltip that explains what the number means and
            how it's computed — same content as the top-of-Matches
            legend, repeated here so users who jump straight to a row
            don't have to scroll back up to learn the vocabulary. */}
        <div className="grid grid-cols-3 gap-2 pt-1">
          <ScorePill
            label="Fit"
            value={`${fitPct}`}
            title="Resume ↔ job-description similarity, before any ghost penalty. Computed as cosine similarity between your resume embedding (BAAI/bge-m3) and the JD embedding, with small adjustments for title, location, salary, and years of experience."
          />
          <ScorePill
            label="Score"
            value={`${adjPct}`}
            tone="accent"
            title={`Fit after the ghost penalty knocks it down${ghostPenaltyPct ? ` (this one was knocked down ${ghostPenaltyPct}%)` : ""}, then rescaled into a 5-98% band so small differences are visible. This is what the table sorts by.`}
          />
          {job._fake ? (
            <GhostBadgePill score={job._fake.score} />
          ) : (
            <ScorePill label="Ghost" value="—" title="No ghost-detection signals available for this row." />
          )}
        </div>
      </CardHeader>

      <Separator />

      {/* Action row — the close-the-loop bit. Apply opens the real
          listing in a new tab; Like/Pass mutate via TanStack Query
          (optimistic) so the row updates instantly. */}
      <CardContent className="pt-4 space-y-4">
        <div className="flex gap-2">
          <Button asChild variant="accent" className="flex-1" disabled={!job.url}>
            <a href={job.url} target="_blank" rel="noopener noreferrer">
              <ExternalLink className="h-4 w-4 mr-2" />
              Apply
            </a>
          </Button>
          <Button
            variant={job._starred ? "accent" : "outline"}
            size="icon"
            onClick={() => react.mutate({ url: job.url, reaction: "star" })}
            title={job._starred ? "Unstar" : "Star"}
          >
            <Star className={`h-4 w-4 ${job._starred ? "fill-current" : ""}`} />
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={() => react.mutate({ url: job.url, reaction: "up" })}
            title="Like"
          >
            <Heart className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={() => react.mutate({ url: job.url, reaction: "down" })}
            title="Pass"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Cover-letter generator — collapsed by default so the panel
            stays tight for users who just want to scan + apply. The
            generation call is heavy (qwen3:30b on local Ollama, ~30-90s)
            so we want it to be a deliberate click, never automatic. */}
        <div className="rounded-md border bg-secondary/20">
          <button
            type="button"
            onClick={() => setCoverOpen((v) => !v)}
            className="w-full flex items-center justify-between gap-2 px-3 py-2 text-sm hover:bg-secondary/40 transition-colors rounded-md"
            aria-expanded={coverOpen}
          >
            <span className="flex items-center gap-2 font-medium">
              <FileText className="h-4 w-4 text-accent" />
              Generate cover letter
            </span>
            {coverOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>

          {coverOpen && (
            <div className="px-3 pb-3 pt-1 space-y-3">
              {/* Tone selector — three short pill-buttons. We render as
                  buttons (not a <select>) because three options is the
                  classic "show all, no surprises" sweet spot. */}
              <div>
                <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1.5">Tone</div>
                <div className="flex gap-1.5">
                  {(["professional", "warm", "punchy"] as CoverLetterTone[]).map((t) => (
                    <Button
                      key={t}
                      type="button"
                      size="sm"
                      variant={tone === t ? "accent" : "outline"}
                      onClick={() => setTone(t)}
                      className="capitalize text-xs h-7 px-2.5"
                      disabled={generate.isPending}
                    >
                      {t}
                    </Button>
                  ))}
                </div>
              </div>

              {/* Optional candidate-side instruction. Goes verbatim into
                  the prompt under "EXTRA INSTRUCTIONS FROM CANDIDATE".
                  Keep it short — one sentence is the sweet spot. */}
              <div>
                <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1.5">
                  Anything to mention? <span className="normal-case text-[10px]">(optional)</span>
                </div>
                <Textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="e.g. relocating to NYC next month, open to contract"
                  rows={2}
                  className="text-sm resize-none"
                  disabled={generate.isPending}
                />
              </div>

              <Button
                onClick={onGenerate}
                disabled={generate.isPending}
                variant="accent"
                size="sm"
                className="w-full"
              >
                {generate.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Drafting… (30-90s on local LLM)
                  </>
                ) : generate.data ? (
                  "Regenerate"
                ) : (
                  "Generate cover letter"
                )}
              </Button>

              {generate.isError && (
                <p className="text-xs text-destructive">
                  {generate.error?.message ?? "Could not generate. Check Ollama is running."}
                </p>
              )}

              {draftText && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="text-xs text-muted-foreground">
                      <span className="font-mono">{generate.data?.model}</span>
                      {generate.data?.saved_to && (
                        <span className="ml-2 italic">· saved to disk</span>
                      )}
                    </div>
                    <Button
                      onClick={onCopy}
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-xs"
                    >
                      {copied ? (
                        <>
                          <Check className="h-3 w-3 mr-1" />
                          Copied
                        </>
                      ) : (
                        <>
                          <Copy className="h-3 w-3 mr-1" />
                          Copy
                        </>
                      )}
                    </Button>
                  </div>
                  {/* Render in a textarea so the user can tweak before
                      copying — generated text is a draft, not the final
                      letter. Read-only would be paternalistic. */}
                  <Textarea
                    value={draftText}
                    onChange={(e) => {
                      setDraftText(e.target.value);
                      setCopied(false);
                    }}
                    rows={12}
                    className="text-sm leading-relaxed font-serif"
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Résumé tailoring — on-demand. Same deliberate-click model as
            the cover-letter generator above: one local LLM call
            (~20-40s), so it never runs automatically. It used to run
            for the top 5 matches every pipeline cycle (the slowest
            stage) — now it's a per-match button. */}
        <div className="rounded-md border bg-secondary/20">
          <button
            type="button"
            onClick={() => setResumeOpen((v) => !v)}
            className="w-full flex items-center justify-between gap-2 px-3 py-2 text-sm hover:bg-secondary/40 transition-colors rounded-md"
            aria-expanded={resumeOpen}
          >
            <span className="flex items-center gap-2 font-medium">
              <Sparkles className="h-4 w-4 text-accent" />
              Tailor résumé
            </span>
            {resumeOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>

          {resumeOpen && (
            <div className="px-3 pb-3 pt-1 space-y-3">
              <p className="text-xs text-muted-foreground">
                Rewrites your résumé for this specific role — a tailored summary,
                prioritised experience bullets, and JD keywords. One local LLM call;
                nothing is fabricated, only reframed from your uploaded résumé.
              </p>

              <Button
                onClick={() => {
                  setResumeCopied(false);
                  tailor.mutate({ job });
                }}
                disabled={tailor.isPending}
                variant="accent"
                size="sm"
                className="w-full"
              >
                {tailor.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Tailoring… (20-40s on local LLM)
                  </>
                ) : tailor.data ? (
                  "Re-tailor"
                ) : (
                  "Tailor résumé"
                )}
              </Button>

              {tailor.isError && (
                <p className="text-xs text-destructive">
                  {tailor.error?.message ?? "Could not tailor. Check Ollama is running."}
                </p>
              )}

              {tailor.data && (
                <div className="space-y-3 text-sm">
                  <div className="flex items-center justify-between">
                    <div className="text-xs text-muted-foreground uppercase tracking-wider">
                      Tailored draft
                    </div>
                    <Button onClick={onCopyResume} size="sm" variant="ghost" className="h-7 px-2 text-xs">
                      {resumeCopied ? (
                        <>
                          <Check className="h-3 w-3 mr-1" />
                          Copied
                        </>
                      ) : (
                        <>
                          <Copy className="h-3 w-3 mr-1" />
                          Copy all
                        </>
                      )}
                    </Button>
                  </div>

                  {tailor.data.summary && (
                    <div>
                      <div className="text-xs text-foreground/70 mb-1">Summary</div>
                      <p className="leading-relaxed text-foreground/90">{tailor.data.summary}</p>
                    </div>
                  )}

                  {tailor.data.bullets.length > 0 && (
                    <div>
                      <div className="text-xs text-foreground/70 mb-1">Bullets</div>
                      <ul className="space-y-1 text-foreground/90">
                        {tailor.data.bullets.map((b, i) => (
                          <li
                            key={i}
                            className="pl-3 relative before:absolute before:left-0 before:content-['·']"
                          >
                            {b}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {tailor.data.keywords.length > 0 && (
                    <div>
                      <div className="text-xs text-foreground/70 mb-1">Keywords</div>
                      <div className="flex flex-wrap gap-1.5">
                        {tailor.data.keywords.map((k) => (
                          <Badge key={k} variant="secondary" className="font-normal">
                            {k}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {tailor.data.cover_note && (
                    <div>
                      <div className="text-xs text-foreground/70 mb-1">Cover note</div>
                      <p className="leading-relaxed text-muted-foreground italic">
                        {tailor.data.cover_note}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Tags / tech stack */}
        {(job.tags?.length ?? 0) > 0 && (
          <div>
            <div className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Tags</div>
            <div className="flex flex-wrap gap-1.5">
              {job.tags!.map((t) => (
                <Badge key={t} variant="secondary" className="font-normal">
                  {t}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Fit/gap rationale (only present after ANALYZE stage). */}
        {job._fit_gap?.summary && (
          <div>
            <div className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Why this match</div>
            <p className="text-sm leading-relaxed text-muted-foreground">{job._fit_gap.summary}</p>
            {(job._fit_gap.matched?.length ?? 0) > 0 && (
              <div className="mt-3">
                <div className="text-xs text-foreground/70 mb-1">Matched</div>
                <div className="flex flex-wrap gap-1.5">
                  {job._fit_gap.matched!.map((s) => (
                    <Badge key={s} variant="clear" className="font-normal">
                      {s}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            {(job._fit_gap.gaps?.length ?? 0) > 0 && (
              <div className="mt-3">
                <div className="text-xs text-foreground/70 mb-1">Gaps</div>
                <div className="flex flex-wrap gap-1.5">
                  {job._fit_gap.gaps!.map((s) => (
                    <Badge key={s} variant="suspect" className="font-normal">
                      {s}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Full JD — most ATS feeds (Greenhouse, Lever, Ashby) ship the
            description as HTML markup. We sanitize with DOMPurify (strips
            <script>, on*= handlers, javascript:, etc.) and then render
            via dangerouslySetInnerHTML so the user actually SEES the
            formatting (paragraphs, bullets, bolds) rather than raw tags.
            DOMPurify is the standard tool for this and the sanitization
            is fast (sub-millisecond on the JDs we deal with).
            The `prose-jd` class is defined in the same file as a quick
            local style — see below.
            We pre-trim the JD to focus on responsibilities/requirements/
            qualifications via lib/jdTrim.ts. The "Show full description"
            toggle lets the user fall back to the unedited blob when the
            heuristic was too aggressive. */}
        {/* Summary block — sits ABOVE the JD blob so the user can scan
            a 3-4 sentence narrative before deciding to dive into the
            full posting. The text is generated on demand by clicking
            "Summarize" (one ~5-10s LLM call on gemma3:12b/qwen3:8b).
            After the first generation it's persisted into the match
            registry, so subsequent visits to the same role render
            instantly from the cached `_summary` field. */}
        {job.description && (() => {
          // Prefer the freshly-mutated text if it just landed (instant
          // feedback, no 2s polling lag). Otherwise fall back to the
          // registry-cached `_summary`. We keep `cached` separate so
          // the caption can say "cached" vs "just generated".
          const fresh = summarize.data?.summary;
          const stored = job._summary;
          const summaryText = fresh ?? stored ?? "";
          const summaryModel = summarize.data?.model ?? job._summary_model ?? "";
          const isCached = !fresh && !!stored;
          return (
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs text-muted-foreground uppercase tracking-wider">
                  Summary
                  {summaryText && (
                    <span className="ml-2 text-[10px] normal-case tracking-normal text-muted-foreground/70 italic">
                      ({isCached ? "cached" : "just generated"}{summaryModel ? " · " + summaryModel : ""})
                    </span>
                  )}
                </div>
                {!summarize.isPending && (
                  <button
                    type="button"
                    onClick={() =>
                      summarize.mutate({
                        url: job.url,
                        description: job.description,
                        title: job.title,
                        company: job.company,
                        // If a summary already exists, the next click
                        // is a "regenerate" — force a fresh LLM call.
                        force: !!summaryText,
                      })
                    }
                    className="text-xs text-accent hover:underline disabled:opacity-50 disabled:no-underline"
                  >
                    {summaryText ? "Regenerate" : "Summarize"}
                  </button>
                )}
              </div>
              {summarize.isPending && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Generating summary… (~5–10s)
                </div>
              )}
              {summarize.error && !summarize.isPending && (
                <div className="text-xs text-destructive py-2">
                  {summarize.error.message || "Summary failed. Try again."}
                </div>
              )}
              {summaryText && !summarize.isPending && (
                <p className="text-sm leading-relaxed text-foreground/90 whitespace-pre-wrap">
                  {summaryText}
                </p>
              )}
              {!summaryText && !summarize.isPending && !summarize.error && (
                <p className="text-xs text-muted-foreground italic">
                  Click "Summarize" for a 3–4 sentence overview of this role.
                </p>
              )}
            </div>
          );
        })()}

        {job.description && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="text-xs text-muted-foreground uppercase tracking-wider">
                Job description
                {trimmedAvailable && !showFullJd && (
                  <span className="ml-2 text-[10px] normal-case tracking-normal text-muted-foreground/70 italic">
                    (trimmed to key sections)
                  </span>
                )}
              </div>
              {trimmedAvailable && (
                <button
                  type="button"
                  onClick={() => setShowFullJd((v) => !v)}
                  className="text-xs text-accent hover:underline"
                >
                  {showFullJd ? "Show key sections only" : "Show full description"}
                </button>
              )}
            </div>
            <div
              className="prose-jd text-sm leading-relaxed"
              dangerouslySetInnerHTML={{
                __html: DOMPurify.sanitize(jdHtmlToShow, {
                  // Strip <a target=> and rel attributes too — we don't
                  // need links inside the JD blob; Apply button handles that.
                  ALLOWED_TAGS: [
                    "p", "br", "strong", "b", "em", "i", "u",
                    "h1", "h2", "h3", "h4", "h5", "h6",
                    "ul", "ol", "li", "blockquote", "code", "pre",
                    "span", "div", "hr",
                  ],
                  ALLOWED_ATTR: [],
                }),
              }}
            />
          </div>
        )}

        {/* Ghost signals fired — iterate the `signals` dict from the
            fake-detector. A signal is present in the dict only if it
            could be evaluated; we show the ones that actually FIRED
            (score > 0) with their human-readable `reason`. Sorted
            strongest-first so the most damning evidence reads at the
            top. If nothing fired, the whole block is omitted. */}
        {job._fake?.signals &&
          (() => {
            const fired = Object.entries(job._fake.signals)
              .filter(([, s]) => s.score > 0)
              .sort(([, a], [, b]) => b.score - a.score);
            if (fired.length === 0) return null;
            return (
              <div>
                <div className="text-xs text-muted-foreground uppercase tracking-wider mb-2">
                  Ghost signals fired
                </div>
                <ul className="text-sm space-y-1 text-muted-foreground">
                  {fired.map(([name, s]) => (
                    <li key={name}>· {s.reason}</li>
                  ))}
                </ul>
              </div>
            );
          })()}
      </CardContent>
    </Card>
  );
}

/**
 * Format the salary field into a compact badge string, or return null
 * if there's nothing useful to show.
 *
 * Examples:
 *   {min: 140000, max: 180000, currency: "USD"}  → "$140–180k"
 *   {min: 150000, currency: "USD"}                → "$150k+"
 *   {max: 200000, currency: "EUR"}                → "€200k"
 *   undefined / both null                          → null
 *
 * Why thousands-only: every PM JD lists comp in the $100-300k range,
 * and "$144,500–172,800" wastes badge real estate. Round to the
 * nearest k and let the user click through if they want exact figures.
 */
function formatSalaryBadge(s?: { min?: number; max?: number; currency?: string }): string | null {
  if (!s) return null;
  if (s.min == null && s.max == null) return null;
  const cur = (s.currency || "USD").toUpperCase();
  const sym = cur === "USD" ? "$" : cur === "GBP" ? "£" : cur === "EUR" ? "€" : `${cur} `;
  const k = (n: number) => `${Math.round(n / 1000)}k`;
  if (s.min != null && s.max != null && s.min !== s.max) {
    return `${sym}${k(s.min)}–${k(s.max)}`;
  }
  if (s.min != null) return `${sym}${k(s.min)}+`;
  if (s.max != null) return `${sym}${k(s.max)}`;
  return null;
}

/**
 * Format the parsed years-of-experience signal into a badge string.
 * Returns null for missing / zero values so the badge doesn't render.
 *
 * The "+" suffix matches how JDs typically phrase it ("5+ years of
 * product management experience"). We don't model upper bounds here
 * because the JD parser already collapsed e.g. "5-7 years" to a
 * single floor number — see `agents/parse.py` for that logic.
 */
function formatYearsBadge(yoe?: number): string | null {
  if (yoe == null || yoe <= 0) return null;
  const rounded = Math.round(yoe);
  return `${rounded}+ yrs`;
}

/**
 * One score pill — uniform layout with the ghost pill so the three
 * read as a coherent triple. Big tabular-nums value on top, tiny
 * label below. The value is just a string here (not number) so the
 * Ghost variant can show "Clear", "Aging", or "Suspect" while the
 * other two show "71" / "97".
 */
function ScorePill({ label, value, tone = "muted", title }: { label: string; value: string; tone?: "accent" | "muted"; title?: string }) {
  const valueColor = tone === "accent" ? "text-accent" : "text-foreground";
  return (
    <div className="rounded-md border bg-secondary/30 p-3 text-center cursor-help" title={title}>
      <div className={`text-2xl font-mono font-semibold tabular-nums ${valueColor}`}>{value}</div>
      <div className="text-[10px] text-muted-foreground uppercase tracking-wider mt-1">{label}</div>
    </div>
  );
}

/**
 * Ghost variant — same outer shape as ScorePill so the three line up,
 * but the colour scheme tracks the ghost-tier band (clear / aging /
 * suspect) the same way the inline GhostBadge does.
 */
function GhostBadgePill({ score }: { score: number }) {
  const tier = score >= 0.45 ? "suspect" : score >= 0.3 ? "caution" : "clear";
  const colorClass = {
    clear: "text-ghost-clear",
    caution: "text-ghost-aging",
    suspect: "text-ghost-suspect",
  }[tier];
  const tierLabel = { clear: "Clear", caution: "Caution", suspect: "Suspect" }[tier];

  return (
    <div className="rounded-md border bg-secondary/30 p-3 text-center" title={`Ghost suspicion ${Math.round(score * 100)}/100`}>
      <div className={`text-2xl font-mono font-semibold tabular-nums ${colorClass}`}>
        {Math.round(score * 100)}
      </div>
      <div className={`text-[10px] uppercase tracking-wider mt-1 ${colorClass}`}>
        {tierLabel}
      </div>
    </div>
  );
}
