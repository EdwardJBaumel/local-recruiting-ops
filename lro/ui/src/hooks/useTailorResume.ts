import { useMutation } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { MatchPayload } from "@/types/match";

export interface TailorResumeRequest {
  job: MatchPayload;
}

export interface TailorResumeResponse {
  ok: boolean;
  /** Rewritten 3-4 sentence professional summary for this role. */
  summary: string;
  /** Experience bullets, reordered + reworded, most-relevant first. */
  bullets: string[];
  /** 8-12 JD keywords to weave into the résumé. */
  keywords: string[];
  /** Short personalised note on why this role is a strong fit. */
  cover_note: string;
}

/**
 * One-shot mutation → POST /api/tailor-resume. Tailors the user's
 * résumé to one specific job: a rewritten summary, prioritised
 * experience bullets, JD keywords, and a short cover note.
 *
 * Why a mutation, not a query: the call is one local LLM pass
 * (~20-40s on qwen3:14b) and SHOULD only fire on explicit user action.
 * Same rationale as useGenerateCoverLetter — résumé tailoring used to
 * run for the top 5 every pipeline cycle (the slowest stage); it's now
 * strictly user-triggered from the Matches detail panel.
 *
 * Errors surface via the standard ApiError path — the caller renders
 * `error.message` inline. Common rejection: "No resume on file" when
 * the user hasn't uploaded one yet.
 */
export function useTailorResume() {
  return useMutation<TailorResumeResponse, Error, TailorResumeRequest>({
    mutationFn: (req) =>
      api.post<TailorResumeResponse>("/tailor-resume", {
        // Send only what the server reads — no need to ship the whole
        // MatchPayload (_starred, _seen, etc.).
        job: {
          title: req.job.title,
          company: req.job.company,
          description: req.job.description,
          technologies: req.job.technologies,
          // If the row has been through ANALYZE, its fit/gap data gives
          // the LLM richer context; harmless when absent.
          _fit_gap: req.job._fit_gap,
          _match_score: req.job._match_score,
        },
      }),
  });
}
