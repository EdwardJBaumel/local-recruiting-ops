import type { MatchPayload } from "@/types/match";
import { getRemoteRegion } from "@/lib/geocode";

export interface MatchFilters {
  archetype: string | null;
  starredOnly: boolean;
  unseenOnly: boolean;
  showDismissed: boolean;
  hideForeignRemote: boolean;
}

export const DEFAULT_MATCH_FILTERS: MatchFilters = {
  archetype: null,
  starredOnly: false,
  unseenOnly: false,
  showDismissed: false,
  hideForeignRemote: true,
};

export interface MatchFilterStats {
  foreignRemoteDropped: number;
  dismissedHidden: number;
}

export interface FilteredMatches {
  rows: MatchPayload[];
  stats: MatchFilterStats;
}

function remoteLocation(row: MatchPayload): string {
  return row.location ?? row._location ?? row.work_mode ?? "";
}

/**
 * Registry-backed row filters (Starred / Unseen / Show dismissed / US-only remote / tier / archetype).
 * Location allow/block, title block, and freshness stay in Matches.tsx for config-driven instant feedback.
 */
export function filterMatchRows(rows: MatchPayload[], filters: MatchFilters): FilteredMatches {
  const stats: MatchFilterStats = { foreignRemoteDropped: 0, dismissedHidden: 0 };
  const out: MatchPayload[] = [];

  for (const row of rows) {
    if (row._removed) continue;

    if (row._match_tier === "maybe" && !row._starred) continue;
    if (row._match_tier && row._match_tier !== "match" && !row._starred) continue;

    if (!filters.showDismissed && row._dismissed) {
      stats.dismissedHidden += 1;
      continue;
    }

    if (filters.starredOnly && !row._starred) continue;
    if (filters.unseenOnly && row._seen) continue;
    if (filters.archetype && row.archetype !== filters.archetype) continue;

    if (filters.hideForeignRemote) {
      const region = getRemoteRegion(remoteLocation(row));
      if (region === "foreign") {
        stats.foreignRemoteDropped += 1;
        continue;
      }
    }

    out.push(row);
  }

  return { rows: out, stats };
}
