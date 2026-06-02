import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

/**
 * Mirrors what core/reset_history.reset_history actually returns. Was
 * `removed: {matches, cycles, digests, parsed}` in an earlier version
 * which has never been the wire shape — backend returns flat lists of
 * cleared / skipped / errored target names. The DangerSection card
 * surfaces `cleared` so the user sees concrete confirmation of what
 * was wiped.
 */
interface ResetResponse {
  ok: boolean;
  cleared?: string[];
  skipped?: string[];
  errors?: { target: string; error: string }[];
  error?: string;
}

/**
 * POST /api/reset-history — wipes per-cycle data:
 *   - data/matches/ (per-cycle history), legacy parsed/ and fit_gaps/
 *   - data/digests/* (when digest has run)
 *   - data/match_registry.json (so star / dismiss flags reset too)
 *   - data/seen_urls.json, data/seen_jobs.json (URL dedupe)
 *   - data/cycle_times.json, data/market_intel.json (aggregates)
 *   - data/dead_slugs.json
 *
 * Preserved: resume, config, tracker, decision_log,
 * feedback_embeddings (so the LEARNING from past stars/dismisses
 * survives even though the visible flag does not).
 *
 * Backend rejects with HTTP 409 if a cycle is in progress. The
 * DangerSection surfaces that error verbatim so the user knows to
 * wait.
 *
 * Cache cleanup
 * -------------
 * On success we have to do MORE than `invalidateQueries`. Both
 * useMatches() and useMarket() use `placeholderData: (prev) => prev`
 * to keep the table painted while a refetch is in flight — that
 * means after invalidation, the stale Top-hiring-companies / match
 * rows linger on screen until the new fetch resolves. Worse, if the
 * user is on the Settings tab (where Reset lives) when they click,
 * those queries are inactive and won't even trigger a refetch until
 * the user navigates back to Brief / Matches; the stale cache is
 * served instantly on remount.
 *
 * The fix is `removeQueries`: nuke the cached data entirely. With no
 * `prev` to fall back on, the next mount sees `data === undefined`
 * and renders the empty-state placeholder while the refetch runs.
 * status stays on `invalidateQueries` because it's actively polling
 * and has no placeholderData fallback to worry about.
 */
export function useResetHistory() {
  const qc = useQueryClient();
  return useMutation<ResetResponse, Error, { force?: boolean } | void>({
    mutationFn: async (input) => {
      const force = !!(input && typeof input === "object" && "force" in input && input.force);
      const data = await api.post<ResetResponse>("/reset-history", force ? { force: true } : undefined);
      if (!data.ok) throw new Error(data.error ?? "Reset failed");
      return data;
    },
    onSuccess: () => {
      // Hard reset: drop the cached data so the placeholderData
      // fallback can't paint stale rows during the next refetch.
      qc.removeQueries({ queryKey: ["matches"] });
      qc.removeQueries({ queryKey: ["market"] });
      qc.removeQueries({ queryKey: ["cycle-history"] });
      // status auto-polls and has no placeholderData, so a plain
      // invalidate is enough — the next 2s heartbeat will repopulate.
      qc.invalidateQueries({ queryKey: ["status"] });
    },
  });
}
