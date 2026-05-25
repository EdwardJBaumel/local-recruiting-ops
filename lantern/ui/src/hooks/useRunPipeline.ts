import { useMutation } from "@tanstack/react-query";
import { api } from "@/api/client";

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
 */
export function useRunPipeline() {
  return useMutation<RunResponse, Error, void>({
    mutationFn: async () => {
      const data = await api.post<RunResponse>("/run-cycle");
      // 200 OK with ok:false is the "rejected for business reasons"
      // case (cycle already running, setup not complete). Promote
      // it to a thrown error so the UI's error path renders.
      if (!data.ok) throw new Error(data.error ?? "Pipeline rejected");
      return data;
    },
  });
}

