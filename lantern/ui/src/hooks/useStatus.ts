import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { StatusResponse } from "@/types/status";

/**
 * /api/status is the heartbeat — it's the only endpoint we poll
 * unconditionally. Every other server-state hook gates its
 * refetchInterval on this hook's `cycle_in_progress`.
 *
 * Cadence:
 *   - 1s mid-cycle (so the stage badge + progress feel live)
 *   - 2s at rest
 *   - 15s when the last poll FAILED — backs off the retry storm if
 *     the backend is fully down (Python server not running, mid-
 *     restart, etc.). Without this, every 2s fired 4 attempts (1 +
 *     3 default retries) AND triggered every other on-mount query
 *     to do the same. Net effect: dozens of ECONNREFUSED log lines
 *     per second for the user, all noise.
 *
 * `retry: 1` cuts each failed query from 4 attempts to 2, which
 * still tolerates a transient blip but doesn't spam the console.
 */
export function useStatus() {
  return useQuery<StatusResponse>({
    queryKey: ["status"],
    queryFn: () => api.get<StatusResponse>("/status"),
    refetchInterval: (query) => {
      if (query.state.error) return 15000;
      return query.state.data?.cycle_in_progress ? 1000 : 2000;
    },
    retry: 1,
    // Keep prior data on errors so a transient backend blip doesn't
    // flash the UI to "disconnected" for 50ms.
    placeholderData: (prev) => prev,
  });
}
