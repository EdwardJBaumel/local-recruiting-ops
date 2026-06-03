import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "@/api/client";
import type { MatchPayload } from "@/types/match";
import { useStatus } from "./useStatus";

/**
 * Match list. Polls every 2 s while a cycle is in flight so new rows
 * stream live. Otherwise quiet — sit on the cached list.
 *
 * Cycle-edge refetch
 * ------------------
 * Falling edge (cycle ends): final pull after the registry flush.
 * Rising edge: invalidate only if the query is stuck in `error` (boot
 * race before the API is up). Normal cycle starts keep the cached list
 * via `placeholderData` and the 2 s poll — no wipe on click.
 *
 * Polling condition
 * -----------------
 * Poll whenever a cycle is running, regardless of cached data state.
 */
export function useMatches() {
  const status = useStatus();
  const queryClient = useQueryClient();
  const wasInProgressRef = useRef(false);

  useEffect(() => {
    const inProgress = status.data?.cycle_in_progress ?? false;
    if (wasInProgressRef.current !== inProgress) {
      if (!inProgress) {
        queryClient.invalidateQueries({ queryKey: ["matches"] });
      } else if (queryClient.getQueryState<MatchPayload[]>(["matches"])?.status === "error") {
        queryClient.invalidateQueries({ queryKey: ["matches"] });
      }
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
