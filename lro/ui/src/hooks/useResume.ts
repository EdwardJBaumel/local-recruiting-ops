import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { ResumeProfile, ResumeState } from "@/types/config";

/** Hydrate-once same as config — user-driven, no background mutation. */
export function useResume() {
  return useQuery<ResumeState>({
    queryKey: ["resume"],
    queryFn: () => api.get<ResumeState>("/resume"),
    staleTime: Infinity,
    refetchInterval: false,
  });
}

/** The structured profile (parsed fields). Hydrates after upload / re-parse. */
export function useResumeProfile() {
  return useQuery<ResumeProfile | null>({
    queryKey: ["resume-profile"],
    queryFn: async () => {
      const data = await api.get<{ profile?: ResumeProfile; status?: string }>("/resume/profile");
      return data?.profile ?? null;
    },
    staleTime: Infinity,
    refetchInterval: false,
  });
}

/**
 * Read a File into a base64 string (sans the `data:...;base64,` prefix).
 * Used by the resume upload mutation below to convert the picked file
 * into the wire format the backend expects.
 *
 * Why base64-in-JSON instead of multipart/form-data
 * -------------------------------------------------
 * The backend's HTTP server is Python's stdlib `http.server`, which
 * doesn't parse multipart bodies natively (you'd have to hand-roll a
 * boundary parser using `email.parser` or `cgi.FieldStorage`). The
 * JSON-with-base64 path keeps the server logic to one parse call. The
 * 33% size overhead from base64 is negligible for resume PDFs (~100
 * KB → ~130 KB on the wire).
 */
async function fileToBase64(file: File): Promise<string> {
  const dataUrl: string = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error("FileReader failed"));
    reader.readAsDataURL(file);
  });
  // `dataUrl` looks like "data:application/pdf;base64,JVBERi0xLjQK..."
  // We only want the base64 portion after the comma.
  const comma = dataUrl.indexOf(",");
  return comma >= 0 ? dataUrl.slice(comma + 1) : "";
}

export function useUploadResume() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, File>({
    mutationFn: async (file) => {
      const contentBase64 = await fileToBase64(file);
      // Backend contract: POST /api/resume expects
      // { filename: string, content_base64: string }.
      // Returns { ok: true, metadata: {...} } on success.
      return api.post("/resume", {
        filename: file.name,
        content_base64: contentBase64,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["resume"] });
      qc.invalidateQueries({ queryKey: ["resume-profile"] });
    },
  });
}

export function useReparseResume() {
  const qc = useQueryClient();
  return useMutation<{ ok: boolean; profile: ResumeProfile }, Error, void>({
    mutationFn: () => api.post("/resume/reparse"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["resume-profile"] });
    },
  });
}

/**
 * Save user-edited overrides to the parsed profile. Sends only the
 * fields the user changed; the backend merges them into profile.json
 * and stamps `_user_edited: true` so the UI can show "edited" state.
 *
 * Re-parsing later overwrites these edits — that's intentional. Edits
 * are a "I know better than the parser" override, not a permanent
 * lock. The user can re-edit any time.
 */
export function useSaveProfile() {
  const qc = useQueryClient();
  return useMutation<{ ok: boolean; profile: ResumeProfile }, Error, Partial<ResumeProfile>>({
    mutationFn: (patch) => api.post("/resume/profile", patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["resume-profile"] });
    },
  });
}
