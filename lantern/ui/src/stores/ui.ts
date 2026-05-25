import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * UI state — anything that's purely client-side, not server-derived.
 * Server data (matches, config, status) lives in TanStack Query. Form
 * state lives in react-hook-form. Everything else lives here.
 *
 * `persist` keeps `currentTab` and table filters across reloads so a
 * dashboard refresh doesn't yank you back to Brief.
 */
type Tab = "brief" | "matches" | "settings";

export type SortKey = "score" | "company" | "role" | "posted" | "ghost" | "location";

interface MatchFilters {
  archetype: string | null;
  starredOnly: boolean;
  showDismissed: boolean;
  unseenOnly: boolean;
  /**
   * Drop "Remote - UK / Canada / EU / etc." postings — the kind that
   * use a remote token but are scoped to a non-US country and almost
   * always require local work authorisation. Default ON because that's
   * what a US-based candidate wants 95% of the time. Toggle OFF if
   * you're actually open to relocating or have multi-country auth.
   */
  hideForeignRemote: boolean;
  // NOTE: `windowDays` lived here through v2/v3. It's now sourced from
  // `config.preferences.freshness_window_*_days` so the value persists
  // across reloads/devices and can be tuned per company-size tier.
  // Bump persist version when removing fields like this so existing
  // users don't carry a stale `windowDays: 14` in localStorage that
  // confuses the next code path that adds back a `windowDays` field.
}

interface MatchSort {
  key: SortKey;
  dir: "asc" | "desc";
}

interface UIState {
  currentTab: Tab;
  setCurrentTab: (t: Tab) => void;

  selectedJobUrl: string | null;
  setSelectedJobUrl: (url: string | null) => void;

  matchFilters: MatchFilters;
  setMatchFilter: <K extends keyof MatchFilters>(k: K, v: MatchFilters[K]) => void;

  matchSort: MatchSort;
  setMatchSort: (s: MatchSort) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      currentTab: "brief",
      setCurrentTab: (t) => set({ currentTab: t }),

      selectedJobUrl: null,
      setSelectedJobUrl: (url) => set({ selectedJobUrl: url }),

      matchFilters: {
        archetype: null,
        starredOnly: false,
        showDismissed: false,
        unseenOnly: false,
        // ON by default — most users in the US don't have UK / Canada
        // / EU work auth. They can flip it off in one click if they do.
        hideForeignRemote: true,
      },
      setMatchFilter: (k, v) =>
        set((s) => ({ matchFilters: { ...s.matchFilters, [k]: v } })),

      matchSort: { key: "score", dir: "desc" },
      setMatchSort: (s) => set({ matchSort: s }),
    }),
    {
      name: "lantern-ui",
      // Bump this when default values change so existing users pick
      // up new defaults instead of being stuck on a localStorage value
      // baked in from an old build.
      version: 4,
      // Don't persist `selectedJobUrl` — it's session-only state. A
      // refresh should land you on the table, not deep into a row.
      partialize: (s) => ({
        currentTab: s.currentTab,
        matchFilters: s.matchFilters,
        matchSort: s.matchSort,
      }),
    },
  ),
);
