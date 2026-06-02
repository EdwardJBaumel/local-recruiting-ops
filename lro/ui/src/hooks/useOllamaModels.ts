import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/**
 * GET /api/ollama-models — discovers what's installed locally so the
 * Settings → Models picker can render a real dropdown instead of asking
 * users to type model names by hand.
 *
 * Why this query is fussier than the others
 * -----------------------------------------
 * Original version had `retry: 0` + `staleTime: Infinity`, on the
 * theory that "Ollama is either up or down, telling the user once is
 * enough." That assumption broke on every cold launch: the FE mounts
 * in ~150 ms, the backend takes 10-20s to load the embedding model
 * before /api/* starts serving. The first /api/ollama-models call
 * during that window gets ECONNREFUSED, the query parks in error
 * state with `data = undefined`, and the Settings → Models page later
 * renders every preset as "(not installed)" because `installed = []`.
 *
 * Fix: retry the call a few times with exponential backoff so the
 * boot-race resolves itself, AND refetch on window focus so coming
 * back to the dashboard after a model pull is reflected automatically.
 *
 * We keep `staleTime: Infinity` because Ollama's installed-models list
 * doesn't change during a session unless the user explicitly pulls or
 * removes a model — and either of those would trigger a focus event
 * when they switch back to the dashboard tab.
 *
 * Wire shape — returns ok:false (with empty models[]) when Ollama
 * isn't reachable. UI uses that to render a "start Ollama" hint
 * inline rather than a blank dropdown.
 */
export interface OllamaModelsResponse {
  ok: boolean;
  models: string[];
  host: string;
  error?: string;
}

export function useOllamaModels() {
  return useQuery<OllamaModelsResponse>({
    queryKey: ["ollama-models"],
    queryFn: () => api.get<OllamaModelsResponse>("/ollama-models"),
    staleTime: Infinity,
    refetchInterval: false,
    // Retry to survive the 10-20s API boot window. Exponential
    // backoff caps at 4s so we settle by ~10s total wall clock.
    retry: 5,
    retryDelay: (attempt) => Math.min(4000, 500 * 2 ** attempt),
    // Refetch when the user comes back to the tab. Covers both the
    // boot-race recovery and the "I just pulled a new model" flow.
    refetchOnWindowFocus: true,
  });
}
