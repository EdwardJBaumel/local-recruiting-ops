import { useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useMatches } from "@/hooks/useMatches";
import { useFilteredMatches } from "@/hooks/useFilteredMatches";
import { useConfig } from "@/hooks/useConfig";
import { useUIStore } from "@/stores/ui";
import { MatchTable } from "@/components/MatchTable";
import { MatchDetail } from "@/components/MatchDetail";
import { useStatus } from "@/hooks/useStatus";
import { isCountryOnlyLocation } from "@/lib/geocode";
import { classifyCompany, DEFAULT_FRESHNESS_WINDOWS } from "@/lib/companyTier";
import { MapPin } from "lucide-react";

/**
 * Matches view — the headline of the app.
 *
 * Layout:
 *   left  → MatchTable (TanStack Table over the matches array)
 *   right → MatchDetail (sticky panel, driven by selectedJobUrl)
 *
 * Filtering happens here (not inside MatchTable) so the table stays
 * a pure "render rows" component. Filter state lives in Zustand
 * because we want it to survive a tab switch (you go to Settings,
 * tweak ghost weight, come back — your filters are still there).
 */
export function Matches() {
  const matches = useMatches();
  const status = useStatus();
  const config = useConfig();
  const filters = useUIStore((s) => s.matchFilters);
  const { rows: registryRows, stats: filterStats } = useFilteredMatches(matches.data, filters);
  // Pulled at the top so the conditional layout below knows whether to
  // render the right-rail detail panel.
  const selectedUrl = useUIStore((s) => s.selectedJobUrl);

  // Pull location-filter prefs from the saved config. Reading them
  // CLIENT-side gives instant feedback when the user saves the
  // allow/block lists — they don't have to wait for the next
  // pipeline cycle to confirm the filter is doing something. Server
  // still applies the same rules at scoring time so the registry
  // stays clean.
  //
  // Each useMemo is defensively type-checked: if the config has a
  // malformed shape (rare, but possible if someone hand-edited
  // config.json), we treat it as an empty filter rather than
  // crashing the whole tab on a `.map is not a function` throw.
  //
  // The pin-on-a-map filter was removed Apr 2026; only the two
  // text lists remain (allow + block).
  const prefs = config.data?.preferences ?? {};
  const allowedLocs = useMemo(() => {
    const raw = prefs.allowed_locations;
    return Array.isArray(raw) ? raw.filter((s): s is string => typeof s === "string").map((s) => s.toLowerCase()) : [];
  }, [prefs.allowed_locations]);
  const blockedLocs = useMemo(() => {
    const raw = prefs.blocked_locations;
    return Array.isArray(raw) ? raw.filter((s): s is string => typeof s === "string").map((s) => s.toLowerCase()) : [];
  }, [prefs.blocked_locations]);

  // Title block list — precompiled to whole-word regexes so the row
  // filter below is a cheap .test() per row, not a per-row RegExp
  // build. Word-boundary (\b) matching mirrors the backend: "engineer"
  // hides "Software Engineer" but not "Engineering". The backend ALSO
  // skips these at scrape time, so this client filter mainly catches
  // registry rows that were scored before the block list was edited.
  const blockedTitleRe = useMemo(() => {
    const raw = prefs.blocked_title_keywords;
    if (!Array.isArray(raw)) return [] as RegExp[];
    return raw
      .filter((s): s is string => typeof s === "string")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean)
      .map((kw) => new RegExp(`\\b${kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i"));
  }, [prefs.blocked_title_keywords]);

  // Per-tier freshness windows. Each value is days; 0 = no filter for
  // that tier. Defaults sourced from companyTier.ts so the UI
  // behaves sensibly before the user has saved a config.
  const freshnessWindows = useMemo(() => ({
    mega:   typeof prefs.freshness_window_mega_days   === "number" ? prefs.freshness_window_mega_days   : DEFAULT_FRESHNESS_WINDOWS.mega,
    large:  typeof prefs.freshness_window_large_days  === "number" ? prefs.freshness_window_large_days  : DEFAULT_FRESHNESS_WINDOWS.large,
    growth: typeof prefs.freshness_window_growth_days === "number" ? prefs.freshness_window_growth_days : DEFAULT_FRESHNESS_WINDOWS.growth,
  }), [prefs.freshness_window_mega_days, prefs.freshness_window_large_days, prefs.freshness_window_growth_days]);

  // Apply UI-side filters in a useMemo so we don't re-run on every
  // unrelated render. Sort happens in MatchTable via TanStack — this
  // function only filters.
  // Track HOW MANY rows each filter dropped so we can surface "20
  // dropped by location, 8 dropped by age" — the user gets immediate
  // feedback that filters are doing something.
  const { filtered, droppedByLocation, droppedByAge, droppedByTitle } = useMemo(() => {
    const nowMs = Date.now();
    let droppedByLocation = 0;
    let droppedByAge = 0;
    let droppedByTitle = 0;
    const out = registryRows.filter((m) => {
      // Title block list — wrong-discipline titles the user excluded
      // in Settings. Cheap whole-word regex test; runs before the
      // freshness / location checks since it's the cheapest gate.
      if (blockedTitleRe.length) {
        const title = m.title ?? "";
        if (blockedTitleRe.some((re) => re.test(title))) {
          droppedByTitle++;
          return false;
        }
      }
      // Per-tier freshness window. classifyCompany() picks "mega" /
      // "large" / "growth" from a hand-curated client-side list, and
      // each tier reads its own window from Settings. Rows with no
      // posted_date pass through (we can't date them) — the ghost
      // detector caps undated rows at Clear so they're not a
      // credibility risk. Window = 0 disables filtering for that
      // tier (e.g. "I want to see all Amazon reqs regardless of age").
      if (m.posted_date) {
        const tier = classifyCompany(m.company);
        const winDays = freshnessWindows[tier];
        if (winDays > 0) {
          const ts = Date.parse(m.posted_date);
          if (Number.isFinite(ts) && nowMs - ts > winDays * 86400000) {
            droppedByAge++;
            return false;
          }
        }
      }
      // Location filter — same OR semantics as the backend, applied
      // here for instant UI feedback.
      const loc = m.location ?? m._location ?? "";
      const locLower = loc.toLowerCase();

      // 1. Block list always wins.
      if (blockedLocs.length && blockedLocs.some((b) => locLower.includes(b))) {
        droppedByLocation++;
        return false;
      }
      // 2. Allow list inclusion. Empty allow list = no filter (every
      //    location is fine). Country-only locations ("United States",
      //    "Canada", etc.) get the benefit of the doubt — see
      //    isCountryOnlyLocation for rationale. Mirrors backend.
      if (allowedLocs.length) {
        const countryOnly = isCountryOnlyLocation(loc);
        const textPasses = allowedLocs.some((a) => locLower.includes(a));
        if (!countryOnly && !textPasses) {
          droppedByLocation++;
          return false;
        }
      }
      return true;
    });
    return { filtered: out, droppedByLocation, droppedByAge, droppedByTitle };
  }, [registryRows, allowedLocs, blockedLocs, blockedTitleRe, freshnessWindows]);

  const locationFilterActive = allowedLocs.length > 0 || blockedLocs.length > 0;

  // Layout structure: header row (counts + legend + filters) ALWAYS
  // spans the full viewport width, then below it a conditional grid
  // that splits 1fr / 360px when a row is selected — otherwise the
  // table takes the full width. Keeping the header outside the grid
  // matters because it carries cross-cutting info (filter chips,
  // dropped-row counts, the ghost-score legend) that applies to the
  // whole view, not just the table column. When it lived INSIDE the
  // left grid column it got compressed to that column's width and
  // looked like it belonged to the table only.
  // Breakpoint is `md:` (768px) so side-by-side kicks in on smaller
  // laptop screens / browser-with-sidebar widths.
  return (
    <div className="space-y-3">
      {/* Full-width header column. Three lines, all left-aligned:
            1. Counts + filter chips (location filter, age dropped)
            2. Ghost-score legend
            3. FilterBar (Starred / Unseen / Show dismissed / age select)
          We previously used `justify-between` which pinned FilterBar to
          the far right edge — on a wide viewport that produced a
          weird ~1000px dead gap between the counts and the controls.
          Stacking is the cleaner read: eye scans left-to-right per row
          and there's no awkward whitespace island. */}
      <div className="space-y-1.5">
        <p className="text-sm text-muted-foreground flex items-center gap-2 flex-wrap">
          <span>
            {filtered.length} of {matches.data?.length ?? 0} matches
          </span>
          {status.data?.cycle_in_progress && (
            <span className="text-accent">· cycle in progress, more landing live</span>
          )}
          {locationFilterActive && (
            <Badge
              variant="outline"
              className="gap-1 text-[10px]"
              title={`Allow: ${allowedLocs.length} · Block: ${blockedLocs.length}`}
            >
              <MapPin className="h-3 w-3" />
              Location filter on
              {droppedByLocation > 0 && ` · dropped ${droppedByLocation}`}
            </Badge>
          )}
          {droppedByAge > 0 && (
            <Badge
              variant="outline"
              className="text-[10px]"
              title={`Per-tier freshness windows from Settings — mega ${freshnessWindows.mega}d, large ${freshnessWindows.large}d, growth ${freshnessWindows.growth}d.`}
            >
              Freshness filter dropped {droppedByAge}
            </Badge>
          )}
          {filters.hideForeignRemote && filterStats.foreignRemoteDropped > 0 && (
            <Badge
              variant="outline"
              className="text-[10px]"
              title="Remote postings scoped to UK / Canada / EU / etc. — usually require local work authorisation."
            >
              Foreign-remote dropped {filterStats.foreignRemoteDropped}
            </Badge>
          )}
          {droppedByTitle > 0 && (
            <Badge
              variant="outline"
              className="text-[10px]"
              title="Titles matching your block list (Settings → Job titles). Whole-word matched — 'engineer' hides 'Software Engineer' but not 'Engineering'."
            >
              Title filter dropped {droppedByTitle}
            </Badge>
          )}
        </p>
        {/* Three-line metrics legend. Spelling out all three columns
            (not just Ghost) because users were left guessing what the
            difference between Fit and Score meant. */}
        <div className="text-xs text-muted-foreground space-y-0.5">
          <p>
            <span className="font-medium text-foreground/80">Fit</span> = how aligned the JD reads against your resume — cosine similarity (BAAI/bge-m3 embeddings) plus small adjustments for title, location, salary, and years of experience.
          </p>
          <p>
            <span className="font-medium text-foreground/80">Score</span> = Fit after the Ghost penalty knocks it down, then rescaled into a 5-98% display band. <span className="italic">This is the column the table sorts by.</span>
          </p>
          <p>
            <span className="font-medium text-foreground/80">Ghost</span> = 0-100 suspicion the posting is stale or padding the funnel (age, missing fields, duplicate titles). Postings without a posted-date default to <span className="text-ghost-clear font-medium">Clear</span>.{" "}
            <span className="text-ghost-clear font-medium">Clear</span> &lt;30 ·{" "}
            <span className="text-ghost-aging font-medium">Caution</span> 30-44 ·{" "}
            <span className="text-ghost-suspect font-medium">Suspect</span> ≥45.
          </p>
        </div>
        <div className="pt-1">
          <FilterBar />
        </div>
      </div>

      {/* Below-the-header content: table on the left, optional detail
          rail on the right. Grid only kicks in when something's
          selected — otherwise the table claims the full width. */}
      <div className={selectedUrl ? "grid grid-cols-1 md:grid-cols-[1fr,360px] gap-6" : ""}>
        <div className="min-w-0">
          {matches.isLoading ? (
            <Card>
              <CardContent className="pt-6 text-sm text-muted-foreground text-center">
                Loading matches…
              </CardContent>
            </Card>
          ) : (
            <MatchTable matches={filtered} />
          )}
        </div>

        {selectedUrl && (
          // Single scroll container — `overflow-y-auto` lives here so
          // the WHOLE detail panel scrolls (header + score pills + JD +
          // fit/gap + ghost reasons) inside the viewport-bounded box.
          <div className="md:sticky md:top-24 md:self-start md:max-h-[calc(100vh-7rem)] overflow-y-auto">
            <MatchDetail matches={filtered} />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Compact filter row at the top of the table. Each control reads/writes
 * the Zustand UI store directly — no local React state, no prop chains.
 */
function FilterBar() {
  const filters = useUIStore((s) => s.matchFilters);
  const set = useUIStore((s) => s.setMatchFilter);
  return (
    <div className="flex items-center gap-3 text-sm">
      <label className="flex items-center gap-1.5 cursor-pointer">
        <input
          type="checkbox"
          checked={filters.starredOnly}
          onChange={(e) => set("starredOnly", e.target.checked)}
          className="accent-accent"
        />
        <span className="text-muted-foreground">Starred only</span>
      </label>
      <label className="flex items-center gap-1.5 cursor-pointer">
        <input
          type="checkbox"
          checked={filters.unseenOnly}
          onChange={(e) => set("unseenOnly", e.target.checked)}
          className="accent-accent"
        />
        <span className="text-muted-foreground">Unseen only</span>
      </label>
      <label className="flex items-center gap-1.5 cursor-pointer">
        <input
          type="checkbox"
          checked={filters.showDismissed}
          onChange={(e) => set("showDismissed", e.target.checked)}
          className="accent-accent"
        />
        <span className="text-muted-foreground">Show dismissed</span>
      </label>
      <label
        className="flex items-center gap-1.5 cursor-pointer"
        title="Drop Remote-UK / Remote-Canada / Remote-EU style postings that almost always require local work authorisation. Toggle off if you have multi-country auth or are open to relocating."
      >
        <input
          type="checkbox"
          checked={filters.hideForeignRemote}
          onChange={(e) => set("hideForeignRemote", e.target.checked)}
          className="accent-accent"
        />
        <span className="text-muted-foreground">US-only remote</span>
      </label>
    </div>
  );
}
