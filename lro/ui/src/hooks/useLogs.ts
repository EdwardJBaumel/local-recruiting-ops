import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useStatus } from "@/hooks/useStatus";

export interface LogTailResponse {
  path: string;
  lines: string[];
  exists: boolean;
}

export function useLogs(limit = 400) {
  const status = useStatus();
  const inProgress = status.data?.cycle_in_progress ?? false;

  return useQuery<LogTailResponse>({
    queryKey: ["logs", limit],
    queryFn: () => api.get<LogTailResponse>(`/logs?n=${limit}`),
    staleTime: 2000,
    refetchInterval: inProgress ? 3000 : 15000,
    placeholderData: (prev) => prev,
  });
}
