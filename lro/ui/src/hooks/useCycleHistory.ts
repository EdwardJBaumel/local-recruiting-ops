import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useStatus } from "@/hooks/useStatus";
import type { LastCycleStats } from "@/types/status";

export interface CycleHistoryEntry extends LastCycleStats {
  cycle: number;
  seconds: number;
  ts: string;
}

/**
 * Long-run funnel history from cycle_times.json. This is what powers the
 * History tab and any trend charts; matches/cycle_*.json is the deeper
 * audit trail, not the chart source.
 */
export function useCycleHistory(limit = 500) {
  const status = useStatus();
  const qc = useQueryClient();
  const wasInProgressRef = useRef(false);

  useEffect(() => {
    const inProgress = status.data?.cycle_in_progress ?? false;
    if (wasInProgressRef.current && !inProgress) {
      qc.invalidateQueries({ queryKey: ["cycle-history"] });
    }
    wasInProgressRef.current = inProgress;
  }, [status.data?.cycle_in_progress, qc]);

  return useQuery<CycleHistoryEntry[]>({
    queryKey: ["cycle-history", limit],
    queryFn: () => api.get<CycleHistoryEntry[]>(`/cycle-history?n=${limit}`),
    staleTime: 1000 * 60,
    refetchInterval: status.data?.cycle_in_progress ? 5000 : false,
    placeholderData: (prev) => prev,
  });
}
