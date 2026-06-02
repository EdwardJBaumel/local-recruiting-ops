import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { AppConfig } from "@/types/config";

/**
 * Config is user-driven state — only this UI mutates it. Hydrate-once
 * via `staleTime: Infinity`; only refetch after a successful save
 * mutation invalidates the cache. This is the explicit fix for the
 * "polling clobbered my typing" bug from v1: we never refetch config
 * while the user might be editing it.
 */
export function useConfig() {
  return useQuery<AppConfig>({
    queryKey: ["config"],
    queryFn: () => api.get<AppConfig>("/config"),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
  });
}

export function useSaveConfig() {
  const queryClient = useQueryClient();
  return useMutation<AppConfig, Error, Partial<AppConfig>>({
    mutationFn: (patch) => api.post<AppConfig>("/config", patch),
    onSuccess: () => {
      // Invalidate so the next read pulls the canonical merged result
      // (the backend shallow-merges incoming patches, so we want the
      // server's view, not just the patch we sent).
      queryClient.invalidateQueries({ queryKey: ["config"] });
    },
  });
}
