import { useMutation } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { MatchPayload } from "@/types/match";

/**
 * Cover letter tone — three flavours the backend understands. We expose
 * them as a small literal union so the UI dropdown can't drift out of
 * sync with what the server validates.
 *   - professional: calm, specific, default for most apps
 *   - warm:         conversational, good for "team-first" companies
 *   - punchy:       tight + high-signal, good for early-stage / IC roles
 */
export type CoverLetterTone = "professional" | "warm" | "punchy";

export interface CoverLetterRequest {
  job: MatchPayload;
  tone?: CoverLetterTone;
  /** Optional one-liner the user can add ("mention I'm relocating to NYC", etc). */
  custom_note?: string;
  /** Override the configured cover_letter.model. Empty string = use config default. */
  model?: string;
}

export interface CoverLetterResponse {
  ok: boolean;
  text: string;
  /** Absolute path to the persisted .md file, if write succeeded. */
  saved_to?: string | null;
  tone: CoverLetterTone;
  model: string;
}

/**
 * One-shot mutation that hits POST /api/cover-letter.
 *
 * Why a mutation, not a query: the call is expensive (qwen3:30b on local
 * Ollama, 30-90s) and SHOULD only fire on explicit user action. A query
 * would either run on mount (wasting compute) or need `enabled: false`
 * + manual refetch — at which point it's just a clumsier mutation.
 *
 * Why no global cache: each generation is one-off and stamped with a
 * timestamp on the server. The user can re-run with a different tone /
 * note and we want a fresh result, not a cached one.
 *
 * Errors surface via the standard ApiError path — caller renders
 * `error.message` inline. Common rejection: "No resume on file" when
 * the user hasn't uploaded yet.
 */
export function useGenerateCoverLetter() {
  return useMutation<CoverLetterResponse, Error, CoverLetterRequest>({
    mutationFn: (req) =>
      api.post<CoverLetterResponse>("/cover-letter", {
        // Pass through only the fields the server reads. Sending the
        // whole MatchPayload (with _starred, _seen, etc.) would work
        // but bloats the request body unnecessarily.
        job: {
          title: req.job.title,
          company: req.job.company,
          location: req.job.location,
          description: req.job.description,
          technologies: req.job.technologies,
          // Pass-through for the persisted-file header.
          _match_score: req.job._match_score,
        },
        tone: req.tone ?? "professional",
        custom_note: req.custom_note ?? "",
        model: req.model ?? "",
      }),
  });
}
