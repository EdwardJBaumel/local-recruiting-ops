import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

/**
 * Job-description summariser.
 *
 * Powers the "Summarize" button on the MatchDetail panel. Hits POST
 * /api/summarize with the job's URL; the backend looks up the entry in
 * the match registry, generates a 3-4 sentence prose summary via the
 * default model (qwen3:8b — cheap, snappy, good enough for "extract the
 * shape of this role"), and caches the result back into the registry's
 * `payload._summary` field.
 *
 * Why caching matters
 * -------------------
 * The button fires on user click. Without a cache, every click would
 * burn a 5-15s LLM call even if the user just wanted to re-read the
 * same summary. The backend short-circuits when `_summary` is already
 * set on the entry, so the first click pays the cost and subsequent
 * clicks return instantly. We DON'T cache on the FE side because
 * registry refreshes (cycle end, reset) need to invalidate this — and
 * the registry already polls every 2s, so the cached `_summary` flows
 * back to the FE through the normal useMatches() data path.
 *
 * After a successful summarise we invalidate the matches query so the
 * `_summary` field that just got persisted on the server appears on
 * the next refetch (2s max). The component using this hook can also
 * paint the returned `summary` text immediately for instant feedback,
 * without waiting for the refetch.
 */

export interface SummarizeRequest {
  /** The job's URL — primary key for registry lookup on the BE. */
  url: string;
  /** Optional fallbacks if the registry doesn't have the entry yet
   *  (rare race: user clicked summarise on a row that just streamed
   *  in but hasn't been persisted to disk). The BE will summarise
   *  from these inline values instead of failing. */
  description?: string;
  title?: string;
  company?: string;
  /** Force re-summarisation even if the registry has a cached value.
   *  Useful for the "regenerate" affordance — not bound to UI yet but
   *  available for future use. */
  force?: boolean;
}

export interface SummarizeResponse {
  ok: boolean;
  /** The summary itself. 3-4 sentences of prose. */
  summary: string;
  /** True if the backend returned a previously-persisted summary
   *  rather than calling the LLM. The UI uses this to show
   *  "regenerate" vs "summarize" affordance. */
  cached: boolean;
  /** True if the result was written back to the registry (false on
   *  the rare race where the entry wasn't found and we summarised
   *  from inline body fields). */
  persisted?: boolean;
  /** Model that produced this summary — included for traceability
   *  in the UI's "model: gemma3:12b" caption beneath the summary. */
  model: string;
}

export function useSummarizeJob() {
  const qc = useQueryClient();
  return useMutation<SummarizeResponse, Error, SummarizeRequest>({
    mutationFn: (req) =>
      api.post<SummarizeResponse>("/summarize", {
        url: req.url,
        description: req.description ?? "",
        title: req.title ?? "",
        company: req.company ?? "",
        force: req.force ?? false,
      }),
    onSuccess: (data) => {
      // Pull the next /api/matches refresh forward so the persisted
      // `_summary` field flows back into the FE's match list. Cheap —
      // useMatches polls every 2s anyway, this just makes the round
      // trip happen in <100ms instead of up to 2000ms.
      if (data.persisted) {
        qc.invalidateQueries({ queryKey: ["matches"] });
      }
    },
  });
}
