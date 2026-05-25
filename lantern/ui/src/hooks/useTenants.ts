import { useMutation } from "@tanstack/react-query";
import { api } from "@/api/client";

/**
 * Test whether a Greenhouse / Lever / Ashby tenant slug actually
 * returns jobs before adding it to the config. POST /api/ingest/test
 * does the HTTP round-trip server-side and reports back:
 *   { ok, slug, kind, count, sample, error? }
 *
 * The shape uses `kind` for the ATS provider so the same endpoint
 * handles all three sources. Saves the user from typing a slug,
 * waiting for the next pipeline cycle, and only then finding out
 * the slug was wrong.
 */
export interface TestTenantResult {
  ok: boolean;
  kind?: string;
  slug?: string;
  /** Count of OPEN jobs the API returned. Doesn't mean PM-matching;
   *  that filtering happens later in the pipeline. */
  count?: number;
  /** First job title, useful as a "yes this is the right company" sanity check. */
  sample?: string;
  error?: string;
}

interface TestTenantArgs {
  kind: "greenhouse" | "lever" | "ashby";
  slug: string;
  display?: string;
}

export function useTestTenant() {
  return useMutation<TestTenantResult, Error, TestTenantArgs>({
    mutationFn: (args) => api.post<TestTenantResult>("/ingest/test", args),
  });
}
