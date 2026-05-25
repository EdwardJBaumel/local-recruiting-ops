import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "@/api/client";
import type { MatchPayload } from "@/types/match";
import { useStatus } from "./useStatus";

/**
 * Match list. Polls every 2 s while a cycle is in flight so new rows
 * stream live. Otherwise quiet — sit on the cached list.
 *
 * Cycle-edge refetches
 * --------------------
 * Two transitions force an immediate invalidate:
 *   - Rising edge (cycle starts): the query may be in an `error` state
 *     from the dashboard-loaded-before-API-was-ready race at boot. Vite
 *     comes up in ~150 ms, the Python API takes ~10–20 s to load the
 *     embedding model + Ollama prewarm, so the first ~3 fetches in that
 *     window get ECONNREFUSED. TanStack Query's default retry burns 3
 *     attempts inside the boot window then parks the query in `error`,
 *     which does NOT auto-recover — `refetchInterval` is never armed
 *     because `data === undefined`. Without a rising-edge invalidate
 *     the user clicks Run Pipeline and sees zero matches stream in
 *     until they hard-refresh the tab. Forcing an invalidate here
 *     restarts the fetch with a fresh retry counter.
 *   - Falling edge (cycle ends): pulls one final time so the table
 *     reflects the completed cycle even if the last 2 s poll didn't
 *     line up with the registry's final flush.
 *
 * Polling condition
 * -----------------
 * Was: `q.state.data !== undefined && cycle_in_progress`. The first
 * clause meant a query stuck in error state never repolled — the same
 * boot-race symptom above. We now poll whenever a cycle is running,
 * regardless of whether the cached data is empty / undefined / errored.
 * Cost: one HTTP request every 2 s during a cycle even on a fresh
 * mount. Negligible (the registry endpoint is mtime-cached on the
 * server).
 */
export function useMatches() {
  const status = useStatus();
  const queryClient = useQueryClient();
  const wasInProgressRef = useRef(false);

  useEffect(() => {
    const inProgress = status.data?.cycle_in_progress ?? false;
    // Rising edge OR falling edge — both invalidate so a stuck error
    // state can't survive a cycle boundary.
    if (wasInProgressRef.current !== inProgress) {
      queryClient.invalidateQueries({ queryKey: ["matches"] });
    }
    wasInProgressRef.current = inProgress;
  }, [status.data?.cycle_in_progress, queryClient]);

  return useQuery<MatchPayload[]>({
    queryKey: ["matches"],
    queryFn: () => api.get<MatchPayload[]>("/matches"),
    refetchInterval: () => (status.data?.cycle_in_progress ? 2000 : false),
    placeholderData: (prev) => prev,
  });
}
