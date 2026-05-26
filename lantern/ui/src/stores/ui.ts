import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_MATCH_FILTERS, type MatchFilters } from "@/lib/matchFilters";

/**
 * UI state — anything that's purely client-side, not server-derived.
 * Server data (matches, config, status) lives in TanStack Query. Form
 * state lives in react-hook-form. Everything else lives here.
 *
 * `persist` keeps `currentTab` and table filters across reloads so a
 * dashboard refresh doesn't yank you back to Brief.
 */
type Tab = "brief" | "matches" | "history" | "settings";

export type SortKey = "score" | "company" | "role" | "posted" | "ghost" | "location";

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

      matchFilters: DEFAULT_MATCH_FILTERS,
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
