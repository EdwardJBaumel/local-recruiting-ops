import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { MarketCycleEntry } from "@/types/market";
import { useStatus } from "./useStatus";

/**
 * Market data is written once per cycle — there's no value in polling
 * it during a cycle. We refetch on the cycle-end transition only,
 * just like /api/matches' final pull.
 */
export function useMarket() {
  const status = useStatus();
  const qc = useQueryClient();
  const wasInProgressRef = useRef(false);

  useEffect(() => {
    const inProgress = status.data?.cycle_in_progress ?? false;
    if (wasInProgressRef.current && !inProgress) {
      qc.invalidateQueries({ queryKey: ["market"] });
    }
    wasInProgressRef.current = inProgress;
  }, [status.data?.cycle_in_progress, qc]);

  return useQuery<MarketCycleEntry[]>({
    queryKey: ["market"],
    queryFn: () => api.get<MarketCycleEntry[]>("/market"),
    staleTime: 1000 * 60, // a minute is fine — refresh on cycle end above
    refetchInterval: false,
    placeholderData: (prev) => prev,
  });
}
