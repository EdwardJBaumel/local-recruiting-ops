import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/**
 * GET /api/system-info — one-shot hardware capability snapshot.
 *
 * Powers the Settings → Models banner that tells the user
 * concretely "you have a 16 GB GPU — qwen3:14b fits comfortably"
 * instead of generic "if you have a beefy card" advice. Includes:
 *   - GPU name + VRAM (via nvidia-smi when present, otherwise null)
 *   - PyTorch CUDA build state (so we surface the CPU-only-torch
 *     bug at the picker, not just buried in the launcher log)
 *
 * Same retry policy as useOllamaModels — both endpoints can hit the
 * backend-boot-race window on a fresh launcher start.
 */
export interface GpuInfo {
  name: string;
  vram_gb: number | null;
  driver_version: string | null;
  compute_capability: string | null;
  error?: string;
}

export interface TorchInfo {
  available: boolean;
  version: string | null;
  cuda: string | null;
  device: "cuda" | "cpu";
  error?: string;
}

export interface SystemInfoResponse {
  ok: boolean;
  gpu: GpuInfo | null;
  torch: TorchInfo;
  host: string;
}

export function useSystemInfo() {
  return useQuery<SystemInfoResponse>({
    queryKey: ["system-info"],
    queryFn: () => api.get<SystemInfoResponse>("/system-info"),
    staleTime: Infinity, // hardware doesn't change during a session
    refetchInterval: false,
    retry: 5,
    retryDelay: (attempt) => Math.min(4000, 500 * 2 ** attempt),
    refetchOnWindowFocus: true,
  });
}
