/**
 * Single typed fetch wrapper for Local Recruiting Ops's backend. Vite dev-server
 * proxies /api/* to the Python backend on :8099 so we never touch
 * an absolute URL from the client. Keeps the entire surface here so
 * if we ever swap to FastAPI / a different host, this is the only
 * file that changes.
 */

const BASE = "/api";

class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  // Try to parse JSON either way so error bodies are inspectable.
  let body: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const detail =
      body && typeof body === "object" && "error" in body
        ? String((body as { error: unknown }).error)
        : `HTTP ${res.status}`;
    throw new ApiError(res.status, body, detail);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, payload?: unknown) =>
    request<T>(path, { method: "POST", body: payload === undefined ? undefined : JSON.stringify(payload) }),
  // NOTE: a `postFile` helper used to live here for multipart uploads.
  // It was removed because the only caller (resume upload) now sends
  // base64-in-JSON instead — see hooks/useResume.ts:fileToBase64 for
  // the rationale. Add it back if a future endpoint genuinely needs
  // multipart, but the BE's stdlib http.server can't parse multipart
  // without hand-rolling a boundary parser, so JSON-with-base64 is
  // the easier path.
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export { ApiError };
