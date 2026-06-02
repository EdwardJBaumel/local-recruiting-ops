import { useMemo } from "react";
import type { MatchPayload } from "@/types/match";
import { filterMatchRows, type MatchFilters } from "@/lib/matchFilters";

/** Client-side registry filters — use in Matches.tsx instead of inline useMemo. */
export function useFilteredMatches(all: MatchPayload[] | undefined, filters: MatchFilters) {
  return useMemo(() => filterMatchRows(all ?? [], filters), [all, filters]);
}
