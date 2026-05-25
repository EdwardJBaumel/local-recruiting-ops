import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { StatusResponse } from "@/types/status";

interface RunResponse {
  ok: boolean;
  message?: string;
  error?: string;
  tiers?: string[];
}

/**
 * POST /api/run-cycle (fast tier) — kicks off a manual pipeline run.
 *
 * Surfaces server rejections explicitly. The v1 bug we're fixing here:
 * the old runCycle() swallowed non-OK responses, so when the backend
 * returned 409 ("cycle already in progress") the button looked stuck
 * with zero feedback. With useMutation, the .error and .data values
 * are first-class — the calling component reads them and renders.
 *
 * Optimistic status: mutation success clears isPending before the next
 * /api/status poll arrives, which used to flash "Run Pipeline" for one
 * frame. We set cycle_in_progress=true in the cache immediately.
 */
export function useRunPipeline() {
  const qc = useQueryClient();

  return useMutation<RunResponse, Error, void, { prev?: StatusResponse }>({
    mutationFn: async () => {
      const data = await api.post<RunResponse>("/run-cycle");
      if (!data.ok) throw new Error(data.error ?? "Pipeline rejected");
      return data;
    },
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: ["status"] });
      const prev = qc.getQueryData<StatusResponse>(["status"]);
      if (prev) {
        qc.setQueryData<StatusResponse>(["status"], {
          ...prev,
          cycle_in_progress: true,
          pipeline_running: true,
          progress: {
            ...prev.progress,
            stage: prev.progress?.stage ?? "ingest",
            stage_label: prev.progress?.stage_label ?? "Starting…",
            stage_index: prev.progress?.stage_index ?? 1,
          },
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(["status"], ctx.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["status"] });
    },
  });
}
